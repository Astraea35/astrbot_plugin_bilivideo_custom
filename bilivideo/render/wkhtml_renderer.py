"""Markdown to image renderer backed by Playwright async API."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path

from ..core.exceptions import PartialRenderError, RenderError
from ..core.logging import get_logger
from .pagination import split_by_chapters
from .templates import build_full_html, extract_title, highlight_timestamps, sanitize_html, wrap_chapters_in_cards

logger = get_logger("BiliVideo/Render")


class WkHtmlRenderer:
    """Render Markdown into one or more images without blocking asyncio."""

    def __init__(self, *, output_dir: str | Path, image_width: int = 1400, services: object = None) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._image_width = image_width
        self._services = services
        self._lock = asyncio.Lock()

    @property
    def available_backends(self) -> list[str]:
        return ["playwright"]

    @property
    def backend_diagnostics(self) -> dict[str, str]:
        return {"playwright": "ready (async)"}

    async def render(self, markdown_text: str, *, base_filename: str,
                     max_cards_per_image: int = 6, enable_split: bool = True) -> list[Path]:
        self._cleanup_old_cache()
        chapter_count = sum(line.startswith("## ") for line in markdown_text.splitlines())
        if not enable_split or chapter_count <= max_cards_per_image:
            return await self._render_single(markdown_text, base_filename)
        pages = split_by_chapters(markdown_text, max_cards=max_cards_per_image)
        if len(pages) == 1:
            return await self._render_single(pages[0], base_filename)

        outputs, failed_pages, page_errors = [], [], {}
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for idx, page_md in enumerate(pages, 1):
            destination = self._output_dir / f"{base_filename}_p{idx}.png"
            try:
                await self._render_html_to_png(
                    page_md, destination,
                    footer_time=f"{now_str} | 第 {idx}/{len(pages)} 页",
                    page_label=f"(第 {idx}/{len(pages)} 页)",
                )
            except RenderError as exc:
                logger.warning(f"page {idx}/{len(pages)} failed: {exc}")
                failed_pages.append(idx)
                page_errors[idx] = str(exc)
            else:
                outputs.append(destination)
        if failed_pages and outputs:
            raise PartialRenderError(
                f"partial render failed; failed_pages={failed_pages}",
                generated_paths=outputs, failed_pages=failed_pages, page_errors=page_errors,
            )
        if not outputs:
            raise RenderError("all pages failed to render")
        return outputs

    async def _render_single(self, markdown_text: str, base_filename: str) -> list[Path]:
        destination = self._output_dir / f"{base_filename}.png"
        await self._render_html_to_png(
            markdown_text, destination, footer_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        return [destination]

    async def _render_html_to_png(self, markdown_text: str, destination: Path, *,
                                  footer_time: str, page_label: str | None = None) -> None:
        try:
            import markdown as md
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RenderError(f"缺少关键依赖: {exc}") from exc

        html_body = md.markdown(markdown_text, extensions=["tables", "fenced_code", "nl2br"])
        html_body = sanitize_html(html_body)
        html_body = highlight_timestamps(html_body)
        title_text, html_body = extract_title(html_body)
        if page_label:
            title_text = f"{title_text} {page_label}"
        html_body = wrap_chapters_in_cards(html_body)

        # 从 PluginConfig 读取渲染参数（现在所有字段均已定义）
        theme, img_width, img_fmt, img_quality, scale = "light", self._image_width, "png", 100, 2
        base_font_size = 32
        if self._services and hasattr(self._services, "config"):
            cfg = self._services.config
            theme = str(getattr(cfg, "theme", theme)).lower()
            img_width = int(getattr(cfg, "image_width", img_width))
            img_fmt = str(getattr(cfg, "image_output_format", img_fmt)).lower()
            img_quality = int(getattr(cfg, "image_quality", img_quality))
            scale = int(getattr(cfg, "image_scale_factor", scale))
            base_font_size = int(getattr(cfg, "image_font_size", base_font_size))

        full_html = build_full_html(html_body, title_text=title_text, footer_time=footer_time,
                                    width=img_width, theme=theme)
        css = f"""<style>
html,body{{padding-bottom:100px!important;overflow:visible!important;height:auto!important;min-height:100vh}}
.markdown-body{{overflow:visible!important;height:auto!important}}
body,.markdown-body{{font-size:{base_font_size}px!important;line-height:1.7!important}}
h1,.title,.main-title{{font-size:{int(base_font_size*1.8)}px!important}}
h2,.section-title{{font-size:{int(base_font_size*1.4)}px!important}}
h3,.card-title{{font-size:{int(base_font_size*1.15)}px!important}}
p,li,td,th,blockquote{{font-size:{base_font_size}px!important}}
code,pre,kbd,samp{{font-size:{int(base_font_size*.85)}px!important}}
.card,.box,.item,.chapter-box{{padding:{int(base_font_size*1.3)}px!important;margin-bottom:{base_font_size}px!important}}
</style>"""
        full_html = full_html.replace("</head>", css + "</head>") if "</head>" in full_html else css + full_html
        model = self._get_current_model()
        footer_left = f"🤖 {model} &nbsp;·&nbsp; Powered by <span>biliVideo</span>"
        if '<div class="ftxt">' in full_html:
            full_html = re.sub(r'<div class="ftxt">.*?</div>', f'<div class="ftxt">{footer_left}</div>', full_html, flags=re.S)

        destination.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        browser = None
        try:
            async with self._lock:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    page = await browser.new_page(device_scale_factor=scale, viewport={"width": img_width, "height": 100})
                    await page.set_content(full_html, wait_until="networkidle")
                    await page.wait_for_timeout(1000)
                    save_fmt = "jpeg" if img_fmt in ("jpg", "jpeg", "webp") else "png"
                    kwargs = {"type": save_fmt, "full_page": True}
                    if save_fmt == "jpeg":
                        kwargs["quality"] = img_quality
                    await page.screenshot(path=str(destination), **kwargs)
                    await page.close()
                    await browser.close()
                    browser = None
        except Exception as exc:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            raise RenderError(f"Playwright 异步长图截图失败: {exc}") from exc

        if not destination.exists() or destination.stat().st_size <= 0:
            raise RenderError("Playwright 未生成有效图片文件")
        logger.info(f"rendered via Playwright (Async): {destination.name} ({destination.stat().st_size} bytes, {time.monotonic()-started:.2f}s)")

    def _get_current_model(self) -> str:
        llm = getattr(self._services, "llm", None) if self._services else None
        if llm:
            model = getattr(llm, "_model", None) or getattr(llm, "provider_id", None)
            if model:
                return str(model)
        raw_cfg = getattr(self._services, "raw_config", {}) if self._services else {}
        llm_cfg = raw_cfg.get("llm", {}) if isinstance(raw_cfg, dict) else {}
        if isinstance(llm_cfg, dict):
            return str(llm_cfg.get("llm_model") or llm_cfg.get("astrbot_provider_id") or "AstrBot 默认模型")
        return "AstrBot 默认模型"

    def _cleanup_old_cache(self) -> None:
        try:
            now = time.time()
            for pattern in ("*.png", "*.jpg", "*.jpeg"):
                for file in self._output_dir.glob(pattern):
                    if file.is_file() and now - file.stat().st_mtime > 86400:
                        file.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"自动清理旧缓存异常: {exc}")

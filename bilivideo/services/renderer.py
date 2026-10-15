"""Dynamic card renderer using Jinja2 templates and HTML-to-Image / Playwright."""

from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader

from ..core.constants import CARD_TEMPLATES, DEFAULT_TEMPLATE, get_template_path
from ..core.logging import get_logger
from ..core.models import RenderPayload

logger = get_logger("BiliVideo/DynamicRenderer")


def resolve_font_uri(font_path: str) -> str:
    """如果配置了有效的自定义字体路径，将其转为 Base64 URI 供 HTML 使用；否则返回空"""
    if font_path and os.path.isfile(font_path):
        try:
            with open(font_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            ext = os.path.splitext(font_path)[1].lower()
            mime = "font/otf" if ext in (".otf", ".subset.otf") else "font/ttf"
            return f"data:{mime};base64,{encoded}"
        except Exception as e:
            logger.warning(f"加载自定义字体失败 [{font_path}]: {e}")
    return ""


class DynamicCardRenderer:
    """动态卡片 HTML / 图片渲染器"""

    def __init__(self, services: Any, style: str = DEFAULT_TEMPLATE) -> None:
        self.services = services
        self.style = style

    def render_html(self, payload: RenderPayload | Dict[str, Any], style: Optional[str] = None) -> str:
        """根据 RenderPayload 渲染 HTML 字符串"""
        if isinstance(payload, dict):
            payload = RenderPayload.from_dict(payload)

        use_style = style or self.style
        template_path = get_template_path(use_style)

        if not os.path.exists(template_path):
            logger.warning(f"模板文件不存在: {template_path}，回退默认模板 {DEFAULT_TEMPLATE}")
            template_path = get_template_path(DEFAULT_TEMPLATE)

        template_dir = os.path.dirname(template_path)
        template_filename = os.path.basename(template_path)

        env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
        template = env.get_template(template_filename)

        # 构建渲染上下文
        context = payload.to_template_context()
        
        # 注入自定义字体 URI（若未填写或文件不存在则返回空字符串，回退系统默认字体）
        custom_font_path = getattr(self.services.config, "custom_font_path", "")
        context["font_uri"] = resolve_font_uri(custom_font_path)

        return template.render(**context)

    async def render_dynamic(
        self, payload: RenderPayload | Dict[str, Any], style: Optional[str] = None
    ) -> Optional[str]:
        """渲染动态卡片：生成 HTML 并由 Playwright 截长图，返回渲染好的图片文件路径字符串"""
        if isinstance(payload, dict):
            payload = RenderPayload.from_dict(payload)

        try:
            html_content = self.render_html(payload, style=style)
        except Exception as e:
            logger.error(f"动态 HTML 模版编译失败: {e}", exc_info=True)
            return None

        cache_dir = Path("data/bili_video_cache").resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_file = cache_dir / f"dyn_{payload.uid or '0'}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}.png"

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 680, "height": 100}, device_scale_factor=2)
                await page.set_content(html_content, wait_until="networkidle")
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(output_file), full_page=True)
                await page.close()
                await browser.close()

            if output_file.exists() and output_file.stat().st_size > 0:
                logger.info(f"动态卡片图片渲染成功: {output_file.name}")
                return str(output_file)
        except Exception as e:
            logger.error(f"Playwright 动态长图渲染失败: {e}", exc_info=True)

        return None

"""Pillow-based fallback renderer.

When wkhtmltopdf isn't installed (Debian 13 dropped the package, Docker
containers without xvfb, etc.) we still want image output. Pillow is a
much smaller dep and ships with most Python installs, so we use it to
render a simple card-style image — visually less rich than the HTML
version, but still readable and unifrom.

Notable simplifications vs. the HTML renderer:
  * No background blur, gradients, or radial glows
  * Uses the best font we can discover. System CJK fonts are preferred;
    if none is available Pillow's built-in default is used so rendering
    never fails.
  * No code blocks / tables (rendered as plain monospaced lines)
  * Uses solid color cards with a left accent strip
"""

from __future__ import annotations

import os
import re
import time  # ← 补回缺失的导入
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.exceptions import PartialRenderError, RenderError
from ..core.logging import get_logger
from .pagination import split_by_chapters
from .theme import card_color_for

if TYPE_CHECKING:  # pragma: no cover
    from PIL.ImageFont import FreeTypeFont
    # 循环导入修复：仅在类型检查时导入，运行时不会加载
    from ..services import BiliVideoServices

logger = get_logger("BiliVideo/PillowRender")


# ──────────────────────── font discovery ────────────────────────

# System font paths, tried in order. On Windows / macOS / Linux the
# appropriate CJK font is discovered automatically; no bundled fonts
# are required.
_CJK_FONT_CANDIDATES: tuple[str, ...] = (
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\yahei.ttf",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/simsun.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)
_FALLBACK_FONT_CANDIDATES: tuple[str, ...] = (
    # A fallback Latin font for non-CJK environments.
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/mnt/c/Windows/Fonts/arial.ttf",
)


def _find_cjk_font() -> str | None:
    for candidate in _CJK_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _find_fallback_font() -> str | None:
    for candidate in _FALLBACK_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def check_pillow_ready() -> tuple[bool, str]:
    """Return whether Pillow can produce images in this environment.

    Never blocks image output. A system CJK font is preferred; failing
    that a Latin fallback is tried; finally Pillow's built-in default is
    used so rendering always proceeds.
    """

    try:
        from PIL import ImageFont
    except ImportError as exc:
        return False, f"Pillow not installed: {exc}"

    font_path = _find_cjk_font()
    if font_path is not None:
        try:
            ImageFont.truetype(font_path, 14)
        except Exception as exc:
            return False, f"CJK font cannot be loaded: {font_path} ({exc})"
        return True, f"font={font_path}"

    fallback_path = _find_fallback_font()
    if fallback_path is None:
        return True, "ready with Pillow default font; no CJK font discovered"
    try:
        ImageFont.truetype(fallback_path, 14)
    except Exception as exc:
        return False, f"fallback font cannot be loaded: {fallback_path} ({exc})"
    return True, f"fallback_font={fallback_path}; no CJK font discovered"


def _load_font(size: int):
    from PIL import ImageFont

    font_path = _find_cjk_font() or _find_fallback_font()
    if font_path is not None:
        try:
            return ImageFont.truetype(font_path, size), font_path
        except Exception as exc:
            logger.warning(f"font load failed ({font_path}): {exc}; using Pillow default")
    return ImageFont.load_default(), "Pillow default"


# Codepoints the bundled GB2312 Noto subset (and most single CJK fonts Pillow
# loads) cannot draw, so they would appear as .notdef "tofu" boxes. Pillow does
# no cross-font glyph fallback, so we drop them up front in the Pillow path; the
# richer wkhtml/HTML path keeps them.
_UNSUPPORTED_GLYPHS_RE = re.compile(
    "["
    "\U0001f000-\U0001ffff"  # emoji + supplemental symbols & pictographs
    "\U00002600-\U000027bf"  # miscellaneous symbols + dingbats (✅✨🔥…)
    "\U00002b00-\U00002bff"  # misc symbols & arrows (★ ⬆ …)
    "\U00002300-\U000023ff"  # misc technical (⏱ ⌚ ⏰ …)
    "\U0000fe00-\U0000fe0f"  # emoji variation selectors
    "‍"  # zero-width joiner
    "]"
)


def _strip_unsupported_glyphs(text: str) -> str:
    """Remove emoji / symbol codepoints a single CJK font can't render."""

    if not text:
        return text
    cleaned = _UNSUPPORTED_GLYPHS_RE.sub("", text)
    if cleaned == text:
        return text
    # collapse the spaces left where a stripped emoji used to sit
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


# Leading timestamp token. After glyph stripping the ⏱ marker is gone but the
# digits remain: "12:34 …", "[12:34] …", "1:02:03 …". Highlighted in cards.
_TS_RE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*")


def _line_advance(font: object) -> int:
    """Vertical advance per line — generous leading for CJK legibility."""

    size = int(getattr(font, "size", 16))
    return max(size + 6, round(size * 1.55))


# ──────────────────────── markdown parsing ────────────────────────


@dataclass(slots=True)
class _Block:
    kind: str  # "h1" | "h2" | "h3" | "p" | "li"
    text: str


def _parse_markdown_blocks(markdown_text: str) -> list[_Block]:
    """Very small markdown-ish tokenizer producing block-level elements."""

    out: list[_Block] = []
    in_code = False
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(_Block("p", line))
            continue
        if not line.strip():
            continue
        if line.startswith("# "):
            out.append(_Block("h1", line[2:].strip()))
        elif line.startswith("## "):
            out.append(_Block("h2", line[3:].strip()))
        elif line.startswith("### "):
            out.append(_Block("h3", line[4:].strip()))
        elif line.startswith(("- ", "* ", "+ ")):
            out.append(_Block("li", line[2:].strip()))
        elif re.match(r"^\d+\.\s", line):
            out.append(_Block("li", re.sub(r"^\d+\.\s", "", line).strip()))
        elif line.startswith("> "):
            out.append(_Block("p", " " + line[2:].strip() + " "))
        else:
            # strip Markdown emphasis for plain rendering
            cleaned = re.sub(r"\*\*?(.+?)\*\*?", r"\1", line.strip())
            cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
            out.append(_Block("p", cleaned))
    stripped: list[_Block] = []
    for block in out:
        text = _strip_unsupported_glyphs(block.text)
        if text:
            stripped.append(_Block(block.kind, text))
    return stripped


# ──────────────────────── renderer ────────────────────────


class PillowRenderer:
    """Render Markdown into PNG cards using only Pillow.

    Layout: dark background, single column, one card per `## chapter`
    section, with a left accent stripe color-cycled through the same
    palette as the wkhtmltopdf renderer.
    """

    BG = (26, 27, 46)
    CARD_BG = (30, 33, 64)
    TITLE_FG = (241, 245, 249)
    TEXT_FG = (201, 206, 220)
    ACCENT_FG = (147, 197, 253)
    TS_FG = (251, 146, 60)
    DIM_FG = (148, 163, 184)
    TITLE_SIZE = 28
    H2_SIZE = 20
    H3_SIZE = 18
    BODY_SIZE = 16
    PADDING = 40
    CARD_PADDING = 26
    CARD_GAP = 22
    STRIPE_W = 6
    BULLET_INDENT = 24
    SUBSECTION_INDENT = 16
    FOOTER_H = 56

    def __init__(self, *, output_dir: str | Path, image_width: int = 1400) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._width = image_width

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def render(
        self,
        markdown_text: str,
        *,
        base_filename: str,
        max_cards_per_image: int = 6,
        enable_split: bool = True,
    ) -> list[Path]:
        chapter_count = sum(1 for line in markdown_text.splitlines() if line.startswith("## "))
        if not enable_split or chapter_count <= max_cards_per_image:
            return self._render_one(markdown_text, base_filename, page_label=None, total=1)

        pages = split_by_chapters(markdown_text, max_cards=max_cards_per_image)
        outputs: list[Path] = []
        failed_pages: list[int] = []
        page_errors: dict[int, str] = {}
        total = len(pages)
        for idx, page in enumerate(pages, start=1):
            label = None if total == 1 else f"({idx}/{total})"
            try:
                outputs.extend(
                    self._render_one(page, f"{base_filename}_p{idx}", page_label=label, total=total)
                )
            except RenderError as exc:
                logger.warning(
                    f"page {idx}/{total} pillow render failed: {exc}; "
                    f"page_chars={len(page)} chapters={page.count(chr(10) + '## ')}"
                )
                failed_pages.append(idx)
                page_errors[idx] = str(exc)
        if failed_pages and outputs:
            raise PartialRenderError(
                f"partial pillow render failed; failed_pages={failed_pages}, "
                f"succeeded_pages={[p.name for p in outputs]}",
                generated_paths=outputs,
                failed_pages=failed_pages,
                page_errors=page_errors,
            )
        if not outputs:
            raise RenderError("all pages failed to render with Pillow")
        return outputs

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _render_one(
        self,
        markdown_text: str,
        base_filename: str,
        *,
        page_label: str | None,
        total: int,
    ) -> list[Path]:
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RenderError(f"Pillow not installed: {exc}") from exc

        blocks = _parse_markdown_blocks(markdown_text)
        if not blocks:
            blocks.append(_Block("p", "(空内容)"))

        title_block = next((b for b in blocks if b.kind == "h1"), None)
        body_blocks = [b for b in blocks if b is not title_block]
        title_text = title_block.text if title_block else "AI 视频总结"
        if page_label:
            title_text = f"{title_text} {page_label}"

        f_title, font_path = _load_font(self.TITLE_SIZE)
        f_h2, _ = _load_font(self.H2_SIZE)
        f_h3, _ = _load_font(self.H3_SIZE)
        f_body, _ = _load_font(self.BODY_SIZE)

        content_w = self._width - self.PADDING * 2
        card_inner_w = content_w - self.CARD_PADDING * 2
        title_adv = _line_advance(f_title)
        head_adv = _line_advance(f_h2)
        h3_adv = _line_advance(f_h3)
        body_adv = _line_advance(f_body)

        # Header: wrap the (often long) title so it can never clip off the edge.
        title_lines = self._wrap(title_text, font=f_title, max_width=content_w)
        header_h = self.PADDING + len(title_lines) * title_adv + 24

        # Build the full layout once; every line is wrapped with the font it is
        # painted in, so the measured height and the painted content stay in
        # lock-step (no vertical clipping) and nothing overruns a card edge.
        layout: list[dict] = []
        chapter_no = 0
        for idx, (heading, blocks_) in enumerate(self._group_into_cards(body_blocks)):
            if heading is not None:
                chapter_no += 1
                badge = f"{chapter_no:02d}"
                badge_w = self._text_w(f_h2, badge + "  ")
                head_lines = self._wrap(
                    heading, font=f_h2, max_width=max(1, card_inner_w - badge_w)
                )
            else:
                badge, badge_w, head_lines = "", 0, []

            items: list[dict] = []
            under_h3 = False
            for b in blocks_:
                if b.kind == "h3":
                    under_h3 = True
                    items.append(
                        {
                            "kind": "h3",
                            "lines": self._wrap(b.text, font=f_h3, max_width=card_inner_w),
                            "indent": 0,
                            "adv": h3_adv,
                            "font": f_h3,
                            "color": self.ACCENT_FG,
                        }
                    )
                    continue
                indent = self.SUBSECTION_INDENT if under_h3 else 0
                if b.kind == "li":
                    avail = max(1, card_inner_w - indent - self.BULLET_INDENT)
                    items.append(
                        {
                            "kind": "li",
                            "lines": self._wrap(b.text, font=f_body, max_width=avail),
                            "indent": indent,
                            "adv": body_adv,
                            "font": f_body,
                            "color": self.TEXT_FG,
                        }
                    )
                else:
                    items.append(
                        {
                            "kind": "p",
                            "lines": self._wrap(
                                b.text, font=f_body, max_width=max(1, card_inner_w - indent)
                            ),
                            "indent": indent,
                            "adv": body_adv,
                            "font": f_body,
                            "color": self.TEXT_FG,
                        }
                    )

            card_h = self.CARD_PADDING * 2
            if head_lines:
                card_h += len(head_lines) * head_adv + 10
            for it in items:
                card_h += len(it["lines"]) * it["adv"] + self.BLOCK_GAP
            layout.append(
                {
                    "idx": idx,
                    "head_lines": head_lines,
                    "badge": badge,
                    "badge_w": badge_w,
                    "items": items,
                    "card_h": card_h,
                }
            )

        total_h = header_h + sum(c["card_h"] + self.CARD_GAP for c in layout) + self.FOOTER_H
        logger.debug(
            f"pillow page layout: chars={len(markdown_text)} cards={len(layout)} "
            f"height={total_h} width={self._width} font={font_path}"
        )

        img = Image.new("RGB", (self._width, total_h), self.BG)
        draw = ImageDraw.Draw(img)

        # Header (multi-line title + width-matched accent underline)
        ty = self.PADDING
        for line in title_lines:
            draw.text((self.PADDING, ty), line, fill=self.TITLE_FG, font=f_title)
            ty += title_adv
        underline_w = (
            min(content_w, max(120, self._text_w(f_title, title_lines[0])))
            if title_lines
            else 120
        )
        draw.line((self.PADDING, ty + 4, self.PADDING + underline_w, ty + 4),
                  fill=self.ACCENT_FG, width=4)

        y = header_h
        for card in layout:
            border, _ = card_color_for(card["idx"])
            border_rgb = self._hex_to_rgb(border)
            card_h = card["card_h"]
            card_x0 = self.PADDING
            card_x1 = self._width - self.PADDING
            draw.rounded_rectangle((card_x0, y, card_x1, y + card_h), radius=14, fill=self.CARD_BG)
            draw.rectangle((card_x0, y, card_x0 + self.STRIPE_W, y + card_h), fill=border_rgb)

            cx = card_x0 + self.CARD_PADDING
            cy = y + self.CARD_PADDING
            head_lines = card["head_lines"]
            if head_lines:
                draw.text((cx, cy), card["badge"], fill=border_rgb, font=f_h2)
                for i, hl in enumerate(head_lines):
                    hx = cx + card["badge_w"] if i == 0 else cx
                    draw.text((hx, cy), hl, fill=self.TITLE_FG, font=f_h2)
                    cy += head_adv
                cy += 10

            for it in card["items"]:
                lx = cx + it["indent"]
                font, color, adv = it["font"], it["color"], it["adv"]
                if it["kind"] == "li":
                    draw.text((lx, cy), "•", fill=self.ACCENT_FG, font=font)
                    tx = lx + self.BULLET_INDENT
                    for i, line in enumerate(it["lines"]):
                        self._draw_line(draw, tx, cy, line, font=font, color=color, first=(i == 0))
                        cy += adv
                else:
                    for i, line in enumerate(it["lines"]):
                        self._draw_line(draw, lx, cy, line, font=font, color=color, first=(i == 0))
                        cy += adv
                cy += self.BLOCK_GAP

            y += card_h + self.CARD_GAP

        footer_text = (
            f"Powered by biliVideo · AI 视频总结 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if total > 1 and page_label:
            footer_text += f" · {page_label}"
        draw.text((self.PADDING, total_h - 32), footer_text, fill=self.DIM_FG, font=f_body)

        out = self._output_dir / f"{base_filename}.png"
        try:
            img.save(out, "PNG", optimize=True)
        except OSError as exc:
            raise RenderError(f"Pillow save failed: {exc}") from exc
        logger.info(f"pillow rendered {out.name} ({out.stat().st_size} bytes)")
        return [out]

    @staticmethod
    def _group_into_cards(
        blocks: Sequence[_Block],
    ) -> list[tuple[str | None, list[_Block]]]:
        cards: list[tuple[str | None, list[_Block]]] = []
        current: list[_Block] = []
        current_heading: str | None = None
        for b in blocks:
            if b.kind == "h2":
                if current_heading is not None or current:
                    cards.append((current_heading, current))
                current = []
                current_heading = b.text
            else:
                current.append(b)
        cards.append((current_heading, current))
        return cards

    @staticmethod
    def _wrap(
        text: str,
        *,
        font: FreeTypeFont,
        max_width: int,
    ) -> list[str]:
        """Break a string into wrapped lines without splitting CJK characters."""

        if not text:
            return [""]

        lines: list[str] = []
        current = ""
        for ch in text:
            test = current + ch
            try:
                bbox = font.getbbox(test)
                width = bbox[2] - bbox[0]
            except Exception:
                width = len(test) * (font.size // 2)
            if width <= max_width:
                current = test
                continue
            if current:
                lines.append(current)
            current = ch
        if current:
            lines.append(current)
        return lines or [""]

    @staticmethod
    def _text_w(font: FreeTypeFont, text: str) -> int:
        """Pixel width of `text` in `font`, robust to font backend quirks."""

        try:
            return int(font.getlength(text))
        except Exception:
            try:
                bbox = font.getbbox(text)
                return bbox[2] - bbox[0]
            except Exception:
                return len(text) * (int(getattr(font, "size", 16)) // 2)

    @classmethod
    def _draw_line(
        cls, draw: object, x: int, y: int, text: str, *, font: object, color, first: bool
    ) -> None:
        """Draw one wrapped line, accent-coloring a leading timestamp token."""

        if first and text:
            match = _TS_RE.match(text)
            if match:
                token = text[: match.end()]
                draw.text((x, y), token, fill=cls.TS_FG, font=font)
                try:
                    advance = draw.textlength(token, font=font)
                except Exception:
                    advance = cls._text_w(font, token)
                draw.text((x + advance, y), text[match.end() :], fill=color, font=font)
                return
        draw.text((x, y), text, fill=color, font=font)

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
        s = hex_str.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in s):
            return PillowRenderer.ACCENT_FG
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# ════════════════════════════════════════════════════════════════
#  🆕 新增：订阅列表专用 Pillow 绘图函数（原 _pillow_renderer.py）
# ════════════════════════════════════════════════════════════════

from PIL import Image as PILImage, ImageDraw, ImageFont


def draw_subscription_image(services: 'BiliVideoServices', subs: list, output_path: Path):
    """Pillow 本地轻量化引擎：自适应测宽换行，彻底根除字数溢出与方框乱码"""
    scale = _resolve_plugin_scale(services)
    def S(v): return int(v * scale)

    width, top_banner_h, card_margin_top, table_header_h, row_h, note_box_h = S(800), S(70), S(25), S(45), S(96), S(60)

    current_y = top_banner_h + card_margin_top + S(25) + S(30) + table_header_h + S(5)
    for sub in subs:
        current_y += row_h

    current_y += S(15) + S(20) + S(35) + S(20) + S(20) + (5 * S(28)) + S(35)
    note_box_top_y = current_y
    card_y2 = current_y + note_box_h + S(30)
    total_height = card_y2 + S(80)

    img = PILImage.new("RGB", (width, total_height), "#F8FAFC")
    draw = ImageDraw.Draw(img)
    font_file = _resolve_plugin_font(services)

    try:
        f_top = ImageFont.truetype(str(font_file), S(22))
        f_title = ImageFont.truetype(str(font_file), S(20))
        f_th = ImageFont.truetype(str(font_file), S(15))
        f_bold = ImageFont.truetype(str(font_file), S(16))
        f_norm = ImageFont.truetype(str(font_file), S(15))
        f_sub = ImageFont.truetype(str(font_file), S(13))
        f_filter = ImageFont.truetype(str(font_file), S(12))
        f_foot = ImageFont.truetype(str(font_file), S(12))
    except Exception:
        f_top = f_title = f_th = f_bold = f_norm = f_sub = f_filter = f_foot = ImageFont.load_default()

    draw.rectangle([0, 0, width, top_banner_h], fill="#FFFFFF")
    draw.text((width // 2, top_banner_h // 2 - S(2)), "AI 视频总结", fill="#16A34A", font=f_top, anchor="mm")
    draw.line([(0, top_banner_h), (width, top_banner_h)], fill="#E2E8F0", width=S(1))

    card_x1, card_x2 = S(40), width - S(40)
    draw.rounded_rectangle([card_x1, top_banner_h + card_margin_top, card_x2, card_y2], radius=S(12), fill="#FFFFFF", outline="#E2E8F0", width=S(1))
    draw.rounded_rectangle([card_x1, top_banner_h + card_margin_top, card_x1 + S(6), card_y2], radius=S(12), fill="#16A34A")
    draw.rectangle([card_x1 + S(4), top_banner_h + card_margin_top, card_x1 + S(6), card_y2], fill="#16A34A")

    print_y = top_banner_h + card_margin_top + S(25)
    _draw_custom_icon(draw, "clipboard", card_x1 + S(35), print_y - S(1), size=S(20))
    draw.text((card_x1 + S(35) + S(26), print_y), "biliVideo 侦听订阅列表", fill="#1E293B", font=f_title)

    print_y += S(45)
    th_bg_x1, th_bg_x2 = card_x1 + S(25), card_x2 - S(25)
    draw.rounded_rectangle([th_bg_x1, print_y, th_bg_x2, print_y + table_header_h], radius=S(6), fill="#F1F5F9")
    draw.text((th_bg_x1 + S(30), print_y + S(13)), "序号", fill="#475569", font=f_th)
    draw.text((th_bg_x1 + S(110), print_y + S(13)), "监测目标名称", fill="#475569", font=f_th)
    draw.text((th_bg_x2 - S(240), print_y + S(13)), "当前推送策略与订阅范围详情", fill="#475569", font=f_th)

    print_y += table_header_h + S(5)
    max_subs = getattr(services.config, "max_subscriptions", 100)

    # 直接使用中文类型名，不映射
    for i, up in enumerate(subs, start=1):
        up_name = getattr(up, "name", "未知博主")
        up_mid = getattr(up, "mid", "0")
        auto_summary = getattr(up, "auto_summary", False)
        # 使用 sub_types（白名单）
        sub_types = getattr(up, "sub_types", ["视频"])
        if isinstance(sub_types, str):
            sub_types = [sub_types]
        filter_regex = getattr(up, "filter_regex", [])
        live_atall = getattr(up, "live_atall", False)

        draw.text((th_bg_x1 + S(38), print_y + S(22)), str(i), fill="#1E293B", font=f_norm)

        draw.text((th_bg_x1 + S(110), print_y + S(14)), up_name, fill="#16A34A", font=f_bold)
        draw.text((th_bg_x1 + S(110), print_y + S(38)), f"(UID: {up_mid})", fill="#64748B", font=f_sub)

        st_x = th_bg_x2 - S(240)
        if "视频" in sub_types and auto_summary:
            _draw_custom_icon(draw, "robot", st_x, print_y + S(14), size=S(18))
            draw.text((st_x + S(26), print_y + S(15)), "视频自动总结", fill="#1E293B", font=f_norm)
        elif "视频" in sub_types:
            _draw_custom_icon(draw, "bell", st_x, print_y + S(14), size=S(18))
            draw.text((st_x + S(26), print_y + S(15)), "仅推视频提醒", fill="#1E293B", font=f_norm)
        else:
            _draw_custom_icon(draw, "bell", st_x, print_y + S(14), size=S(18))
            draw.text((st_x + S(26), print_y + S(15)), "动态摘要按全局配置", fill="#1E293B", font=f_norm)

        # 类型显示：全部展示 + 图标（修改处）
        type_icons = {
            "视频": "📺",
            "动态": "📰",
            "图文": "🖼️",
            "转发": "🔄",
            "专栏": "📝",
            "直播": "🔴",
            "抽奖": "🎁",
            "转发抽奖": "🎟️",
        }
        type_parts = [f"{type_icons.get(t, '')}{t}" for t in sub_types]
        type_display = "、".join(type_parts) if type_parts else "未设置"
        if filter_regex:
            type_display += f" | 过滤: {','.join(filter_regex)}"
        if live_atall:
            type_display += " | @全体"

        # 自适应换行（使用 f_filter）
        words = list(type_display)
        lines = []
        current_line = ""
        max_allowed_w = S(230)
        for char in words:
            test_line = current_line + char
            w = draw.textlength(test_line, font=f_filter)
            if w <= max_allowed_w:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)

        text_y = print_y + S(42)
        for line in lines:
            draw.text((st_x, text_y), line, fill="#8B5CF6", font=f_filter)
            text_y += S(15)

        print_y += row_h
        draw.line([(th_bg_x1 + S(10), print_y), (th_bg_x2 - S(10), print_y)], fill="#E2E8F0", width=S(1))

    print_y += S(15)
    _draw_custom_icon(draw, "chart", th_bg_x1 + S(10), print_y + S(1), size=S(16))
    draw.text((th_bg_x1 + S(10) + S(24), print_y), f"当前共计管理 {len(subs)} / {max_subs} 个监测目标", fill="#475569", font=f_norm)

    print_y += S(35)
    draw.line([(th_bg_x1, print_y), (th_bg_x2, print_y)], fill="#E2E8F0", width=S(1))

    # ================= 常用指令 =================
    print_y += S(20)
    _draw_custom_icon(draw, "lightbulb", th_bg_x1 + S(10), print_y + S(1), size=S(16))
    draw.text((th_bg_x1 + S(10) + S(24), print_y), "常用指令：", fill="#16A34A", font=f_bold)

    actions = [
        ("chart", "查看/检查订阅：", "/订阅列表 /检查更新 ", "/订阅列表"),
        ("robot", "订阅视频总结：", "/订阅 UID [类型] 开/关 ", "/订阅 123 关"),
        ("cross", "取消订阅：", "/取消订阅 序号1 [类型] ", "/取消订阅 序号1 视频"),
        ("play", "查看UP主页：", "/主页视频 序号1 /访问主页 序号1 ", "/主页视频 序号1"),
        ("bell", "开播@全体提醒：", "/订阅开播@全体 UID 开/关 ", "/订阅开播@全体 123 开"),
    ]
    for icon, t1, t2, t3 in actions:
        print_y += S(28)
        _draw_custom_icon(draw, icon, th_bg_x1 + S(12), print_y + S(2), size=S(16))
        _draw_mixed_text(draw, th_bg_x1 + S(38), print_y, [
            (t1, f_bold, "#16A34A"), ("发送 ", f_norm, "#475569"), (t2, f_norm, "#64748B"),
            ("(例：", f_norm, "#475569"), (t3, f_norm, "#64748B"), (")", f_norm, "#475569")
        ])

    print_y += S(30)
    _draw_custom_icon(draw, "lightbulb", th_bg_x1 + S(12), print_y + S(2), size=S(16))
    draw.text((th_bg_x1 + S(38), print_y), "定位目标：纯数字默认 UID；数字昵称请使用 up名；列表操作可用 序号1。", fill="#475569", font=f_norm)

    draw.rounded_rectangle([th_bg_x1, note_box_top_y, th_bg_x2, note_box_top_y + note_box_h], radius=S(6), fill="#F8FAFC", outline="#E2E8F0", width=S(1))
    draw.rectangle([th_bg_x1, note_box_top_y, th_bg_x1 + S(4), note_box_top_y + note_box_h], fill="#6366F1")
    _draw_custom_icon(draw, "lightbulb", th_bg_x1 + S(20), note_box_top_y + S(22), size=S(16))
    draw.text((th_bg_x1 + S(20) + S(24), note_box_top_y + S(21)), "视频总结开关不影响动态 AI 摘要，动态摘要按全局配置运行。", fill="#EF4444", font=f_norm)

    # ================= 订阅规则补充说明 =================
    # 记录当前画布高度
    current_height = card_y2 + S(80)   # 原图底部已有时间戳
    # 预留底部空间以容纳三条规则和两行类型说明
    help_zone_height = S(260)
    new_height = current_height + help_zone_height

    # 创建延伸后的新画布（浅灰色背景，与网页版底色完美一致）
    extended_img = PILImage.new("RGB", (width, new_height), "#F4F5F7")
    extended_img.paste(img, (0, 0))
    draw_ext = ImageDraw.Draw(extended_img)

    # 起始高度与左边距对齐（S(40) 与上方卡片左边界完美垂直切齐）
    start_y = current_height + S(25)
    
    # 1. 绘制规则标题
    _draw_custom_icon(draw_ext, "lightbulb", S(40), start_y + S(2), size=S(16))
    draw_ext.text((S(40) + S(24), start_y), "订阅使用规则", fill="#18191C", font=f_bold)

    # 2. Line 1: 目标定位
    print_y_line = start_y + S(32)
    _draw_mixed_text(draw_ext, S(40), print_y_line, [
        ("• 目标定位：", f_bold, "#18191C"),
        ("纯数字默认 UID；数字昵称使用 up名；列表操作可用 序号1。", f_norm, "#6D757A")
    ])

    # 3. Line 2: 订阅格式
    print_y_line += S(26)
    _draw_mixed_text(draw_ext, S(40), print_y_line, [
        ("• 订阅格式：", f_bold, "#18191C"),
        ("/订阅 319785096 视频 动态 关；不写开关默认开启。", f_norm, "#6D757A")
    ])

    # 4. Line 3: 前缀兼容
    print_y_line += S(26)
    _draw_mixed_text(draw_ext, S(40), print_y_line, [
        ("• 前缀兼容：", f_bold, "#18191C"),
        ("uid: 前缀可省略，序号 1 中间空格也支持。", f_norm, "#6D757A")
    ])

    # ========== 订阅类型说明 ==========
    print_y_line += S(36)
    # 画一个浅色框
    draw_ext.rectangle([S(40), print_y_line - S(6), width - S(40), print_y_line + S(56)], fill="#F1F5F9", outline="#E2E8F0", width=1)
    _draw_custom_icon(draw_ext, "lightbulb", S(52), print_y_line + S(6), size=S(16))
    draw_ext.text((S(52) + S(24), print_y_line + S(6)), "📋 支持订阅类型：视频 动态 图文 转发 专栏", fill="#475569", font=f_norm)
    draw_ext.text((S(52) + S(24), print_y_line + S(30)), "直播 抽奖 转发抽奖", fill="#475569", font=f_norm)
    # =========================================

    # 最后保存延伸后的完整图片
    extended_img.save(output_path, format="PNG")
    logger.info(f"订阅列表图片已保存至 {output_path}")


def _resolve_plugin_font(services: 'BiliVideoServices') -> Path:
    """查找系统字体用于订阅列表图片绘制。"""
    # 先从系统字体查找
    system_fonts = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for sf in system_fonts:
        if os.path.exists(sf):
            return Path(sf)
    # 最后兜底：尝试从 pyinstaller 打包路径或 cwd 寻找
    return Path("SimHei.ttf")


def _resolve_plugin_scale(services: 'BiliVideoServices') -> int:
    for key in dir(services.config):
        if "scale" in key.lower() or "ratio" in key.lower():
            val = getattr(services.config, key)
            if isinstance(val, (int, float)) and val in [1, 2, 4]: return int(val)
    return 2


def _draw_custom_icon(draw, icon_type, x, y, size=16):
    if icon_type == "play":
        draw.rounded_rectangle([x, y, x + size, y + size], radius=max(2, size // 6), fill="#3B82F6")
        poly = [(x + size * 0.38, y + size * 0.25), (x + size * 0.38, y + size * 0.75), (x + size * 0.75, y + size * 0.5)]
        draw.polygon(poly, fill="#FFFFFF")
    elif icon_type == "cross":
        draw.rounded_rectangle([x, y, x + size, y + size], radius=max(2, size // 6), fill="#EF4444")
        r = size * 0.25
        cx, cy = x + size / 2, y + size / 2
        w = max(2, size // 8)
        draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill="#FFFFFF", width=w)
        draw.line([(cx + r, cy - r), (cx - r, cy + r)], fill="#FFFFFF", width=w)
    elif icon_type == "clipboard":
        draw.rounded_rectangle([x, y + size * 0.1, x + size * 0.9, y + size], radius=max(2, size // 8), fill="#D97706")
        draw.rectangle([x + size * 0.15, y + size * 0.25, x + size * 0.75, y + size * 0.9], fill="#FFFFFF")
        draw.line([(x + size * 0.25, y + size * 0.45), (x + size * 0.65, y + size * 0.45)], fill="#CBD5E1", width=max(1, size // 14))
        draw.line([(x + size * 0.25, y + size * 0.65), (x + size * 0.65, y + size * 0.65)], fill="#CBD5E1", width=max(1, size // 14))
        draw.rounded_rectangle([x + size * 0.28, y, x + size * 0.62, y + size * 0.2], radius=1, fill="#94A3B8")
    elif icon_type == "chart":
        draw.rectangle([x, y + size * 0.5, x + size * 0.22, y + size], fill="#22C55E")
        draw.rectangle([x + size * 0.33, y + size * 0.1, x + size * 0.55, y + size], fill="#EC4899")
        draw.rectangle([x + size * 0.66, y + size * 0.3, x + size * 0.88, y + size], fill="#3B82F6")
    elif icon_type == "lightbulb":
        draw.ellipse([x + size * 0.12, y, x + size * 0.88, y + size * 0.7], fill="#EAB308")
        draw.rectangle([x + size * 0.3, y + size * 0.7, x + size * 0.7, y + size * 0.9], fill="#94A3B8")
    elif icon_type == "robot":
        draw.rounded_rectangle([x, y, x + size, y + size], radius=max(2, size // 6), fill="#10B981")
        draw.rectangle([x + size * 0.2, y + size * 0.3, x + size * 0.8, y + size * 0.8], fill="#FFFFFF")
    elif icon_type == "bell":
        draw.rounded_rectangle([x, y, x + size, y + size], radius=max(2, size // 6), fill="#64748B")
        cx, cy = x + size // 2, y + size // 2
        r = size // 4
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#FFFFFF")


def _draw_mixed_text(draw, start_x, y, segments):
    current_x = start_x
    for text, font, color in segments:
        draw.text((current_x, y), text, fill=color, font=font)
        current_x += draw.textlength(text, font=font)
    return current_x

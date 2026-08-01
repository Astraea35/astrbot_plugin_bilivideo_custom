"""HTML templating and style customizer supporting Light/Dark themes and LaTeX."""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from ..core.logging import get_logger

logger = get_logger("BiliVideo/Templates")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FONTS_DIR = _REPO_ROOT / "fonts"
_ASSETS_DIR = _REPO_ROOT / "assets"

_FONT_MAP = {
    "JetBrainsMono-Light.ttf": ("JetBrains Mono", "300"),
    "JetBrainsMono-Bold.ttf": ("JetBrains Mono", "700"),
    "JetBrainsMono-Thin.ttf": ("JetBrains Mono", "100"),
}

_font_face_cache: str | None = None


def _build_font_faces() -> str:
    global _font_face_cache
    if _font_face_cache is not None:
        return _font_face_cache

    blocks: list[str] = []
    for filename, (family, weight) in _FONT_MAP.items():
        path = _FONTS_DIR / filename
        if not path.exists():
            continue
        try:
            data = base64.b64encode(path.read_bytes()).decode()
            blocks.append(
                f"@font-face{{font-family:'{family}';font-weight:{weight};font-display:swap;"
                f"src:url(data:font/truetype;base64,{data}) format('truetype')}}"
            )
        except OSError:
            continue
    _font_face_cache = "\n".join(blocks)
    return _font_face_cache


def sanitize_html(html_str: str) -> str:
    # 保持原样防御性清洗
    return html_str


def highlight_timestamps(html_str: str) -> str:
    html_str = re.sub(r"⏱\s*(\d{1,2}:\d{2})", r'<span class="ts">⏱ \1</span>', html_str)
    html_str = re.sub(r"\[(\d{1,2}:\d{2})\]", r'<span class="ts">⏱ \1</span>', html_str)
    return html_str


def extract_title(html_str: str) -> tuple[str, str]:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html_str, re.DOTALL | re.IGNORECASE)
    if not match:
        return "AI 视频总结", html_str
    title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
    body = html_str[: match.start()] + html_str[match.end() :]
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        title = f"{head} —— {tail}"
    return title, body


def wrap_chapters_in_cards(html_str: str) -> str:
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html_str, flags=re.DOTALL | re.IGNORECASE)
    if len(parts) <= 1:
        return f'<div class="card">{html_str}</div>'
    pieces: list[str] = []
    intro = parts[0].strip()
    if intro:
        pieces.append(f'<div class="card-intro">{intro}</div>')
    i = 1
    while i < len(parts):
        h2 = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        pieces.append(f'<div class="card">{h2}{body}</div>')
        i += 2
    return "\n".join(pieces)


# 修改后的基础 CSS —— 强制防止内容溢出/裁剪
_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }

/* 核心修复：防止长图截断/文字遮挡 */
html, body {
    height: auto !important;
    min-height: 100vh;
    overflow-x: visible !important;
    overflow-y: visible !important;
}
.markdown-body, .content, .card, .card-intro {
    overflow: visible !important;
    height: auto !important;
}

body {
    font-family: __FONT_STACK__;
    background-color: var(--bg);
    color: var(--text);
    width: __WIDTH__px;
    line-height: 1.7;
    font-size: 24px;
    padding: 0;
}
.header {
    background: var(--header-bg);
    padding: 50px 40px;
    text-align: center;
    border-bottom: 2px solid var(--border);
}
.header h1 {
    font-size: 38px;
    font-weight: 800;
    color: var(--title-color);
    line-height: 1.4;
}
.content { padding: 40px; display: block; clear: both; }
.card-intro {
    background: var(--intro-bg);
    border-left: 6px solid #34d399;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 30px;
    width: 100%;
}
.card {
    background: var(--card-bg);
    border-radius: 14px;
    border: 1px solid var(--border);
    border-left: 6px solid var(--title-color);
    padding: 28px;
    margin-bottom: 30px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    width: 100%;
}
h2 {
    font-size: 28px;
    font-weight: 700;
    color: var(--title-color);
    margin-bottom: 16px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
}
h3 { font-size: 24px; color: var(--title-color); margin: 20px 0 10px; }
p { margin-bottom: 12px; text-align: justify; }
strong { color: var(--title-color); font-weight: 700; }
.ts {
    display: inline-block;
    background: rgba(251,146,60,0.15);
    color: #fb923c;
    font-weight: 700;
    font-size: 18px;
    padding: 2px 10px;
    border-radius: 8px;
    margin: 0 4px;
    font-family: 'JetBrains Mono', monospace;
}
ul, ol { margin-bottom: 12px; padding-left: 30px; }
li { margin-bottom: 8px; }
blockquote {
    background: rgba(139,92,246,0.05);
    border-left: 4px solid #8b5cf6;
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 10px 10px 0;
}
code { background: var(--code-bg); padding: 4px 8px; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 20px; }
pre { background: var(--code-bg); padding: 24px; border-radius: 12px; overflow-x: auto; margin: 16px 0; }
pre code { background: transparent; padding: 0; font-size: 20px; }
table { width: 100%; border-collapse: collapse; margin: 20px 0; }
th { background: rgba(96,165,250,0.1); padding: 12px; text-align: left; border-bottom: 2px solid var(--border); }
td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
.footer { padding: 24px 40px; border-top: 1px solid var(--border); background: var(--header-bg); font-size: 18px; }
.ftxt { float: left; color: #64748b; }
.ftime { float: right; color: #64748b; font-family: 'JetBrains Mono', monospace; }
"""


def build_full_html(
    body_html: str,
    *,
    title_text: str,
    footer_time: str,
    width: int = 1400,
    theme: str = "light",
    font_family: str = "default",
) -> str:
    # 根据字体选项构建 CSS font-family 堆叠
    if font_family == "pingfang":
        font_stack = '"PingFang SC", -apple-system, "Helvetica Neue", "Microsoft YaHei", sans-serif'
    elif font_family == "harmonyos":
        font_stack = '"HarmonyOS Sans SC", "Source Han Sans CN", "Microsoft YaHei", sans-serif'
    else:  # default
        font_stack = '-apple-system, "Microsoft YaHei", "PingFang SC", sans-serif'

    # 主题配色
    if theme == "light":
        theme_vars = """
        :root {
            --bg: #ffffff;
            --text: #333333;
            --title-color: #2e7d32;
            --header-bg: #f5f5f5;
            --card-bg: #ffffff;
            --intro-bg: #f0fdf4;
            --border: #e0e0e0;
            --code-bg: #f5f5f5;
        }
        """
    else:
        theme_vars = """
        :root {
            --bg: #1a1b26;
            --text: #c0caf5;
            --title-color: #7aa2f7;
            --header-bg: #24283b;
            --card-bg: #1f2335;
            --intro-bg: #24283b;
            --border: #414868;
            --code-bg: #24283b;
        }
        """

    # 替换 CSS 中的占位符：宽度和字体族
    css = _BASE_CSS.replace("__WIDTH__", str(width)).replace("__FONT_STACK__", font_stack)
    fonts = _build_font_faces()
    
    # KaTeX 支持
    katex_scripts = """
    <link rel="stylesheet" href="https://unpkg.com/katex@0.16.9/dist/katex.min.css">
    <script defer src="https://unpkg.com/katex@0.16.9/dist/katex.min.js"></script>
    <script defer src="https://unpkg.com/katex@0.16.9/dist/contrib/auto-render.min.js"
        onload="renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false}
            ],
            throwOnError: false
        });"></script>
    """

    return (
        f"<!DOCTYPE html><html><head><meta charset=\"utf-8\">{katex_scripts}"
        f"<style>{theme_vars}{fonts}{css}</style></head><body>"
        f"<div class=\"header\"><h1>{html.escape(title_text)}</h1></div>"
        f"<div class=\"content\">{body_html}</div>"
        "<div class=\"footer\">"
        "<div class=\"ftxt\">Powered by <span>biliVideo</span> · AI 视频总结助手</div>"
        f"<div class=\"ftime\">{footer_time}</div>"
        "</div></body></html>"
    )
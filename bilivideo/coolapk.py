"""Coolapk feed fetching and normalization.

Coolapk share links are regular ``/feed/<id>`` URLs.  The public v6 feed
endpoint is used first; a lightweight HTML fallback keeps shared web links
useful when the API is unavailable.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

import aiohttp


COOLAPK_API = "https://api.coolapk.com/v6/feed/detail"
COOLAPK_HOSTS = ("coolapk.com", "www.coolapk.com", "m.coolapk.com")
COOLAPK_URL_RE = re.compile(r"https?://(?:www\.|m\.)?coolapk\.com/feed/(\d+)", re.I)
_IMAGE_RE = re.compile(r"https?://[^\"'<>\s]+\.(?:jpe?g|png|gif|webp)(?:\?[^\"'<>\s]*)?", re.I)


@dataclass(slots=True, frozen=True)
class CoolapkPost:
    feed_id: str
    url: str
    title: str = ""
    author: str = ""
    content: str = ""
    images: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def render_markdown(self) -> str:
        lines = [f"# {self.title or '酷安动态'}", "", f"**作者：** {self.author or '未知'}"]
        if self.created_at:
            lines.append(f"**发布时间：** {self.created_at}")
        lines.extend([f"**原链接：** {self.url}", "", "## 原文内容", self.content or "（无文字内容）"])
        if self.images:
            lines.extend(["", "## 原图"])
            lines.extend(f'<p><img src="{html.escape(url, quote=True)}" alt="酷安原图" /></p>' for url in self.images)
        return "\n".join(lines)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)


def extract_coolapk_url(text: str) -> str | None:
    match = COOLAPK_URL_RE.search(text or "")
    return match.group(0).rstrip("，。！？!?)]}>") if match else None


def extract_coolapk_id(url: str) -> str | None:
    match = COOLAPK_URL_RE.search(url or "")
    return match.group(1) if match else None


def is_coolapk_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in COOLAPK_HOSTS and extract_coolapk_id(url) is not None


def _clean_html(value: object) -> str:
    parser = _TextParser()
    parser.feed(str(value or ""))
    return " ".join(parser.parts).strip()


def _walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _find_images(value: object) -> list[str]:
    found: list[str] = []
    for obj in _walk_dicts(value):
        for key, item in obj.items():
            key_l = str(key).lower()
            if key_l not in {"pic", "picarr", "message_cover", "images", "imagelist", "image_list", "url"}:
                continue
            values = item if isinstance(item, list) else [item]
            for candidate in values:
                if isinstance(candidate, dict):
                    candidate = candidate.get("url") or candidate.get("pic") or candidate.get("originUrl")
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    if re.search(r"\.(?:jpe?g|png|gif|webp)(?:\?|$)", candidate, re.I) or key_l != "url":
                        found.append(candidate)
    return list(dict.fromkeys(found))


def _pick_feed(payload: object, feed_id: str) -> dict:
    candidates = list(_walk_dicts(payload))
    for obj in candidates:
        if str(obj.get("entityId", obj.get("id", ""))) == feed_id and any(k in obj for k in ("message", "message_title", "title")):
            return obj
    for obj in candidates:
        if "message" in obj or "message_title" in obj:
            return obj
    return payload if isinstance(payload, dict) else {}


async def fetch_coolapk_post(url: str, *, timeout: float = 15.0) -> CoolapkPost:
    feed_id = extract_coolapk_id(url)
    if not feed_id:
        raise ValueError("无效的酷安动态链接")
    canonical_url = f"https://www.coolapk.com/feed/{feed_id}"
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; Android 10; Pixel 4) +CoolMarket/7.3",
        "X-Requested-With": "XMLHttpRequest",
        "X-Sdk-Locale": "zh-CN",
        "X-App-Id": "coolmarket",
        "X-App-Version": "7.3",
        "X-Api-Version": "7",
        "Referer": canonical_url,
    }
    payload: object = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as session:
        try:
            async with session.get(COOLAPK_API, params={"id": feed_id}) as response:
                if response.status < 400:
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, OSError, json.JSONDecodeError):
            payload = {}
        if not payload:
            try:
                async with session.get(canonical_url) as response:
                    page = await response.text(errors="ignore")
                    payload = {
                        "title": re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S).group(1)
                        if re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S) else "",
                        "message": re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S).group(1)
                        if re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S) else "",
                        "images": _IMAGE_RE.findall(page),
                    }
            except (aiohttp.ClientError, OSError):
                payload = {}
    item = _pick_feed(payload, feed_id)
    title = str(item.get("title") or item.get("message_title") or "酷安动态").strip()
    author = str(item.get("username") or item.get("userName") or item.get("user_name") or item.get("author") or "未知").strip()
    content = _clean_html(item.get("message") or item.get("message_brief") or item.get("description") or item.get("content"))
    images = tuple(_find_images(item) or _find_images(payload))
    created = str(item.get("dateline") or item.get("create_time") or item.get("created_at") or "").strip()
    if not content and not images:
        raise RuntimeError("酷安动态内容获取失败，可能需要稍后重试")
    return CoolapkPost(feed_id, canonical_url, title, author, content, images, created, item)


def build_coolapk_prompt(post: CoolapkPost, style: str = "professional") -> str:
    return f"""你是一名中文文本帖总结助手。请总结下面这篇酷安帖子，写作风格为「{style}」。

这是一篇社区文本帖，不是视频，也没有字幕、时间轴或视频转写内容。请依据标题、作者、正文以及随请求提供的帖子图片进行总结。图片是正文语境的一部分：请识别图片中可读的文字、型号、参数、表格、报错、界面和示意信息，并与正文交叉核对；无法清晰辨认的内容要明确说明，不要臆测。

标题：{post.title}
作者：{post.author}
正文：
{post.content}

请使用 Markdown，并遵守以下要求：
1. 开头先给出一段简短结论，说明这篇帖子主要在讲什么。
2. 提炼 3-8 条关键事实、观点或操作步骤，保持原文的条件和限定语。
3. 如果正文涉及产品、手机、应用、系统、型号、版本、价格、参数或兼容性，请准确保留这些信息。
4. 如果正文包含优缺点、适用人群、风险、限制或注意事项，请单独列出，不要把推测写成事实。
5. 保留正文中的重要链接、代码、命令和专有名词；不要改写链接地址。
6. 正文信息不足时明确说“原文未说明”，不要补充臆测内容。
7. 不要输出视频总结常见的“时间轴”“字幕”“片段”等内容。"""

"""Coolapk feed fetching and normalization.

Coolapk share links are regular ``/feed/<id>`` URLs.  The public v6 feed
endpoint is used first; a lightweight HTML fallback keeps shared web links
useful when the API is unavailable.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]


COOLAPK_API = "https://api.coolapk.com/v6/feed/detail"
COOLAPK_HOSTS = ("coolapk.com", "www.coolapk.com", "m.coolapk.com")
COOLAPK_URL_RE = re.compile(r"https?://(?:www\.|m\.)?coolapk\.com/feed/(\d+)", re.I)
_IMAGE_RE = re.compile(r"https?://[^\"'<>\s]+\.(?:jpe?g|png|gif|webp)(?:\?[^\"'<>\s]*)?", re.I)
_INVALID_CONTENT_PATTERNS = (
    "请求未验证",
    "token expired",
    "need login",
    "您当前查看的是「动态分享」",
    "请用酷安手机APP扫码查看详情",
    "在酷安App内打开",
)


def generate_coolapk_token(device_id: str | None = None) -> str:
    """Generate dynamic X-App-Token for Coolapk API authentication."""
    dev_id = device_id or str(uuid.uuid4())
    t = int(time.time())
    hex_t = hex(t)
    md5_t = hashlib.md5(str(t).encode("utf-8")).hexdigest()
    raw = f"token://com.coolapk.market/c67ef59437ac340d694c483a936a2829?{md5_t}${dev_id}&com.coolapk.market"
    b64_raw = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
    md5_raw = hashlib.md5(b64_raw.encode("utf-8")).hexdigest()
    return f"{md5_raw}{dev_id}{hex_t}"


@dataclass(slots=True, frozen=True)
class CoolapkPost:
    feed_id: str
    url: str
    title: str = ""
    author: str = ""
    content: str = ""
    images: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = ""
    likes: str = "0"
    comments: str = "0"
    shares: str = "0"
    raw: dict = field(default_factory=dict)

    @property
    def render_markdown(self) -> str:
        lines = [f"# {self.title or '酷安动态'}", "", f"**作者：** {self.author or '未知'}"]
        if self.created_at:
            lines.append(f"**发布时间：** {self.created_at}")
        if self.likes or self.comments:
            lines.append(f"**互动数据：** 👍 {self.likes} · 💬 {self.comments}")
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
    if isinstance(payload, dict):
        # If the response root is an error response, ignore it
        if payload.get("status") not in (None, 0, 1, 200) and "data" not in payload:
            return {}
        if isinstance(payload.get("data"), dict):
            return payload["data"]

    candidates = list(_walk_dicts(payload))
    for obj in candidates:
        if str(obj.get("entityId", obj.get("id", ""))) == feed_id and any(k in obj for k in ("message", "message_title", "title")):
            return obj
    for obj in candidates:
        if ("message" in obj or "message_title" in obj) and obj.get("message") not in _INVALID_CONTENT_PATTERNS:
            return obj
    return payload if isinstance(payload, dict) else {}


async def fetch_coolapk_post(url: str, *, timeout: float = 15.0) -> CoolapkPost:
    feed_id = extract_coolapk_id(url)
    if not feed_id:
        raise ValueError("无效的酷安动态链接")
    canonical_url = f"https://www.coolapk.com/feed/{feed_id}"
    device_id = str(uuid.uuid4())
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 4 Build/SP1A.210812.016) (#Build; Google; Pixel 4; SP1A.210812.016; 12) +CoolMarket/14.5.1",
        "X-Requested-With": "XMLHttpRequest",
        "X-Sdk-Int": "31",
        "X-Sdk-Locale": "zh-CN",
        "X-App-Id": "com.coolapk.market",
        "X-App-Token": generate_coolapk_token(device_id),
        "X-App-Version": "14.5.1",
        "X-App-Code": "2411200",
        "X-Api-Version": "14",
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
        if not payload or (isinstance(payload, dict) and payload.get("status") not in (None, 0, 1, 200) and "data" not in payload):
            web_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.coolapk.com/",
            }
            try:
                async with session.get(canonical_url, headers=web_headers) as response:
                    page = await response.text(errors="ignore")
                    title_m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S)
                    desc_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S)
                    payload = {
                        "title": title_m.group(1) if title_m else "",
                        "message": desc_m.group(1) if desc_m else "",
                        "images": _IMAGE_RE.findall(page),
                    }
            except (aiohttp.ClientError, OSError):
                payload = {}
    item = _pick_feed(payload, feed_id)
    raw_message = item.get("message") or item.get("message_brief") or item.get("description") or item.get("content") or ""
    content = _clean_html(raw_message)

    # Filter out anti-crawler and API auth failure strings
    if any(pat in content for pat in _INVALID_CONTENT_PATTERNS):
        content = ""

    title = str(item.get("title") or item.get("message_title") or "酷安动态").strip()
    author = str(item.get("username") or item.get("userName") or item.get("user_name") or item.get("author") or "").strip()
    images = tuple(_find_images(item) or _find_images(payload))
    created = str(item.get("dateline") or item.get("create_time") or item.get("created_at") or "").strip()
    likes = str(item.get("likenum") or item.get("like_num") or item.get("likes") or "0").strip()
    comments = str(item.get("replynum") or item.get("commentnum") or item.get("comments") or "0").strip()
    shares = str(item.get("sharenum") or item.get("shares") or "0").strip()

    if not content and not images:
        raise RuntimeError("酷安动态内容获取失败（接口鉴权未通过或受官方反爬保护），请稍后重试")

    if not author:
        author = "酷安用户"

    return CoolapkPost(
        feed_id=feed_id,
        url=canonical_url,
        title=title,
        author=author,
        content=content,
        images=images,
        created_at=created,
        likes=likes,
        comments=comments,
        shares=shares,
        raw=item,
    )


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

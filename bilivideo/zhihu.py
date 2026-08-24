"""知乎内容抓取、HTML 转 Markdown 和统一总结负载。"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

try:
    import aiohttp
except ImportError:  # pragma: no cover - production dependencies include aiohttp
    aiohttp = None  # type: ignore[assignment]


_ANSWER_RE = re.compile(r"https?://(?:www\.)?zhihu\.com/question/\d+/answer/(\d+)", re.I)
_ARTICLE_RE = re.compile(r"https?://zhuanlan\.zhihu\.com/p/(\d+)", re.I)
_QUESTION_RE = re.compile(r"https?://(?:www\.)?zhihu\.com/question/(\d+)(?:[/?#]|$)", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.zhihu.com/",
    "Origin": "https://www.zhihu.com",
}


@dataclass(slots=True, frozen=True)
class ZhihuPost:
    content_type: str
    content_id: str
    title: str
    author: str
    content: str
    source_url: str
    voteup_count: int = 0
    comment_count: int = 0
    images: tuple[str, ...] = field(default_factory=tuple)

    @property
    def render_markdown(self) -> str:
        kind = {"answer": "知乎回答", "article": "知乎专栏", "question": "知乎问题"}.get(
            self.content_type, "知乎内容"
        )
        lines = [
            f"# {self.title or kind}",
            "",
            f"**类型：** {kind}",
            f"**作者：** {self.author or '匿名用户'}",
            f"**原链接：** {self.source_url}",
        ]
        if self.voteup_count or self.comment_count:
            lines.append(f"**互动：** 赞同 {self.voteup_count} · 评论 {self.comment_count}")
        lines.extend(["", "## 原文", self.content or "（正文为空）"])
        return "\n".join(lines)


class _ZhihuMarkdownParser(HTMLParser):
    """Small dependency-free HTML-to-Markdown converter for Zhihu rich text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.link_href = ""
        self.link_text: list[str] = []
        self.in_pre = False
        self.image_urls: list[str] = []

    def _emit(self, value: str) -> None:
        self.parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "section", "figure"}:
            self._emit("\n\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._emit(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "br":
            self._emit("\n")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "blockquote":
            self._emit("\n\n> ")
        elif tag == "pre":
            self.in_pre = True
            self._emit("\n\n```\n")
        elif tag == "code" and not self.in_pre:
            self._emit("`")
        elif tag == "li":
            self._emit("\n- ")
        elif tag == "a":
            self.link_href = attrs_map.get("href") or ""
            self.link_text = []
        elif tag == "img":
            src = attrs_map.get("data-original") or attrs_map.get("src") or ""
            alt = attrs_map.get("alt") or "知乎图片"
            if src.startswith(("http://", "https://")):
                self.image_urls.append(src)
                self._emit(f"\n\n![{alt}]({src})\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "svg"}:
                self.skip_depth -= 1
            return
        if tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "a":
            text = "".join(self.link_text).strip()
            self._emit(f"[{text}]({self.link_href})" if text and self.link_href else text)
            self.link_href = ""
            self.link_text = []
        elif tag == "code" and not self.in_pre:
            self._emit("`")
        elif tag == "pre":
            self.in_pre = False
            self._emit("\n```\n")
        elif tag in {"p", "div", "section", "figure", "blockquote"}:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.link_href:
            self.link_text.append(data)
        else:
            self._emit(data)

    def get_markdown(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def extract_zhihu_url(text: str) -> str | None:
    for raw_url in _URL_RE.findall(text or ""):
        url = raw_url.rstrip("，。！？!?)]}>")
        if detect_zhihu_url(url):
            return url
    return None


def detect_zhihu_url(url_or_text: str) -> tuple[str, str] | None:
    answer = _ANSWER_RE.search(url_or_text or "")
    if answer:
        return "answer", answer.group(1)
    article = _ARTICLE_RE.search(url_or_text or "")
    if article:
        return "article", article.group(1)
    question = _QUESTION_RE.search(url_or_text or "")
    if question and "/answer/" not in url_or_text:
        return "question", question.group(1)
    return None


def _to_markdown(content_html: str) -> tuple[str, tuple[str, ...]]:
    parser = _ZhihuMarkdownParser()
    parser.feed(content_html or "")
    markdown = parser.get_markdown()
    images = tuple(dict.fromkeys(parser.image_urls))
    return markdown, images


class ZhihuClient:
    def __init__(self, cookie: str = "", timeout: float = 15.0) -> None:
        self.cookie = cookie.strip()
        self.timeout = timeout
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def fetch(self, url: str) -> ZhihuPost:
        detected = detect_zhihu_url(url)
        if not detected:
            raise ValueError("无法识别知乎链接")
        if not self.cookie:
            raise ValueError("未配置知乎 Cookie，请在插件设置中填写")
        content_type, content_id = detected
        if content_type == "answer":
            data = await self._request(
                f"https://www.zhihu.com/api/v4/answers/{content_id}",
                {"include": "content,voteup_count,comment_count,author.name,question.id,question.title"},
            )
            normalized = self._normalize_answer(data, content_id)
        elif content_type == "article":
            data = await self._request(f"https://api.zhihu.com/articles/{content_id}")
            if not data:
                data = await self._request(f"https://www.zhihu.com/api/v4/articles/{content_id}")
            normalized = self._normalize_article(data, content_id)
        else:
            data = await self._request(
                f"https://www.zhihu.com/api/v4/questions/{content_id}/answers",
                {"limit": "1", "sort_by": "default", "include": "content,voteup_count,comment_count,author.name,question.id,question.title"},
            )
            answers = (data or {}).get("data") or []
            if not answers:
                raise RuntimeError("知乎问题暂无可用回答")
            normalized = self._normalize_answer(answers[0], str(answers[0].get("id", "")))
        if not normalized or not normalized.get("content_html"):
            raise RuntimeError("知乎内容为空或无法获取")
        content, images = _to_markdown(normalized["content_html"])
        if not content:
            raise RuntimeError("知乎正文解析为空")
        return ZhihuPost(
            content_type=normalized["type"],
            content_id=normalized["id"],
            title=normalized["title"],
            author=normalized["author"],
            content=content,
            source_url=normalized["source_url"],
            voteup_count=int(normalized.get("voteup_count") or 0),
            comment_count=int(normalized.get("comment_count") or 0),
            images=images,
        )

    async def _request(self, url: str, params: dict[str, str] | None = None) -> dict | None:
        if aiohttp is None:
            raise RuntimeError("缺少 aiohttp 依赖，请按 requirements.txt 安装插件依赖")
        async with self._lock:
            elapsed = asyncio.get_running_loop().time() - self._last_request
            if elapsed < 0.3:
                await asyncio.sleep(0.3 - elapsed)
            self._last_request = asyncio.get_running_loop().time()
        headers = {**_HEADERS, "Cookie": self.cookie}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.get(url, params=params, headers=headers) as response:
                if response.status in {401, 403}:
                    raise RuntimeError("知乎 Cookie 无效或已过期")
                if response.status == 404:
                    raise RuntimeError("知乎内容不存在或已删除")
                if response.status == 429:
                    raise RuntimeError("知乎接口限流，请稍后重试")
                if response.status >= 400:
                    raise RuntimeError(f"知乎接口返回 HTTP {response.status}")
                return await response.json(content_type=None)

    @staticmethod
    def _normalize_answer(data: dict | None, fallback_id: str) -> dict:
        data = data or {}
        question = data.get("question") or {}
        author = data.get("author") or {}
        qid = str(question.get("id") or "")
        aid = str(data.get("id") or fallback_id)
        return {
            "type": "answer", "id": aid, "title": str(question.get("title") or "知乎回答"),
            "content_html": data.get("content") or "", "author": str(author.get("name") or "匿名用户"),
            "voteup_count": data.get("voteup_count", 0), "comment_count": data.get("comment_count", 0),
            "source_url": f"https://www.zhihu.com/question/{qid}/answer/{aid}",
        }

    @staticmethod
    def _normalize_article(data: dict | None, fallback_id: str) -> dict:
        data = data or {}
        author = data.get("author") or {}
        aid = str(data.get("id") or fallback_id)
        return {
            "type": "article", "id": aid, "title": str(data.get("title") or "知乎专栏文章"),
            "content_html": data.get("content") or "", "author": str(author.get("name") or "匿名用户"),
            "voteup_count": data.get("voteup_count", 0), "comment_count": data.get("comment_count", 0),
            "source_url": f"https://zhuanlan.zhihu.com/p/{aid}",
        }

"""Single source of truth for building forward-message Nodes across all platforms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..core.types import VideoInfo
from .builders import format_video_info_block
from .chunker import split_text_for_messages

# Lazy AstrBot imports so the module is import-safe in tests.
try:
    from astrbot.api.message_components import Image, Node, Nodes, Plain  # type: ignore[import]
except Exception:  # pragma: no cover - test env stubs
    Plain = Image = Node = Nodes = None  # type: ignore[assignment]


def _build_image_component(pic: str) -> Any | None:
    """Create Image component from either URL or local file path."""
    if not pic or Image is None:
        return None
    try:
        if pic.startswith(("http://", "https://")):
            return Image.fromURL(pic)
        return Image.fromFileSystem(pic)
    except Exception:
        return None


def _format_meta_lines(item: Any) -> tuple[str, str | None, list[str], str]:
    """Extract (title, cover_url, meta_lines, summary_label) from various post/video types."""
    # 1. Standard VideoInfo
    if isinstance(item, VideoInfo):
        title = f"📺 {item.title}"
        cover = item.normalized_pic
        meta_lines = [format_video_info_block(item)]
        label = "📝 AI 视频总结"
        return title, cover, meta_lines, label

    # 2. CoolapkPost
    if hasattr(item, "feed_id") and hasattr(item, "images"):
        title = f"📱 {getattr(item, 'title', '') or '酷安动态'}"
        images = getattr(item, "images", ())
        cover = images[0] if images else ""
        author = getattr(item, "author", "") or "酷安用户"
        created = getattr(item, "created_at", "")
        likes = getattr(item, "likes", "0")
        comments = getattr(item, "comments", "0")
        url = getattr(item, "url", "")
        content = getattr(item, "content", "")

        meta = [f"📱 酷安动态 · {author}"]
        if created:
            meta.append(f"📅 发布时间: {created}")
        if likes != "0" or comments != "0":
            meta.append(f"📊 互动数据: 👍 {likes} | 💬 {comments}")
        if content:
            snippet = content[:100] + ("..." if len(content) > 100 else "")
            meta.append(f"📝 正文简介: {snippet}")
        if url:
            meta.append(f"🔗 原文直达: {url}")
        return title, cover, meta, "📝 AI 动态总结"

    # 3. ZhihuPost
    if hasattr(item, "content_type") and hasattr(item, "source_url"):
        ctype = getattr(item, "content_type", "")
        kind = {"answer": "知乎回答", "article": "知乎专栏", "question": "知乎问题"}.get(ctype, "知乎内容")
        title = f"📚 {getattr(item, 'title', '') or kind}"
        images = getattr(item, "images", ())
        cover = images[0] if images else ""
        author = getattr(item, "author", "") or "匿名用户"
        votes = getattr(item, "voteup_count", 0)
        comments = getattr(item, "comment_count", 0)
        url = getattr(item, "source_url", "")
        content = getattr(item, "content", "")

        meta = [f"📚 {kind} · {author}"]
        if votes or comments:
            meta.append(f"📊 互动数据: 👍 赞同 {votes} | 💬 评论 {comments}")
        if content:
            snippet = content[:100] + ("..." if len(content) > 100 else "")
            meta.append(f"📝 正文简介: {snippet}")
        if url:
            meta.append(f"🔗 原文直达: {url}")
        return title, cover, meta, "📝 AI 知乎总结"

    # 4. Dict payload (e.g. Douyin metadata dictionary)
    if isinstance(item, dict):
        desc = item.get("desc") or item.get("title") or "内容详情"
        title = f"🎵 {desc}" if "douyin" in str(item.get("link", "")) else f"📄 {desc}"
        cover = item.get("cover") or item.get("thumbnail") or item.get("pic") or ""
        author = item.get("author") or item.get("uploader") or ""
        url = item.get("link") or item.get("url") or ""
        meta = []
        if author:
            meta.append(f"👤 创作者: {author}")
        stats = []
        if item.get("likes"):
            stats.append(f"❤️ {item['likes']}")
        if item.get("comments"):
            stats.append(f"💬 {item['comments']}")
        if item.get("collects"):
            stats.append(f"⭐ {item['collects']}")
        if item.get("shares"):
            stats.append(f"🔗 {item['shares']}")
        if stats:
            meta.append("📊 互动数据: " + " | ".join(stats))
        if item.get("desc") and item.get("desc") != desc:
            meta.append(f"📝 文案简介: {item['desc'][:100]}")
        if url:
            meta.append(f"🔗 网页直达: {url}")
        return title, cover, meta, "📝 AI 视频总结"

    # 5. Fallback generic object
    title = f"📄 {getattr(item, 'title', '') or '内容总结'}"
    cover = getattr(item, 'normalized_pic', '') or getattr(item, 'cover', '') or ""
    url = getattr(item, 'url', '')
    meta = []
    if hasattr(item, "author"):
        meta.append(f"👤 作者: {getattr(item, 'author')}")
    if url:
        meta.append(f"🔗 直达链接: {url}")
    return title, cover, meta or [title], "📝 AI 内容总结"


def build_forward_nodes(
    info: Any,
    rendered: Sequence[Any] | str,
    *,
    bot_name: str,
    bot_uin: str,
    summary_label: str | None = None,
) -> Any:
    """Pack `[cover+title, info, summary]` into a `Nodes` payload for any platform.

    `rendered` may be a sequence of `Image` components (image mode) or a raw
    Markdown string (text mode); both are split sensibly so platforms with
    a 2000-char per-message ceiling don't truncate.
    """
    if Node is None:  # tests / imports without AstrBot installed
        raise RuntimeError("AstrBot message components are unavailable")

    title, cover, meta_lines, default_label = _format_meta_lines(info)
    label_prefix = summary_label or default_label

    nodes: list[Any] = []

    # 1. cover + title
    cover_content: list[Any] = []
    if cover:
        img_comp = _build_image_component(cover)
        if img_comp is not None:
            cover_content.append(img_comp)
    cover_content.append(Plain(title))
    nodes.append(Node(content=cover_content, name=bot_name, uin=bot_uin))

    # 2. metadata
    meta_text = "\n".join(meta_lines).strip()
    if meta_text:
        nodes.append(
            Node(
                content=[Plain(meta_text)],
                name=bot_name,
                uin=bot_uin,
            )
        )

    # 3. summary content
    if isinstance(rendered, str):
        for idx, chunk in enumerate(split_text_for_messages(rendered)):
            label = label_prefix if idx == 0 else f"{label_prefix}(第 {idx + 1} 部分)"
            nodes.append(Node(content=[Plain(f"{label}\n\n{chunk}")], name=bot_name, uin=bot_uin))
    else:
        image_idx = 0
        for comp in rendered:
            if Plain is not None and isinstance(comp, Plain):
                label = "⚠️ 渲染说明 / 文本内容"
            else:
                image_idx += 1
                label = label_prefix if image_idx == 1 else f"{label_prefix}(第 {image_idx} 页)"
            nodes.append(Node(content=[Plain(label), comp], name=bot_name, uin=bot_uin))

    return Nodes(nodes=nodes)


# Backwards compatibility alias
build_video_forward_nodes = build_forward_nodes

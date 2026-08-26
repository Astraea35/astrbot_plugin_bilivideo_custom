"""Build user-facing text/messages for video info & metadata."""

from __future__ import annotations

import contextlib
import time

from ..core.config import PluginConfig
from ..core.types import VideoInfo
from .chunker import format_count


def format_video_stats(info: VideoInfo) -> str:
    """Render all interaction counters in the plain video-information block."""

    return (
        f"▶️ {format_count(info.view)}播放  "
        f"💬 {format_count(info.danmaku)}弹幕  "
        f"👍 {format_count(info.like)}点赞  "
        f"🪙 {format_count(info.coin)}投币  "
        f"⭐ {format_count(info.favorite)}收藏  "
        f"💭 {format_count(info.reply)}评论  "
        f"🔁 {format_count(info.share)}分享"
    )


def format_video_summary_lines(
    info: VideoInfo,
    *,
    config: PluginConfig,
    desc_max: int | None = None,
) -> list[str]:
    """Build the text lines for an auto-detect "card-like" announcement.

    Each toggle in the config is honored; the caller decides whether to
    prepend a cover image component.
    """

    lines: list[str] = [f"📺 {info.title}"]
    if config.detect_show_uploader:
        lines.append(f"👤 UP主: {info.owner_name}")
    if config.detect_show_desc and info.desc:
        desc = info.desc
        limit = getattr(config, "detect_desc_max_len", 0) if desc_max is None else desc_max
        if limit > 0 and len(desc) > limit:
            desc = desc[:limit] + "..."
        lines.append(f"📝 简介: {desc}")
    if config.detect_show_pubtime and info.pubdate:
        try:
            pub_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.pubdate))
            lines.append(f"📅 发布: {pub_str}")
        except (ValueError, OSError):
            pass
    if config.detect_show_stats:
        lines.append(format_video_stats(info))
    if config.detect_show_link:
        lines.append(f"🔗 {info.url}")
    return lines


def format_video_info_block(info: VideoInfo, *, desc_max: int = 0) -> str:
    """Compact info block used in forward-message nodes."""

    parts: list[str] = [f"👤 UP主: {info.owner_name}"]
    if info.desc:
        desc = info.desc
        if desc_max > 0 and len(desc) > desc_max:
            desc = desc[:desc_max] + "..."
        parts.append(f"📝 简介: {desc}")
    if info.pubdate:
        with contextlib.suppress(ValueError, OSError):
            parts.append(
                f"📅 发布时间: {time.strftime('%Y-%m-%d %H:%M', time.localtime(info.pubdate))}"
            )
    parts.append(format_video_stats(info))
    parts.append(f"🔗 {info.url}")
    return "\n".join(parts)

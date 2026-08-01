"""Scheduled push callback supporting only UP type."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from ..api.endpoints import get_latest_videos
from ..access.control import is_auto_summary_allowed
from ..core.exceptions import BiliVideoError
from ..core.types import LatestVideo, VideoInfo
from ..messaging.forward import build_video_forward_nodes
from ..services import BiliVideoServices
from ..subscription.manager import Subscription
from ._render_helper import render_note_components

try:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Image, Plain
except Exception:
    Plain = Image = MessageChain = None


async def push_callback(
    services: BiliVideoServices,
    origin: str,
    sub: Subscription,
) -> int:
    if services.astrbot_context is None:
        services.logger.error("push_callback: astrbot_context is None; cannot deliver push")
        return 0

    # ================= 订阅类型守卫（仅视频） =================
    user_sub_types = getattr(sub, "sub_types", ["视频"])
    if isinstance(user_sub_types, str):
        user_sub_types = [user_sub_types]
    else:
        user_sub_types = list(user_sub_types)

    # 只有明确订阅了“视频”才触发定时推送（不因“动态”宏而触发）
    if "视频" not in user_sub_types:
        services.logger.info(
            f"定时推送器拦截：博主 {sub.name} 发布了视频，但本会话未订阅视频类目，跳过投递。"
        )
        return 0
    # ========================================================

    try:
        videos = await get_latest_videos(services.http_client, sub.mid, count=1)
    except Exception as exc:
        services.logger.warning(f"latest fetch failed for {sub.name}: {exc}")
        return 0
    if not videos:
        return 0

    latest = videos[0]

    # 如果 bvid 无效（不是 BV 开头），直接跳过，避免写入错误数据
    if not latest.bvid or not latest.bvid.startswith("BV"):
        services.logger.warning(
            f"跳过推送：无效的 BV 号格式 ({latest.bvid}) for {sub.name}"
        )
        return 0

    if latest.bvid == sub.last_bvid:
        return 0

    # 首次订阅，只记录 bvid，不推送
    if not sub.last_bvid:
        await services.subscription_manager.update_last_video(origin, sub.mid, latest.bvid)
        return 0

    services.logger.info(f"new video for {sub.name}: {latest.title}")
    chain_components = await _build_chain(services, origin, sub, latest)
    if not chain_components:
        services.logger.warning(f"empty push chain for {sub.name}; will retry on next check")
        return 0

    # Deliver back to the exact conversation that created the subscription.
    # The platform prefix is therefore discovered from AstrBot, never guessed.
    push_origins = [origin]
    services.logger.info(
        f"delivering scheduled push for {sub.name} to {len(push_origins)} target(s): {push_origins}"
    )
    sent_count = 0
    for target in push_origins:
        try:
            mc = MessageChain(chain=chain_components)
            await asyncio.wait_for(
                services.astrbot_context.send_message(target, mc), timeout=30
            )
            sent_count += 1
            services.logger.info(f"scheduled push send call completed for {target}: {latest.bvid}")
        except asyncio.TimeoutError:
            services.logger.error(f"push to {target} timed out after 30 seconds")
        except Exception as exc:
            services.logger.error(f"push to {target} failed: {exc}")

    if sent_count > 0:
        # Mark the video only after a delivery succeeds.  Otherwise a temporary
        # rendering, LLM, or adapter failure would permanently swallow it.
        await services.subscription_manager.update_last_video(origin, sub.mid, latest.bvid)
        return 1

    services.logger.warning(
        f"new video for {sub.name} was not sent to any target; will retry: "
        f"bvid={latest.bvid} targets={push_origins}"
    )
    return 0


async def _build_chain(
    services: BiliVideoServices,
    origin: str,
    sub: Subscription,
    latest: LatestVideo,
) -> list[Any]:
    push_header = f"🔔 UP主【{sub.name}】发布了新视频!\n"
    config = services.config
    if Plain is None:
        services.logger.error("scheduled push: Plain component unavailable; skipping")
        return []
    bvid = latest.bvid
    video_url = f"https://www.bilibili.com/video/{bvid}"
    info: VideoInfo | None = None

    auto_summary_allowed = is_auto_summary_allowed(
        origin,
        config=config,
        sender_id=getattr(sub, "auto_summary_user_id", ""),
    )
    if sub.auto_summary and not auto_summary_allowed:
        sub.auto_summary = False
        sub.auto_summary_user_id = ""
        await services.subscription_manager.update_subscription(origin, sub)
        services.logger.info("自动总结权限失效，订阅已降级为仅推送: %s", sub.name)
    if not config.auto_push_summary or not sub.auto_summary or not auto_summary_allowed:
        lines = [push_header + f"📺 {latest.title}"]
        if latest.description:
            desc = latest.description if len(latest.description) <= 100 else latest.description[:100] + "..."
            lines.append(f"📝 简介: {desc}")
        if latest.pubdate:
            with contextlib.suppress(ValueError, OSError):
                lines.append(
                    f"📅 发布: {time.strftime('%Y-%m-%d %H:%M', time.localtime(latest.pubdate))}"
                )
        lines.append(f"🔗 {video_url}")
        chain: list[Any] = []
        if latest.pic and Image is not None:
            chain.append(Image.fromURL(latest.pic))
        chain.append(Plain("\n".join(lines)))
        return chain

    try:
        note = await services.orchestrator.generate(video_url)
        info = note.video_info
        rendered = await render_note_components(services, note.markdown)
    except BiliVideoError as exc:
        services.logger.warning(f"summary generation failed: {exc}")
        rendered = exc.user_message

    if config.enable_forward_message and info is not None:
        try:
            forward = build_video_forward_nodes(
                info,
                rendered,
                bot_name=config.forward_bot_name,
                bot_uin=config.forward_bot_uin,
            )
            from astrbot.api.message_components import Node
            forward.nodes.insert(
                0,
                Node(
                    content=[Plain(push_header.rstrip())],
                    name=config.forward_bot_name,
                    uin=config.forward_bot_uin,
                ),
            )
            return [forward]
        except Exception as exc:
            services.logger.warning(f"forward path failed, fallback: {exc}")

    if isinstance(rendered, list):
        return [Plain(push_header), *list(rendered)]
    if info is not None:
        body = f"{push_header}━━━━━━━━━━━━━━━━━━━\n\n{rendered}"
        return [Plain(body)]
    return [Plain(push_header + str(rendered))]

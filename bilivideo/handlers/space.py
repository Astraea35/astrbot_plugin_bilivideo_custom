"""简化的 UP 主空间查看终端（仅支持 UP 主，支持序号前缀）"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from ..access.control import is_allowed, is_manual_summary_allowed
from ..api.endpoints import (
    get_uploader_info,
    get_video_info,
    search_uploader_by_name,
)
from ..api.wbi import sign_params
from ..core.exceptions import BiliVideoError
from ..parsing.url_extractor import extract_uid
from ..services import BiliVideoServices
from ._render_helper import render_note_components
from ._send_helper import yield_note_response
from ._utils import parse_command_args, resolve_subscription_index

SPACE_PAGINATION_CACHE: dict[str, dict] = {}


async def fetch_space_videos_by_page(services: BiliVideoServices, mid: str, page: int = 1, retry_count: int = 0) -> list[dict]:
    """通过 B 站 API 获取 UP 主空间视频列表（带 order_avoided 参数，支持 -799 自动重试）"""
    try:
        params = {
            "mid": mid,
            "ps": 12,
            "pn": page,
            "order": "pubdate",
            "order_avoided": "true",   # 关键魔法参数，绕过未登录风控
        }
        # 可选：添加 web_location 模拟更真实的请求
        # params["web_location"] = 1550101

        # 使用 BilibiliHTTPClient 的 request_json 方法（自带重试、Cookie 管理）
        payload = await services.http_client.request_json(
            "GET",
            "https://api.bilibili.com/x/space/arc/search",
            params=params,
            expect_code_zero=False   # 不强制要求 code=0，我们自己处理错误码
        )
        code = payload.get("code")
        if code == 0:
            data = payload.get("data", {})
            vlist = data.get("list", {}).get("vlist", [])
            return vlist
        elif code == -799 and retry_count < 2:
            # 请求过于频繁，等待后重试
            wait_time = 2 ** (retry_count + 1)  # 2秒, 4秒
            services.logger.warning(f"B站限流(-799) {mid}，{wait_time}秒后重试 (第{retry_count+1}次重试)...")
            await asyncio.sleep(wait_time)
            return await fetch_space_videos_by_page(services, mid, page, retry_count + 1)
        else:
            services.logger.warning(f"获取空间视频失败 {mid}: code={code}, msg={payload.get('message')}")
            return []
    except Exception as e:
        if retry_count < 2:
            await asyncio.sleep(2)
            return await fetch_space_videos_by_page(services, mid, page, retry_count + 1)
        else:
            services.logger.warning(f"获取空间视频异常 {mid}: {e}")
            return []


async def handle_space_view(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return

    clean_str = parse_command_args(getattr(event, "message_str", "") or "").strip()
    body_parts = clean_str.split()
    query_target = body_parts[0] if body_parts else ""
    target_page = 1
    if len(body_parts) > 1 and body_parts[-1].isdigit():
        target_page = int(body_parts[-1])
        if target_page < 1:
            target_page = 1
        query_target = " ".join(body_parts[:-1]).strip()
    else:
        query_target = clean_str

    origin = getattr(event, "unified_msg_origin", "")
    # 先尝试序号解析（只有明确带“序号”关键字才生效）
    resolved = await resolve_subscription_index(services, origin, query_target)
    if resolved:
        mid, up_name = resolved
    else:
        # 原有逻辑：UID / 昵称 / 空间链接
        if query_target.isdigit():
            mid = query_target
            # 🌟【硬核修复】：当传入纯数字 mid 时，同步调取 API 获取博主名字，获取失败则降级为带有 mid 的友好称呼
            info = await get_uploader_info(services.http_client, mid)
            up_name = info.name if info else f"UP主_{mid}"
        else:
            mid = extract_uid(query_target)
            if not mid:
                uploader = await search_uploader_by_name(services.http_client, query_target)
                if uploader is None:
                    yield event.plain_result("❌ 无法锚定目标UP主。用法: /主页视频 <名称/UID/序号X> [页码]")
                    return
                mid = uploader.mid
                up_name = uploader.name
            else:
                info = await get_uploader_info(services.http_client, mid)
                up_name = info.name if info else f"UP主_{mid}"

    yield event.plain_result(f"📺 正在调取 【{up_name}】 主页投稿第 {target_page} 页...")
    vlist = await fetch_space_videos_by_page(services, mid, page=target_page)
    if not vlist:
        yield event.plain_result(f"📋 【{up_name}】 第 {target_page} 页没有任何公开投稿。")
        return

    SPACE_PAGINATION_CACHE[origin] = {
        "mode": "space",
        "mid": mid,
        "up_name": up_name,
        "current_page": target_page,
        "video_map": {},
    }

    html_buffer = [
        f"## 📺 【{up_name}】的视频点播单 (第 {target_page} 页)",
        "💡 *看视频总结：在群内发送 `视频 [序号]` (例如: `视频 1`)*",
        "🧭 *连续翻页：`下一页` / `上一页` | 定向跳页：`跳页 [页码]`*",
        "",
        '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 15px; font-family: sans-serif;">',
    ]

    for idx, v in enumerate(vlist, start=1):
        SPACE_PAGINATION_CACHE[origin]["video_map"][str(idx)] = v.get("bvid", "")
        html_buffer.append(
            f'<div style="position: relative; background: rgba(128,128,128,0.05); border-radius: 8px; border: 1px solid rgba(128,128,128,0.15); padding: 8px; display: flex; flex-direction: column; justify-content: space-between;">'
            f'  <div style="position: absolute; top: 12px; left: 12px; background: #00a1d6; color: white; padding: 3px 9px; border-radius: 6px; font-weight: bold; font-size: 14px; z-index: 10;">{idx}</div>'
            f'  <img src="{v.get("pic")}" style="width: 100%; aspect-ratio: 16/10; border-radius: 6px; object-fit: cover;" referrerpolicy="no-referrer" />'
            f'  <p style="margin: 6px 0 2px 0; font-size: 13px; font-weight: bold; line-height: 1.3; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; height: 34px; color: var(--text-color);">{v.get("title")}</p>'
            f"</div>"
        )
    html_buffer.append("</div>")
    try:
        components = await render_note_components(services, "\n".join(html_buffer))
        if components:
            yield event.chain_result(components)
            return
    except Exception:
        pass
    yield event.plain_result(
        f"📺 【{up_name}】第 {target_page} 页:\n"
        + "\n".join([f" [{i}] {v.get('title')}" for i, v in enumerate(vlist, start=1)])
    )


async def handle_space_page_jump(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    args = parse_command_args(getattr(event, "message_str", "") or "").strip()
    num = re.search(r"\d+", args)
    if not num:
        yield event.plain_result("⚠️ 请附带要跳转的纯数字页码（例如：`跳页 3`）")
        return
    origin = getattr(event, "unified_msg_origin", "")
    if origin not in SPACE_PAGINATION_CACHE:
        yield event.plain_result("⚠️ 终端未激活，请先发送 `/主页视频 <UP主>`")
        return
    cache = SPACE_PAGINATION_CACHE[origin]
    event.message_str = f"/主页视频 {cache['mid']} {num.group()}"
    async for resp in handle_space_view(services, event):
        yield resp


async def handle_space_page_turn(services: BiliVideoServices, event: object, direction: int) -> AsyncIterator[object]:
    origin = getattr(event, "unified_msg_origin", "")
    if origin not in SPACE_PAGINATION_CACHE:
        yield event.plain_result("⚠️ 请先发送一次 `/主页视频` 激活终端。")
        return
    cache = SPACE_PAGINATION_CACHE[origin]
    next_page = cache["current_page"] + direction
    if next_page < 1:
        yield event.plain_result(f"◀️ 提示：当前已是 【{cache['up_name']}】 的第 1 页。")
        return
    event.message_str = f"/主页视频 {cache['mid']} {next_page}"
    async for resp in handle_space_view(services, event):
        yield resp


async def handle_visit_homepage(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not is_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return
    clean_str = parse_command_args(getattr(event, "message_str", "") or "").strip()
    origin = getattr(event, "unified_msg_origin", "")
    # 先尝试序号解析
    resolved = await resolve_subscription_index(services, origin, clean_str)
    if resolved:
        mid, up_name = resolved
        url = f"https://space.bilibili.com/{mid}"
        yield event.plain_result(f"🔗 【{up_name}】的B站主页直达链接：\n{url}")
        return

    # 原有逻辑
    if clean_str.isdigit():
        mid = clean_str
    else:
        mid = extract_uid(clean_str)
        if not mid:
            uploader = await search_uploader_by_name(services.http_client, clean_str)
            if uploader is None:
                yield event.plain_result("❌ 无法获取目标UP主主页链接。用法: /访问主页 <序号X/名称/UID>")
                return
            mid = uploader.mid
            up_name = uploader.name
        else:
            info = await get_uploader_info(services.http_client, mid)
            up_name = info.name if info else f"UP主_{mid}"
    url = f"https://space.bilibili.com/{mid}"
    yield event.plain_result(f"🔗 【{up_name}】的B站主页直达链接：\n{url}")


async def handle_video_select(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not is_manual_summary_allowed(event, config=services.config):
        yield event.plain_result("⛔ 你没有权限使用此插件")
        return
    clean_str = parse_command_args(getattr(event, "message_str", "") or "").strip()
    if not clean_str:
        yield event.plain_result("❌ 请提供要查看的视频序号\n用法: 视频 <序号> [总结/不总结]")
        return

    body_parts = clean_str.split()
    if len(body_parts) > 1 and body_parts[-1] in ["总结", "不总结"]:
        num_part = body_parts[0]
    else:
        num_part = clean_str

    origin = getattr(event, "unified_msg_origin", "")
    if origin not in SPACE_PAGINATION_CACHE or not SPACE_PAGINATION_CACHE[origin].get("video_map"):
        yield event.plain_result("⚠️ 还没有加载任何点播单，请先发送 `/主页视频 <UP>`")
        return
    vmap = SPACE_PAGINATION_CACHE[origin]["video_map"]
    if num_part not in vmap:
        yield event.plain_result(f"❌ 错误的序号！当前页面有效的序号范围是 1 到 {len(vmap)}")
        return
    bvid = vmap[num_part]
    yield event.plain_result(
        f"🎬 已为你锁定了当前页面的第 [{num_part}] 号视频 (BV号: {bvid})\n⏳ 正在全速进行解析与长图总结..."
    )

    try:
        note = await services.inflight.run(
            bvid, lambda: services.orchestrator.generate(f"https://www.bilibili.com/video/{bvid}")
        )
    except BiliVideoError as exc:
        yield event.plain_result(exc.user_message)
        return
    components = await render_note_components(services, note.markdown)
    async for resp in yield_note_response(services, event, components, video_info=note.video_info):
        yield resp

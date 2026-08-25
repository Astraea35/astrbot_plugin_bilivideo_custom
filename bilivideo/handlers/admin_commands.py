"""管理员全局命令：全局订阅列表、全局删除、全局添加订阅"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..access.control import is_admin, is_auto_summary_allowed
from ..api.endpoints import get_latest_videos, get_uploader_info
from ..core.utils import is_valid_umo
from ..services import BiliVideoServices
from ._utils import parse_sub_args, parse_command_args, build_filter_desc


async def handle_global_list(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """/bili_global_list - 查看所有会话的订阅"""
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    all_subs = await services.subscription_manager.get_all_subscriptions()
    if not all_subs:
        yield event.plain_result("📋 没有任何会话订阅过。")
        return

    lines = ["📋 全局订阅列表:", "━━━━━━━━━━━━━━━━━━━"]
    for origin, subs in all_subs.items():
        lines.append(f"📌 {origin}:")
        for sub in subs:
            strategy = "自动总结" if sub.auto_summary else "仅推送"
            filters = []
            if sub.filter_types:
                filters.append(f"过滤:{','.join(sub.filter_types)}")
            if sub.filter_regex:
                filters.append(f"正则:{','.join(sub.filter_regex)}")
            if sub.live_atall:
                filters.append("@全体")
            filter_str = f" [{', '.join(filters)}]" if filters else ""
            lines.append(f"  - {sub.name} (UID:{sub.mid}) | {strategy}{filter_str}")
        lines.append("")

    yield event.plain_result("\n".join(lines))


async def handle_global_del(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """/bili_global_del <UMO> - 删除指定会话的所有订阅"""
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    args = parse_command_args(getattr(event, "message_str", "") or "")
    if not args or not is_valid_umo(args):
        yield event.plain_result(
            "❌ 请提供有效的 UMO\n"
            "用法: /bili_global_del <UMO>\n"
            "使用 /sid 指令查看当前会话的 UMO"
        )
        return

    # 查找匹配的会话
    all_subs = await services.subscription_manager.get_all_subscriptions()
    target_origins = [o for o in all_subs.keys() if o == args or o.endswith(f":{args}")]

    if not target_origins:
        yield event.plain_result(f"⚠️ 未找到 UMO 为 {args} 的订阅")
        return

    removed_count = 0
    for origin in target_origins:
        subs = await services.subscription_manager.get_subscriptions(origin)
        for sub in subs:
            if await services.subscription_manager.remove_subscription(origin, sub.mid):
                removed_count += 1

    yield event.plain_result(f"✅ 已删除 {removed_count} 个订阅 (UMO: {args})")


async def handle_global_sub(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """/bili_global_sub <UMO> <UID> [过滤器...] - 为指定会话添加订阅"""
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    args = parse_command_args(getattr(event, "message_str", "") or "")
    if not args:
        yield event.plain_result(
            "❌ 用法: /bili_global_sub <UMO> <UID> [过滤器...]\n"
            "示例: /bili_global_sub aiocqhttp:GroupMessage:123456 789012 lottery video"
        )
        return

    parts = args.split()
    if len(parts) < 2:
        yield event.plain_result("❌ 请提供 UMO 和 UID")
        return

    umo = parts[0]
    uid_str = parts[1]
    filter_input = " ".join(parts[2:]) if len(parts) > 2 else ""

    if not is_valid_umo(umo):
        yield event.plain_result("❌ UMO 格式无效")
        return

    if not uid_str.isdigit():
        yield event.plain_result("❌ UID 必须是数字")
        return

    uid_int = int(uid_str)
    filter_types, filter_regex, live_atall = parse_sub_args(filter_input)

    # 获取UP主信息
    info = await get_uploader_info(services.http_client, uid_str)
    up_name = info.name if info else f"UP主_{uid_str}"

    # 添加订阅
    auto_summary = is_auto_summary_allowed(umo, config=services.config)
    status = await services.subscription_manager.add_subscription(
        origin=umo,
        mid=uid_str,
        name=up_name,
        auto_summary=auto_summary,
        filter_types=filter_types,
        filter_regex=filter_regex,
        live_atall=live_atall,
    )

    if status == 1:
        # 初始化 last_bvid
        videos = await get_latest_videos(services.http_client, uid_str, count=1)
        if videos:
            await services.subscription_manager.update_last_video(umo, uid_str, videos[0].bvid)
        filter_desc = build_filter_desc(filter_types, filter_regex, live_atall)
        yield event.plain_result(f"✅ 已为 {umo} 添加订阅 {up_name} (UID:{uid_str})\n{filter_desc}")
    elif status == 2:
        yield event.plain_result(f"🔄 已更新 {umo} 对 {up_name} 的订阅配置")
    else:
        yield event.plain_result(f"ℹ️ {umo} 已订阅 {up_name}，配置未变动")

"""Admin commands for subscription conversation management."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..access.control import is_admin
from ..services import BiliVideoServices
from ._utils import parse_command_args


def _display_conversation(origin: str) -> str:
    """Present a unified origin without exposing its subscribed UPs."""
    parts = [part for part in origin.split(":") if part]
    kind = next((part.casefold() for part in parts if "message" in part.casefold()), "")
    identifier = parts[-1] if parts else origin
    if "group" in kind:
        return f"群聊 {identifier}"
    if any(marker in kind for marker in ("friend", "private", "person")):
        return f"私聊 {identifier}"
    return f"会话 {identifier}"


async def handle_list_push(
    services: BiliVideoServices, event: object
) -> AsyncIterator[object]:
    """List conversations that contain subscriptions, not their UP details."""
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")  # type: ignore[attr-defined]
        return

    subscriptions = await services.subscription_manager.get_all_subscriptions()
    if not subscriptions:
        yield event.plain_result("🔍 当前没有任何群聊或私聊订阅")  # type: ignore[attr-defined]
        return

    lines = ["🔍 当前存在订阅的会话:", "━━━━━━━━━━━━━━━━━━━"]
    for index, origin in enumerate(subscriptions, start=1):
        lines.append(f"  {index}. {_display_conversation(origin)}")
    lines.append("\n使用 /移除推送 <序号> 可删除指定会话的全部订阅")
    yield event.plain_result("\n".join(lines))  # type: ignore[attr-defined]


async def handle_remove_push(
    services: BiliVideoServices, event: object
) -> AsyncIterator[object]:
    """Delete every subscription associated with a listed conversation."""
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")  # type: ignore[attr-defined]
        return

    args = parse_command_args(getattr(event, "message_str", "") or "")
    if not args or not args.isdigit():
        yield event.plain_result("❌ 请提供 /推送列表 中的会话序号\n用法: /移除推送 <序号>")  # type: ignore[attr-defined]
        return

    subscriptions = await services.subscription_manager.get_all_subscriptions()
    origins = list(subscriptions)
    index = int(args)
    if not 1 <= index <= len(origins):
        yield event.plain_result("⚠️ 会话序号不存在,请先执行 /推送列表")  # type: ignore[attr-defined]
        return

    origin = origins[index - 1]
    for sub in subscriptions[origin]:
        await services.subscription_manager.remove_subscription(origin, sub.mid)
    yield event.plain_result(  # type: ignore[attr-defined]
        f"✅ 已删除 {_display_conversation(origin)} 的全部订阅"
    )

"""Confirm QQ forward contents rather than trusting a successful send call."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def _response_data(response: Any) -> dict:
    if not isinstance(response, dict):
        raise RuntimeError("QQ 接口未返回有效结果")
    if response.get("status") == "failed" or response.get("retcode", 0) != 0:
        raise RuntimeError("QQ 接口调用失败")
    data = response.get("data", response)
    if not isinstance(data, dict):
        raise RuntimeError("QQ 接口返回数据为空")
    return data


def _forward_id(message: dict) -> str:
    for component in message.get("message", []):
        data = component.get("data", {})
        if component.get("type") == "forward":
            return str(data.get("id") or "")
        if component.get("type") == "json":
            card = data.get("data", {})
            if isinstance(card, str):
                card = json.loads(card)
            return str(card.get("meta", {}).get("detail", {}).get("resid") or "")
    return ""


def _forward_complete(expected: list[dict], actual: list[dict]) -> bool:
    if len(expected) != len(actual):
        return False
    for sent, received in zip(expected, actual, strict=True):
        content = sent.get("data", {}).get("content", [])
        returned = received.get("message", received.get("content", []))
        # A backend may retain the text label while silently omitting its image.
        if sum(item.get("type") == "image" for item in returned) < sum(
            item.get("type") == "image" for item in content
        ):
            return False
        # QQ can coalesce adjacent text segments into one segment.
        if any(item.get("type") == "text" for item in content) and not any(
            item.get("type") == "text" for item in returned
        ):
            return False
    return True


async def try_send_qq_forward(context: Any, origin: str, forward: Any) -> bool:
    """Return False for other platforms; QQ failures propagate to the caller."""
    get_platform = getattr(context, "get_platform_inst", None)
    parts = origin.split(":", 2)
    if not callable(get_platform) or len(parts) != 3:
        return False
    platform = get_platform(parts[0])
    if platform is None or platform.meta().name != "aiocqhttp":
        return False
    if not isinstance(getattr(forward, "nodes", None), list):
        return False
    if parts[1] not in ("GroupMessage", "FriendMessage", "PrivateMessage"):
        raise RuntimeError("不支持的 QQ 会话类型")
    client = platform.get_client()
    payload = await forward.to_dict()
    expected = payload.get("messages", [])
    if not expected:
        raise RuntimeError("QQ 合并转发节点为空")
    group = parts[1] == "GroupMessage"
    payload["group_id" if group else "user_id"] = parts[2]
    response = _response_data(await client.call_action(
        "send_group_forward_msg" if group else "send_private_forward_msg", **payload
    ))
    message_id = response.get("message_id")
    reference = response.get("res_id") or response.get("forward_id")
    if not message_id and not reference:
        raise RuntimeError("QQ 合并转发缺少消息 ID，无法确认完整投递")
    actual: list[dict] = []
    for delay in (0, 0.75, 1.5):
        if delay:
            await asyncio.sleep(delay)
        if not reference:
            message = _response_data(await client.call_action("get_msg", message_id=message_id))
            reference = _forward_id(message)
        if reference:
            result = _response_data(await client.call_action("get_forward_msg", message_id=reference))
            actual = result.get("messages", [])
            if _forward_complete(expected, actual):
                return True
    raise RuntimeError(f"QQ 合并转发不完整：预期 {len(expected)} 条，回读 {len(actual)} 条或图片缺失")


async def send_message_checked(context: Any, origin: str, chain: Any) -> None:
    if len(chain.chain) == 1 and await try_send_qq_forward(context, origin, chain.chain[0]):
        return
    if await context.send_message(origin, chain) is False:
        raise RuntimeError("消息平台不存在，未发送消息")

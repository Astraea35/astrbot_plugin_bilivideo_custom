"""Subscription conversation management command tests."""

from __future__ import annotations

import pytest

from bilivideo.handlers.push_target import handle_list_push, handle_remove_push
from bilivideo.subscription.manager import SubscriptionManager


class _PermissionManager:
    async def is_admin(self, _event: object) -> bool:
        return True


class _Context:
    permission_manager = _PermissionManager()


class _Services:
    def __init__(self, manager: SubscriptionManager) -> None:
        self.subscription_manager = manager
        self.astrbot_context = _Context()


class _Event:
    def __init__(self, message_str: str) -> None:
        self.message_str = message_str

    def plain_result(self, text: str) -> str:
        return text


async def _responses(generator):
    return [response async for response in generator]


@pytest.mark.asyncio
async def test_list_shows_subscription_conversations_only(tmp_path) -> None:
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("demo:GroupMessage:100", "1", "UP A")
    await manager.add_subscription("demo:FriendMessage:200", "2", "UP B")

    result = await _responses(handle_list_push(_Services(manager), _Event("/推送列表")))

    assert "群聊 100" in result[0]
    assert "私聊 200" in result[0]
    assert "UP A" not in result[0]
    assert "UP B" not in result[0]


@pytest.mark.asyncio
async def test_remove_deletes_every_subscription_for_selected_conversation(tmp_path) -> None:
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("demo:GroupMessage:100", "1", "UP A")
    await manager.add_subscription("demo:GroupMessage:100", "2", "UP B")
    await manager.add_subscription("demo:FriendMessage:200", "3", "UP C")

    result = await _responses(handle_remove_push(_Services(manager), _Event("/移除推送 1")))

    assert "已删除 群聊 100 的全部订阅" in result[0]
    assert await manager.get_subscriptions("demo:GroupMessage:100") == []
    assert len(await manager.get_subscriptions("demo:FriendMessage:200")) == 1

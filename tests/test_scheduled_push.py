"""Scheduled video push delivery tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bilivideo.core.types import LatestVideo
from bilivideo.handlers import scheduled_push
from bilivideo.subscription.manager import Subscription, SubscriptionManager


class _MessageChain:
    def __init__(self, *, chain: list[object]) -> None:
        self.chain = chain


class _Context:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[tuple[str, _MessageChain]] = []

    async def send_message(self, target: str, message: _MessageChain) -> None:
        if self.fail:
            raise RuntimeError("adapter unavailable")
        self.sent.append((target, message))


def _services(manager: SubscriptionManager, context: _Context) -> SimpleNamespace:
    return SimpleNamespace(
        astrbot_context=context,
        subscription_manager=manager,
        logger=SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None),
    )


@pytest.mark.asyncio
async def test_failed_delivery_keeps_video_pending(monkeypatch, tmp_path) -> None:
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("origin", "42", "UP", sub_types=["视频"])
    await manager.update_last_video("origin", "42", "BVold")
    sub = (await manager.get_subscriptions("origin"))[0]
    services = _services(manager, _Context(fail=True))

    monkeypatch.setattr(scheduled_push, "MessageChain", _MessageChain)
    monkeypatch.setattr(
        scheduled_push,
        "get_latest_videos",
        lambda *_args, **_kwargs: _latest_video(),
    )
    monkeypatch.setattr(scheduled_push, "_build_chain", lambda *_args: _chain())

    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    assert (await manager.get_subscription("origin", "42")).last_bvid == "BVold"


@pytest.mark.asyncio
async def test_successful_delivery_marks_video_sent(monkeypatch, tmp_path) -> None:
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("origin", "42", "UP", sub_types=["视频"])
    await manager.update_last_video("origin", "42", "BVold")
    sub = (await manager.get_subscriptions("origin"))[0]
    context = _Context(fail=False)
    services = _services(manager, context)

    monkeypatch.setattr(scheduled_push, "MessageChain", _MessageChain)
    monkeypatch.setattr(
        scheduled_push,
        "get_latest_videos",
        lambda *_args, **_kwargs: _latest_video(),
    )
    monkeypatch.setattr(scheduled_push, "_build_chain", lambda *_args: _chain())

    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    assert len(context.sent) == 1
    assert (await manager.get_subscription("origin", "42")).last_bvid == "BVnew"


async def _latest_video() -> list[LatestVideo]:
    return [LatestVideo(bvid="BVnew", title="new video")]


async def _chain() -> list[object]:
    return [object()]

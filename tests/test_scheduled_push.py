"""Scheduled video push delivery tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import BiliVideoError, DownloadError
from bilivideo.core.types import LatestVideo
from bilivideo.handlers import scheduled_push
from bilivideo.subscription.manager import Subscription, SubscriptionManager


class _MessageChain:
    def __init__(self, chain: list[object]) -> None:
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
        http_client=object(),
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
    monkeypatch.setattr(scheduled_push, "Plain", object)
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
    monkeypatch.setattr(scheduled_push, "Plain", object)
    monkeypatch.setattr(
        scheduled_push,
        "get_latest_videos",
        lambda *_args, **_kwargs: _latest_video(),
    )
    monkeypatch.setattr(scheduled_push, "_build_chain", lambda *_args: _chain())

    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    assert len(context.sent) == 1
    assert (await manager.get_subscription("origin", "42")).last_bvid == "BVnew"


@pytest.mark.asyncio
async def test_incomplete_forward_keeps_video_pending(monkeypatch, tmp_path) -> None:
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("origin", "42", "UP", sub_types=["视频"])
    await manager.update_last_video("origin", "42", "BVold")
    sub = (await manager.get_subscriptions("origin"))[0]
    services = _services(manager, _Context(fail=False))
    monkeypatch.setattr(scheduled_push, "MessageChain", _MessageChain)
    monkeypatch.setattr(scheduled_push, "get_latest_videos", AsyncMock(return_value=await _latest_video()))
    monkeypatch.setattr(scheduled_push, "_build_chain", AsyncMock(return_value=[object()]))
    monkeypatch.setattr(
        scheduled_push, "send_message_checked", AsyncMock(side_effect=RuntimeError("QQ 合并转发不完整"))
    )
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    assert (await manager.get_subscription("origin", "42")).last_bvid == "BVold"


async def _latest_video() -> list[LatestVideo]:
    return [LatestVideo(bvid="BVnew", title="new video")]


async def _chain() -> list[object]:
    return [object()]


class _Plain:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture
async def summary_push(monkeypatch, tmp_path):
    manager = SubscriptionManager(str(tmp_path))
    await manager.add_subscription("origin", "42", "UP", sub_types=["视频"])
    await manager.update_last_video("origin", "42", "BVold")
    sub = (await manager.get_subscriptions("origin"))[0]
    context = _Context(fail=False)
    services = _services(manager, context)
    services.config = PluginConfig(auto_push_summary=True, auto_summary_mode="all")
    services.orchestrator = SimpleNamespace(generate=AsyncMock())
    note = SimpleNamespace(markdown="正确的视频总结", video_info=None)

    async def wait_without_sending(_delay):
        assert context.sent == []
        assert (await manager.get_subscription("origin", "42")).last_bvid == "BVold"

    sleep = AsyncMock(side_effect=wait_without_sending)
    render = AsyncMock(return_value=note.markdown)
    monkeypatch.setattr(scheduled_push, "Plain", _Plain)
    monkeypatch.setattr(scheduled_push, "MessageChain", _MessageChain)
    monkeypatch.setattr(scheduled_push, "get_latest_videos", AsyncMock(return_value=await _latest_video()))
    monkeypatch.setattr(scheduled_push, "render_note_components", render)
    monkeypatch.setattr(scheduled_push.asyncio, "sleep", sleep)
    return services, sub, context, sleep, render, note


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [0, 1, 3])
@pytest.mark.parametrize("message", ["❌ 总结与视频内容不符", "❌ AI 总结超时"])
async def test_summary_retries_then_sends_only_success(summary_push, failures, message):
    services, sub, context, sleep, render, note = summary_push
    services.orchestrator.generate.side_effect = [
        *[BiliVideoError("summary failed", user_message=message) for _ in range(failures)],
        note,
    ]

    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    assert services.orchestrator.generate.await_args_list == [
        call("https://www.bilibili.com/video/BVnew")
    ] * (failures + 1)
    assert sleep.await_args_list == [call(delay) for delay in (5, 10, 15)[:failures]]
    render.assert_awaited_once()
    assert len(context.sent) == 1
    text = context.sent[0][1].chain[0].text
    assert note.markdown in text
    assert message not in text
    assert (await services.subscription_manager.get_subscription("origin", "42")).last_bvid == "BVnew"


@pytest.mark.asyncio
async def test_summary_exhausts_retries_before_sending_one_error(summary_push):
    services, sub, context, sleep, render, _note = summary_push
    messages = ["❌ 总结与视频内容不符", "❌ AI 总结超时", "❌ AI 返回内容为空", "❌ AI 服务暂时不可用"]
    services.orchestrator.generate.side_effect = [
        BiliVideoError("summary failed", user_message=message) for message in messages
    ]

    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    assert services.orchestrator.generate.await_count == 4
    assert sleep.await_args_list == [call(5), call(10), call(15)]
    render.assert_not_awaited()
    assert len(context.sent) == 1
    text = context.sent[0][1].chain[0].text
    assert messages[-1] in text
    assert all(message not in text for message in messages[:-1])
    assert "已自动重试 3 次" in text
    assert "https://www.bilibili.com/video/BVnew" in text
    sub = await services.subscription_manager.get_subscription("origin", "42")
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    assert len(context.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during", ["generation", "backoff"])
async def test_cancelled_summary_does_not_retry_or_send(summary_push, cancel_during):
    services, sub, context, sleep, render, _note = summary_push
    services.orchestrator.generate.side_effect = (
        asyncio.CancelledError() if cancel_during == "generation" else BiliVideoError("failed")
    )
    if cancel_during == "backoff":
        sleep.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await scheduled_push.push_callback(services, "origin", sub)

    services.orchestrator.generate.assert_awaited_once()
    assert sleep.await_count == int(cancel_during == "backoff")
    render.assert_not_awaited()
    assert context.sent == []
    assert (await services.subscription_manager.get_subscription("origin", "42")).last_bvid == "BVold"


@pytest.mark.asyncio
async def test_reminder_only_subscription_skips_summary_retries(summary_push):
    services, sub, context, sleep, render, _note = summary_push
    sub.auto_summary = False

    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    services.orchestrator.generate.assert_not_awaited()
    sleep.assert_not_awaited()
    render.assert_not_awaited()
    assert len(context.sent) == 1
    assert "https://www.bilibili.com/video/BVnew" in context.sent[0][1].chain[0].text


@pytest.mark.asyncio
async def test_network_failure_is_persisted_then_recovers_without_duplicate_notice(summary_push, monkeypatch):
    services, sub, context, sleep, render, note = summary_push
    monkeypatch.setattr(scheduled_push.time, "time", lambda: 1000)
    services.orchestrator.generate.side_effect = DownloadError("TLS EOF", retryable=True)
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    sub = await services.subscription_manager.get_subscription("origin", "42")
    assert sub.last_bvid == "BVold"
    assert sub.pending_summary_bvid == "BVnew"
    assert sub.summary_retry_count == 1
    assert sub.summary_retry_at == 1300
    assert len(context.sent) == 1
    assert "稍后" in context.sent[0][1].chain[0].text
    services.orchestrator.generate.reset_mock()
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    services.orchestrator.generate.assert_not_awaited()

    # A new manager simulates plugin reload; recovery must survive it.
    restored = SubscriptionManager(str(services.subscription_manager._store._path.parent))
    services.subscription_manager = restored
    sub = await restored.get_subscription("origin", "42")
    monkeypatch.setattr(scheduled_push.time, "time", lambda: 1300)
    services.orchestrator.generate.side_effect = None
    services.orchestrator.generate.return_value = note
    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    assert len(context.sent) == 2
    assert note.markdown in context.sent[-1][1].chain[0].text
    stored = await restored.get_subscription("origin", "42")
    assert stored.last_bvid == "BVnew"
    assert stored.pending_summary_bvid == ""
    assert stored.summary_retry_count == 0


@pytest.mark.asyncio
async def test_network_recovery_has_three_delayed_rounds_and_only_one_error_notice(summary_push, monkeypatch):
    services, sub, context, sleep, render, note = summary_push
    clock = [1000]
    monkeypatch.setattr(scheduled_push.time, "time", lambda: clock[0])
    services.orchestrator.generate.side_effect = DownloadError("TLS EOF", retryable=True)
    await scheduled_push.push_callback(services, "origin", sub)
    for count, deadline in [(1, 1300), (2, 2200), (3, 5800)]:
        sub = await services.subscription_manager.get_subscription("origin", "42")
        assert sub.summary_retry_count == count
        assert sub.summary_retry_at == deadline
        clock[0] = deadline
        # The fixture's immediate-backoff check expects no messages yet.
        sleep.side_effect = None
        assert await scheduled_push.push_callback(services, "origin", sub) == 0
        assert len(context.sent) == 1
    sub = await services.subscription_manager.get_subscription("origin", "42")
    assert sub.summary_retry_count == 4
    assert sub.last_bvid == "BVold"
    before = services.orchestrator.generate.await_count
    clock[0] = 999999
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    assert services.orchestrator.generate.await_count == before


@pytest.mark.asyncio
async def test_failed_error_notification_does_not_arm_recovery_or_mark_sent(summary_push):
    services, sub, context, sleep, render, note = summary_push
    services.orchestrator.generate.side_effect = DownloadError("TLS EOF", retryable=True)
    context.fail = True
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    stored = await services.subscription_manager.get_subscription("origin", "42")
    assert stored.last_bvid == "BVold"
    assert stored.pending_summary_bvid == ""


@pytest.mark.asyncio
async def test_newer_upload_is_not_blocked_by_previous_summary_backoff(summary_push, monkeypatch):
    services, sub, context, sleep, render, note = summary_push
    await services.subscription_manager.update_summary_retry("origin", "42", "BVfailed", 1, 999999)
    sub = await services.subscription_manager.get_subscription("origin", "42")
    services.orchestrator.generate.return_value = note
    assert await scheduled_push.push_callback(services, "origin", sub) == 1
    stored = await services.subscription_manager.get_subscription("origin", "42")
    assert stored.last_bvid == "BVnew"
    assert stored.pending_summary_bvid == ""


@pytest.mark.asyncio
async def test_missing_platform_skips_queries_summary_and_delivery(summary_push, monkeypatch):
    services, sub, context, sleep, render, note = summary_push
    context.get_platform_inst = lambda _name: None
    latest = AsyncMock()
    monkeypatch.setattr(scheduled_push, "get_latest_videos", latest)
    assert await scheduled_push.push_callback(services, "origin", sub) == 0
    latest.assert_not_awaited()
    services.orchestrator.generate.assert_not_awaited()
    assert context.sent == []
    stored = await services.subscription_manager.get_subscription("origin", "42")
    assert stored.last_bvid == "BVold"

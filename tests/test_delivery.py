from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bilivideo.messaging import delivery


def _setup(*, group=False, returned=None):
    content = [{"type": "text", "data": {"text": "summary"}}, {"type": "image", "data": {"file": "base64://test"}}]
    forward = SimpleNamespace(nodes=[object()], to_dict=AsyncMock(return_value={
        "messages": [{"type": "node", "data": {"content": content}}],
    }))
    actual = [{"message": content}] if returned is None else returned
    client = SimpleNamespace(call_action=AsyncMock(side_effect=[
        {"message_id": 42}, {"message": [{"type": "forward", "data": {"id": "res"}}]},
        {"messages": actual}, {"messages": actual}, {"messages": actual},
    ]))
    platform = SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: client)
    context = SimpleNamespace(get_platform_inst=lambda _: platform, send_message=AsyncMock())
    origin = "qq:GroupMessage:12" if group else "qq:FriendMessage:34"
    return forward, client, context, origin


@pytest.mark.parametrize("group", [False, True])
async def test_forward_requires_readback_and_uses_exact_conversation(group):
    forward, client, context, origin = _setup(group=group)
    chain = SimpleNamespace(chain=[forward])
    await delivery.send_message_checked(context, origin, chain)
    sent = client.call_action.await_args_list[0]
    assert sent.args == ("send_group_forward_msg" if group else "send_private_forward_msg",)
    assert sent.kwargs["group_id" if group else "user_id"] == ("12" if group else "34")
    assert client.call_action.await_args_list[-1] == call("get_forward_msg", message_id="res")
    context.send_message.assert_not_awaited()


@pytest.mark.parametrize("returned", [[], [{"message": [{"type": "text", "data": {"text": "label"}}]}]])
async def test_success_response_with_missing_node_or_image_is_failure(monkeypatch, returned):
    forward, client, context, origin = _setup(returned=returned)
    monkeypatch.setattr(delivery.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match="不完整"):
        await delivery.send_message_checked(context, origin, SimpleNamespace(chain=[forward]))
    assert sum(args.args[0].startswith("send_") for args in client.call_action.await_args_list) == 1
    context.send_message.assert_not_awaited()


async def test_forward_readback_can_be_temporarily_empty(monkeypatch):
    forward, client, context, origin = _setup()
    responses = client.call_action.side_effect
    responses = list(responses)
    complete = responses[2]
    client.call_action.side_effect = [*responses[:2], {"messages": []}, complete]
    sleep = AsyncMock()
    monkeypatch.setattr(delivery.asyncio, "sleep", sleep)
    assert await delivery.try_send_qq_forward(context, origin, forward)
    sleep.assert_awaited_once_with(0.75)


async def test_missing_platform_does_not_mark_a_message_sent():
    context = SimpleNamespace(send_message=AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="平台不存在"):
        await delivery.send_message_checked(
            context, "tg:FriendMessage:123", SimpleNamespace(chain=[object()])
        )


async def test_other_platform_uses_normal_context_send():
    forward, _, context, origin = _setup()
    context.get_platform_inst = lambda _: SimpleNamespace(meta=lambda: SimpleNamespace(name="telegram"))
    await delivery.send_message_checked(context, origin, SimpleNamespace(chain=[forward]))
    context.send_message.assert_awaited_once()


async def test_missing_send_id_cannot_confirm_forward():
    forward, client, context, origin = _setup()
    client.call_action.side_effect = [{"status": "ok", "data": {}}]
    with pytest.raises(RuntimeError, match="缺少消息 ID"):
        await delivery.try_send_qq_forward(context, origin, forward)


def test_adjacent_text_segments_can_be_coalesced_by_qq():
    expected = [{"data": {"content": [{"type": "text"}, {"type": "text"}]}}]
    actual = [{"message": [{"type": "text"}]}]
    assert delivery._forward_complete(expected, actual)

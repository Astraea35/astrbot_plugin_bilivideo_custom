from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.core.utils import is_telegram_target
from bilivideo.handlers._render_helper import render_note_components
from bilivideo.handlers._send_helper import yield_note_response


@pytest.mark.asyncio
async def test_telegram_summary_skips_image_even_when_image_is_forced():
    renderer = SimpleNamespace(render=AsyncMock())
    services = SimpleNamespace(
        config=SimpleNamespace(output_image=True),
        astrbot_context=None, renderer=renderer,
    )
    event = SimpleNamespace(get_platform_name=lambda: "telegram")
    output = await render_note_components(
        services, "**标题**\n正文", force_image=True, event=event,
    )
    assert output == "**标题**\n正文"
    renderer.render.assert_not_awaited()


def test_proactive_telegram_is_resolved_by_actual_platform():
    platform = SimpleNamespace(meta=lambda: SimpleNamespace(name="telegram"))
    context = SimpleNamespace(get_platform_inst=lambda identifier: platform)
    assert is_telegram_target("private_bot:FriendMessage:123", context)
    assert not is_telegram_target(SimpleNamespace(get_platform_name=lambda: "aiocqhttp"))


@pytest.mark.asyncio
async def test_telegram_does_not_use_qq_forward_messages():
    event = SimpleNamespace(
        get_platform_name=lambda: "telegram",
        plain_result=lambda text: ("text", text),
        chain_result=lambda chain: ("chain", chain),
    )
    services = SimpleNamespace(
        config=SimpleNamespace(enable_forward_message=True),
        astrbot_context=None,
    )
    results = [
        output async for output in yield_note_response(
            services, event, "**完整总结**", meta=object(),
        )
    ]
    assert results == [("text", "**完整总结**")]

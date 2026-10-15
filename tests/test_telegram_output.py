from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.core.utils import is_telegram_target
from bilivideo.core.types import VideoInfo
from bilivideo.core.config import PluginConfig
from bilivideo.handlers import auto_detect
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


@pytest.mark.asyncio
async def test_telegram_summary_keeps_paragraphs_and_source_metadata():
    services = SimpleNamespace(astrbot_context=None)
    event = SimpleNamespace(get_platform_name=lambda: "telegram")
    info = VideoInfo(bvid="BV123", title="测试视频", owner_name="UP主", duration=125)
    output = await render_note_components(
        services, "## 第一节\n\n正文\n\n## 第二节\n\n- 要点",
        video_info=info, event=event,
    )
    text = output[-1].text if isinstance(output, list) else output
    assert "【B站视频总结】" in text
    assert "UP主：`UP主`" in text and "时长：`02:05`" in text
    assert "[测试视频](https://www.bilibili.com/video/BV123)" in text
    assert "> • 要点" in text


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


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,expected_images", [("telegram", 0), ("aiocqhttp", 1)])
async def test_auto_detect_cover_is_only_sent_to_qq(monkeypatch, platform, expected_images):
    from astrbot.api.message_components import Image, Plain

    monkeypatch.setattr(auto_detect, "Image", Image)
    monkeypatch.setattr(auto_detect, "Plain", Plain)
    monkeypatch.setattr(auto_detect, "_resolve_bvid", AsyncMock(return_value="BV123"))
    monkeypatch.setattr(
        auto_detect, "get_video_info",
        AsyncMock(return_value=SimpleNamespace(normalized_pic="https://example.com/cover.jpg")),
    )
    monkeypatch.setattr(auto_detect, "format_video_summary_lines", lambda *args, **kwargs: ["video metadata"])
    services = SimpleNamespace(
        config=PluginConfig(detect_show_cover=True, detect_auto_summary=False),
        enable_miniapp_detect=True,
        http_client=None,
    )
    event = SimpleNamespace(
        message_str="https://www.bilibili.com/video/BV123",
        get_platform_name=lambda: platform,
        chain_result=lambda chain: chain,
    )
    results = [result async for result in auto_detect.handle_auto_detect(services, event)]
    assert len(results) == 1
    assert sum(isinstance(item, Image) for item in results[0]) == expected_images
    assert any(isinstance(item, Plain) for item in results[0])

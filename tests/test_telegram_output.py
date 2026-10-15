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
    text = output[0].text if isinstance(output, list) else output
    assert "正文" in text
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
    text = output[0].text if isinstance(output, list) else output
    assert "📺 **测试视频**" in text
    assert "UP主**：UP主" in text
    assert "时长**：02:05" in text
    assert "第一节" in text
    assert "第二节" in text
    assert "https://www.bilibili.com/video/BV123" in text


@pytest.mark.asyncio
async def test_telegram_summary_includes_featured_comments(monkeypatch):
    from bilivideo.core.types import FeaturedComment, FeaturedCommentReply
    from bilivideo.handlers import _render_helper

    fake_comments = (
        FeaturedComment(
            author_name="热心网友",
            content="这视频真不错，分析到位！",
            like=123,
            replies=(
                FeaturedCommentReply(author_name="小明", content="确实不错", like=10),
            ),
        ),
    )
    monkeypatch.setattr(
        _render_helper, "get_featured_comments", AsyncMock(return_value=fake_comments),
    )
    services = SimpleNamespace(
        config=SimpleNamespace(summary_featured_comment_count=3, summary_comment_reply_count=2),
        http_client=object(),
        astrbot_context=None,
    )
    event = SimpleNamespace(get_platform_name=lambda: "telegram")
    info = VideoInfo(
        bvid="BV123", title="测试视频", owner_name="UP主", duration=125,
        pic="https://i0.hdslb.com/cover.jpg",
    )
    output = await render_note_components(
        services, "## 总结\n\n要点内容", video_info=info, event=event,
    )
    text = output[0].text if isinstance(output, list) else output
    assert "[\u200b](https://i0.hdslb.com/cover.jpg)" in text
    assert "📺 **测试视频**" in text
    assert "💬 **前排精选评论** (热门 1 条)" in text
    assert "👤 **热心网友** (👍 123)" in text
    assert "这视频真不错，分析到位！" in text
    assert "↳ **小明** (👍 10): 确实不错" in text


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

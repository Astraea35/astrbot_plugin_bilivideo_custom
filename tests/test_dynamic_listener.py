"""Dynamic subscription polling selection tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.core.models import RenderPayload
from bilivideo.llm.astrbot_provider import AstrbotProvider
from bilivideo.services.listener import (
    DYNAMIC_DETAIL_TYPES,
    DYNAMIC_SUB_TYPES,
    DynamicListener,
)
from bilivideo.subscription.manager import Subscription


def _subscription(sub_types: list[str]) -> Subscription:
    return Subscription(mid="42", name="UP", sub_types=sub_types)


def test_specific_dynamic_types_request_the_dynamic_feed() -> None:
    listener = object.__new__(DynamicListener)

    for sub_type in DYNAMIC_SUB_TYPES:
        assert listener._needs_dynamic_data(_subscription([sub_type]))

    assert not listener._needs_dynamic_data(_subscription(["视频"]))
    assert not listener._needs_dynamic_data(_subscription(["直播"]))


def test_dynamic_macro_covers_all_non_video_dynamic_types() -> None:
    assert DYNAMIC_DETAIL_TYPES == {
        "图文",
        "转发",
        "专栏",
        "抽奖",
        "转发抽奖",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["", "selected"])
async def test_dynamic_summary_uses_platform_config_without_model_override(selected) -> None:
    generate = AsyncMock(return_value="动态摘要")
    context = SimpleNamespace(llm_generate=generate)
    config = PluginConfig.from_mapping({
        "enable_dynamic_ai_summary": True, "enable_multimodal_dynamic_summary": True,
        "dynamic_summary_provider": selected, "dynamic_summary_model": "removed",
    })
    listener = DynamicListener(
        context, None, None, None, config, None,
        SimpleNamespace(llm=AstrbotProvider(context, "main")),
    )
    payload = RenderPayload(uid="42", title="动态标题", text="正文", image_urls=["image"])
    assert await listener._generate_dynamic_summary(payload, "123") == "动态摘要"
    assert generate.await_args.kwargs == {
        "chat_provider_id": selected or "main",
        "prompt": listener._dynamic_summary_prompt(payload),
        "session_id": "BiliVideo_dynamic_123",
        "image_urls": ["image"],
    }

"""LLM provider factory tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import LLMError
from bilivideo.llm.provider import DisabledLLMProvider, build_provider


@pytest.mark.asyncio
async def test_openai_compatible_without_credentials_is_disabled() -> None:
    cfg = PluginConfig.from_mapping({"llm_provider": "openai_compatible"})
    provider = build_provider(cfg, astrbot_context=None)

    assert isinstance(provider, DisabledLLMProvider)
    with pytest.raises(LLMError) as exc:
        await provider.chat("hello")
    assert "AI 未配置" in exc.value.user_message


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("backup", ["backup", ["backup"]])
async def test_fallback_uses_native_provider_config_without_primary_model(nested: bool, backup) -> None:
    llm_config = {"enable_fallback": True, "backup_provider_id": backup}
    raw_config = {"llm": llm_config} if nested else llm_config
    generate = AsyncMock(side_effect=[RuntimeError("primary unavailable"), "backup result"])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    answer = await provider.chat(
        "summarize",
        session_id="session",
        image_urls=("https://example.com/a.png",),
        model="primary-model",
    )

    assert answer == "backup result"
    assert generate.await_args_list == [
        call(
            chat_provider_id="primary",
            prompt="summarize",
            session_id="session",
            image_urls=["https://example.com/a.png"],
            model="primary-model",
        ),
        call(
            chat_provider_id="backup",
            prompt="summarize",
            session_id="session",
            image_urls=["https://example.com/a.png"],
        ),
    ]


@pytest.mark.asyncio
async def test_primary_success_does_not_call_backup() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": ["backup", "backup-2"]}}
    generate = AsyncMock(return_value="primary result")
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    assert await provider.chat("hello") == "primary result"
    assert generate.await_count == 1
    assert generate.await_args.kwargs["chat_provider_id"] == "primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_fallback,backup_id", [(False, "backup"), (False, ["backup"]), (True, ""), (True, [])])
async def test_disabled_or_unselected_backup_is_not_called(enable_fallback: bool, backup_id) -> None:
    raw_config = {"llm": {"enable_fallback": enable_fallback, "backup_provider_id": backup_id}}
    generate = AsyncMock(side_effect=RuntimeError("primary unavailable"))
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    with pytest.raises(LLMError, match="primary unavailable"):
        await provider.chat("hello")

    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_both_native_providers_failing_preserves_errors() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": "backup"}}
    generate = AsyncMock(side_effect=[RuntimeError("primary unavailable"), RuntimeError("backup unavailable")])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    with pytest.raises(LLMError) as exc:
        await provider.chat("hello")

    assert "primary unavailable" in str(exc.value)
    assert "backup unavailable" in str(exc.value)
    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_cancellation_does_not_call_backup() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": "backup"}}
    generate = AsyncMock(side_effect=asyncio.CancelledError)
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.chat("hello")

    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_multiple_backups_run_in_order_and_stop_on_success() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": [
        "primary", " backup-1 ", "backup-1", "", "backup-2", "backup-3",
    ]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), RuntimeError("backup-1 down"), "success"])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    assert await provider.chat("hello", session_id="session", image_urls=("image",), model="main-model") == "success"
    assert [args.kwargs["chat_provider_id"] for args in generate.await_args_list] == ["primary", "backup-1", "backup-2"]
    for args in generate.await_args_list[1:]:
        assert args.kwargs == {
            "chat_provider_id": args.kwargs["chat_provider_id"],
            "prompt": "hello", "session_id": "session", "image_urls": ["image"],
        }


@pytest.mark.asyncio
async def test_all_backups_failing_preserves_each_error() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": ["backup-1", "backup-2"]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), RuntimeError("first down"), RuntimeError("second down")])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    with pytest.raises(LLMError) as exc:
        await provider.chat("hello")
    for message in ("primary down", "backup-1", "first down", "backup-2", "second down"):
        assert message in str(exc.value)
    assert generate.await_count == 3
    assert "second down" in str(exc.value.__cause__)


@pytest.mark.asyncio
async def test_backup_cancellation_does_not_try_later_models() -> None:
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": ["backup-1", "backup-2"]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), asyncio.CancelledError()])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        provider_id="primary",
        raw_config=raw_config,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.chat("hello")
    assert generate.await_count == 2

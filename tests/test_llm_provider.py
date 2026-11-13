"""LLM provider factory tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import LLMError
from bilivideo.llm.astrbot_provider import AstrbotProvider
from bilivideo.llm.provider import build_provider


def test_selected_platform_is_taken_from_parsed_config_without_raw_config() -> None:
    provider = build_provider(
        PluginConfig(llm_provider="astrbot_custom", astrbot_provider_id="selected"),
        astrbot_context=None,
    )
    assert provider.provider_id == "selected"


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("mode,expected", [("astrbot", ""), ("astrbot_custom", "selected"), ("openai_compatible", "")])
async def test_provider_modes_use_astrbot_including_legacy_config(nested: bool, mode: str, expected: str) -> None:
    block = {
        "llm_provider": mode, "astrbot_provider_id": "selected", "llm_provider_id": "removed",
        "llm_api_base": "https://unused.example/v1", "llm_api_key": "unused-key",
    }
    raw_config = {"llm": block} if nested else block
    chat = AsyncMock(return_value="AstrBot result")
    context = SimpleNamespace(
        get_using_provider=lambda: SimpleNamespace(text_chat=chat),
        llm_generate=chat,
    )
    provider = build_provider(PluginConfig.from_mapping(raw_config), astrbot_context=context, raw_config=raw_config)

    assert isinstance(provider, AstrbotProvider)
    assert provider.provider_id == expected
    assert await provider.chat("hello") == "AstrBot result"
    assert chat.await_count == 1
    assert chat.await_args.kwargs.get("chat_provider_id", "") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("backup", ["backup", ["backup"]])
async def test_fallback_uses_each_native_provider_config(nested: bool, backup) -> None:
    llm_config = {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": backup}
    raw_config = {"llm": llm_config} if nested else llm_config
    generate = AsyncMock(side_effect=[RuntimeError("primary unavailable"), "backup result"])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    answer = await provider.chat(
        "summarize",
        session_id="session",
        image_urls=("https://example.com/a.png",),
    )

    assert answer == "backup result"
    assert generate.await_args_list == [
        call(
            chat_provider_id="primary",
            prompt="summarize",
            session_id="session",
            image_urls=["https://example.com/a.png"],
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
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": ["backup", "backup-2"]}}
    generate = AsyncMock(return_value="primary result")
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    assert await provider.chat("hello") == "primary result"
    assert generate.await_count == 1
    assert generate.await_args.kwargs["chat_provider_id"] == "primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_fallback,backup_id", [(False, "backup"), (False, ["backup"]), (True, ""), (True, [])])
async def test_disabled_or_unselected_backup_is_not_called(enable_fallback: bool, backup_id) -> None:
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": enable_fallback, "backup_provider_id": backup_id}}
    generate = AsyncMock(side_effect=RuntimeError("primary unavailable"))
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    with pytest.raises(LLMError, match="primary unavailable"):
        await provider.chat("hello")

    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_both_native_providers_failing_preserves_errors() -> None:
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": "backup"}}
    generate = AsyncMock(side_effect=[RuntimeError("primary unavailable"), RuntimeError("backup unavailable")])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    with pytest.raises(LLMError) as exc:
        await provider.chat("hello")

    assert "primary unavailable" in str(exc.value)
    assert "backup unavailable" in str(exc.value)
    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_cancellation_does_not_call_backup() -> None:
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": "backup"}}
    generate = AsyncMock(side_effect=asyncio.CancelledError)
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.chat("hello")

    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_multiple_backups_run_in_order_and_stop_on_success() -> None:
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": [
        "primary", " backup-1 ", "backup-1", "", "backup-2", "backup-3",
    ]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), RuntimeError("backup-1 down"), "success"])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    assert await provider.chat("hello", session_id="session", image_urls=("image",)) == "success"
    assert [args.kwargs["chat_provider_id"] for args in generate.await_args_list] == ["primary", "backup-1", "backup-2"]
    for args in generate.await_args_list[1:]:
        assert args.kwargs == {
            "chat_provider_id": args.kwargs["chat_provider_id"],
            "prompt": "hello", "session_id": "session", "image_urls": ["image"],
        }


@pytest.mark.asyncio
async def test_all_backups_failing_preserves_each_error() -> None:
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": ["backup-1", "backup-2"]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), RuntimeError("first down"), RuntimeError("second down")])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
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
    raw_config = {"llm": {"llm_provider": "astrbot_custom", "astrbot_provider_id": "primary", "enable_fallback": True, "backup_provider_id": ["backup-1", "backup-2"]}}
    generate = AsyncMock(side_effect=[RuntimeError("primary down"), asyncio.CancelledError()])
    provider = build_provider(
        PluginConfig.from_mapping(raw_config),
        astrbot_context=SimpleNamespace(llm_generate=generate),
        raw_config=raw_config,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.chat("hello")
    assert generate.await_count == 2

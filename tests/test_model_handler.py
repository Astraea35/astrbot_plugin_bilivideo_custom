"""Tests for the /总结模型 handler (built-in model listing/switching)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.handlers.model import handle_model
from bilivideo.llm.provider import build_provider


class _Event:
    def __init__(self, message_str: str = "") -> None:
        self.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        self.message_str = message_str

    def plain_result(self, text: str) -> str:
        return text


class _Meta:
    def __init__(self, pid: str) -> None:
        self.id = pid


class _Provider:
    def __init__(self, pid: str, model: str) -> None:
        self._pid = pid
        self._model = model

    def meta(self) -> _Meta:
        return _Meta(self._pid)

    def get_model(self) -> str:
        return self._model


class _Context:
    def __init__(self, providers: list[_Provider]) -> None:
        self._providers = providers
        self.permission_manager = SimpleNamespace(is_admin=AsyncMock(return_value=True))

    def get_all_providers(self) -> list[_Provider]:
        return self._providers

class _LLM:
    def __init__(self) -> None:
        self.provider_id = ""


class _Services:
    def __init__(self, config: PluginConfig, providers: list[_Provider]) -> None:
        self.config = config
        self.astrbot_context = _Context(providers)
        self.llm = _LLM()
        self.raw_config = {"llm": {"llm_provider": "astrbot"}}


async def _collect(agen) -> list[str]:
    return [item async for item in agen]


def _astrbot_services(providers: list[_Provider]) -> _Services:
    return _Services(PluginConfig.from_mapping({"llm_provider": "astrbot"}), providers)


@pytest.mark.asyncio
async def test_no_arg_lists_models() -> None:
    services = _astrbot_services([_Provider("openai_gpt4o", "gpt-4o"), _Provider("ds", "deepseek-chat")])
    out = await _collect(handle_model(services, _Event("/总结模型")))  # type: ignore[arg-type]
    assert "可用内置模型" in out[0]
    assert "openai_gpt4o" in out[0]
    assert "ds" in out[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_switch_to_valid_id(wrapped: bool) -> None:
    services = _astrbot_services([_Provider("ds", "deepseek-chat")])
    primary = services.llm
    if wrapped:
        services.llm = SimpleNamespace(primary=primary)
    out = await _collect(handle_model(services, _Event("/总结模型 ds")))  # type: ignore[arg-type]
    assert primary.provider_id == "ds"
    assert services.config.llm_provider == "astrbot_custom"
    assert services.config.astrbot_provider_id == "ds"
    assert services.raw_config["llm"] == {"llm_provider": "astrbot_custom", "astrbot_provider_id": "ds"}
    reloaded = build_provider(
        PluginConfig.from_mapping(services.raw_config), astrbot_context=services.astrbot_context,
        raw_config=services.raw_config,
    )
    assert reloaded.provider_id == "ds"
    assert "已切换" in out[0]
    listed = await _collect(handle_model(services, _Event("/总结模型")))
    assert "ds (model: deepseek-chat)  ← 当前" in listed[0]


@pytest.mark.asyncio
async def test_invalid_id_is_rejected_and_lists() -> None:
    services = _astrbot_services([_Provider("ds", "deepseek-chat")])
    out = await _collect(handle_model(services, _Event("/总结模型 nope")))  # type: ignore[arg-type]
    assert services.llm.provider_id == ""  # unchanged
    assert "未找到" in out[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_reset_restores_default(wrapped: bool) -> None:
    services = _astrbot_services([_Provider("ds", "deepseek-chat")])
    services.raw_config["llm"] = {"llm_provider": "astrbot_custom", "astrbot_provider_id": "ds"}
    services.config = PluginConfig.from_mapping(services.raw_config)
    services.llm.provider_id = "ds"
    primary = services.llm
    if wrapped:
        services.llm = SimpleNamespace(primary=primary)
    out = await _collect(handle_model(services, _Event("/总结模型 默认")))  # type: ignore[arg-type]
    assert primary.provider_id == ""
    assert services.config.llm_provider == "astrbot"
    assert services.config.astrbot_provider_id == ""
    assert services.raw_config["llm"] == {"llm_provider": "astrbot", "astrbot_provider_id": ""}
    reloaded = build_provider(
        PluginConfig.from_mapping(services.raw_config), astrbot_context=services.astrbot_context,
        raw_config=services.raw_config,
    )
    assert reloaded.provider_id == ""
    assert "默认" in out[0]


@pytest.mark.asyncio
async def test_legacy_openai_mode_can_use_astrbot_model_command() -> None:
    config = PluginConfig.from_mapping(
        {"llm_provider": "openai_compatible", "llm_api_base": "https://x/v1", "llm_api_key": "k"}
    )
    services = _Services(config, [_Provider("ds", "deepseek-chat")])
    out = await _collect(handle_model(services, _Event("/总结模型")))  # type: ignore[arg-type]
    assert "可用内置模型" in out[0]
    assert "ds" in out[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("async_save", [False, True])
async def test_model_command_persists_platform_selection_and_reset(tmp_path, async_save) -> None:
    class SavedConfig(dict):
        pass

    saved_path = tmp_path / "plugin.json"
    services = _astrbot_services([_Provider("ds", "deepseek-chat")])
    services.raw_config = SavedConfig(services.raw_config)
    services.raw_config["llm"]["backup_provider_id"] = ["backup"]

    def save():
        saved_path.write_text(json.dumps(services.raw_config), encoding="utf-8")

    async def save_async():
        save()

    services.raw_config.save_config = save_async if async_save else save
    for command, expected in [("/总结模型 ds", "ds"), ("/总结模型 默认", "")]:
        await _collect(handle_model(services, _Event(command)))
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        reloaded = build_provider(
            PluginConfig.from_mapping(saved), astrbot_context=services.astrbot_context, raw_config=saved,
        )
        assert reloaded.provider_id == expected
        assert saved["llm"]["backup_provider_id"] == ["backup"]

"""Native chat provider choices for the backup model control."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.webui import PluginWebUI
from bilivideo.core.config import PluginConfig
from bilivideo.llm.provider import build_provider


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("backup,expected", [("backup", ["backup"]), (["backup-2", "backup"], ["backup-2", "backup"]), ([], [])])
async def test_state_lists_native_chat_models_and_preserves_backup(tmp_path, monkeypatch, available: bool, backup, expected) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    provider = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="backup"),
        get_model=lambda: "backup-model",
        api_key="must-not-be-exposed",
    )
    context = SimpleNamespace(get_all_providers=lambda: [provider]) if available else object()
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": backup}}
    ui = PluginWebUI(
        plugin=SimpleNamespace(config=raw_config),
        context=context,
        services=SimpleNamespace(),
        data_dir=str(tmp_path),
    )

    state = await ui.get_state()

    assert state["providers"] == ([{"id": "backup", "model": "backup-model"}] if available else [])
    assert state["config"]["backup_provider_id"] == expected
    assert state["config"]["enable_fallback"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("backup,expected", [
    ("backup", ["backup"]),
    (["backup-2", "backup", "backup-2"], ["backup-2", "backup"]),
    ([], []),
])
async def test_save_backup_models_preserves_order_and_can_clear(tmp_path, monkeypatch, backup, expected) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    monkeypatch.setattr("bilivideo.webui.request", SimpleNamespace(json=AsyncMock(return_value={
        "settings": {"backup_provider_id": backup},
    })))
    config = {"llm": {"enable_fallback": True, "backup_provider_id": "old-backup"}}
    services = SimpleNamespace(raw_config={})
    ui = PluginWebUI(SimpleNamespace(config=config), object(), services, str(tmp_path))

    result = await ui.save_settings()

    assert result["saved"] is True
    assert result["restart_required"] is True
    assert config["llm"]["backup_provider_id"] == expected
    assert services.raw_config["llm"]["backup_provider_id"] == expected
    state = await ui.get_state()
    assert state["config"]["backup_provider_id"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_state_normalizes_legacy_template_selection(tmp_path, monkeypatch, nested: bool) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    block = {"renderer_template": "template_1 (经典风格)"}
    config = {"render": block} if nested else block
    ui = PluginWebUI(SimpleNamespace(config=config), object(), SimpleNamespace(), str(tmp_path))

    state = await ui.get_state()

    assert state["config"]["renderer_template"] == "template_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_legacy_openai_fields_are_not_exposed_or_editable(tmp_path, monkeypatch, nested: bool) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    block = {
        "llm_provider": "openai_compatible", "llm_api_base": "https://unused.example/v1",
        "llm_api_key": "old-secret", "llm_model": "old-model", "llm_temperature": 0.4,
    }
    config = {"llm": block} if nested else block
    ui = PluginWebUI(SimpleNamespace(config=config), object(), SimpleNamespace(), str(tmp_path))

    state = await ui.get_state()

    assert state["config"]["llm_provider"] == "astrbot"
    removed = {"llm_api_base", "llm_api_key", "llm_model", "llm_temperature"}
    assert not removed & state["config"].keys()
    assert not removed & state["secret_present"].keys()
    assert "old-secret" not in repr(state)
    assert ui._candidate_with_settings({"llm_api_key": "replacement"}) == config


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("mode,expected", [("astrbot", ""), ("astrbot_custom", "selected")])
async def test_only_platform_selection_controls_the_model(tmp_path, monkeypatch, nested, mode, expected) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    block = {"llm_provider": "astrbot", "llm_provider_id": "removed"}
    config = {"llm": block} if nested else block
    services = SimpleNamespace(raw_config={})
    ui = PluginWebUI(SimpleNamespace(config=config), object(), services, str(tmp_path))
    state = await ui.get_state()
    assert "llm_provider_id" not in state["config"]
    assert ui._candidate_with_settings({"llm_provider_id": "replacement"}) == config

    monkeypatch.setattr("bilivideo.webui.request", SimpleNamespace(json=AsyncMock(return_value={
        "settings": {"llm_provider": mode, "astrbot_provider_id": "selected"},
    })))
    result = await ui.save_settings()
    assert result["saved"] is True and result["restart_required"] is True
    assert "llm_provider_id" not in result["config"]
    parsed = PluginConfig.from_mapping(config)
    assert services.config == parsed
    provider = build_provider(parsed, astrbot_context=None, raw_config=config)
    assert provider.provider_id == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_all_legacy_model_overrides_are_ignored_by_settings(tmp_path, monkeypatch, nested) -> None:
    from dataclasses import asdict

    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    groups = {
        "video": {"video_summary_model": "old-video-model"},
        "coolapk": {"coolapk_summary_model": "old-coolapk-model"},
        "zhihu": {"zhihu_summary_model": "old-zhihu-model"},
        "subscription": {"dynamic_summary_model": "old-dynamic-model"},
    }
    legacy = {key: value for block in groups.values() for key, value in block.items()}
    config = groups if nested else legacy
    ui = PluginWebUI(SimpleNamespace(config=config), object(), SimpleNamespace(raw_config={}), str(tmp_path))
    assert not legacy.keys() & asdict(PluginConfig.from_mapping(config)).keys()
    state = await ui.get_state()
    assert not legacy.keys() & state["config"].keys()
    assert ui._candidate_with_settings(dict.fromkeys(legacy, "replacement")) == config
    monkeypatch.setattr("bilivideo.webui.request", SimpleNamespace(json=AsyncMock(return_value={
        "settings": dict.fromkeys(legacy, "replacement"),
    })))
    saved = await ui.save_settings()
    assert saved["saved"] is True
    assert not legacy.keys() & saved["config"].keys()

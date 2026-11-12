"""Native chat provider choices for the backup model control."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bilivideo.webui import PluginWebUI


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

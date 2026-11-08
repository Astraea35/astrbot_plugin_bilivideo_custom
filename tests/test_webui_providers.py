"""Native chat provider choices for the backup model control."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bilivideo.webui import PluginWebUI


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
async def test_state_lists_native_chat_models_and_preserves_backup(tmp_path, monkeypatch, available: bool) -> None:
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    provider = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="backup"),
        get_model=lambda: "backup-model",
        api_key="must-not-be-exposed",
    )
    context = SimpleNamespace(get_all_providers=lambda: [provider]) if available else object()
    raw_config = {"llm": {"enable_fallback": True, "backup_provider_id": "backup"}}
    ui = PluginWebUI(
        plugin=SimpleNamespace(config=raw_config),
        context=context,
        services=SimpleNamespace(),
        data_dir=str(tmp_path),
    )

    state = await ui.get_state()

    assert state["providers"] == ([{"id": "backup", "model": "backup-model"}] if available else [])
    assert state["config"]["backup_provider_id"] == "backup"
    assert state["config"]["enable_fallback"] is True

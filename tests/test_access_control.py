"""Access-control tests."""

from __future__ import annotations

from bilivideo.access.control import is_allowed, is_auto_summary_allowed, is_manual_summary_allowed
from bilivideo.core.config import PluginConfig


def _cfg(mode: str, access_list: tuple[str, ...] = ()) -> PluginConfig:
    return PluginConfig(access_mode=mode, access_list=access_list)


def test_no_list_allowed() -> None:
    assert is_allowed("aiocqhttp:GroupMessage:111", config=_cfg("blacklist"))


def test_blacklist_blocks() -> None:
    cfg = _cfg("blacklist", ("123",))
    assert is_allowed("aiocqhttp:GroupMessage:456", config=cfg)
    assert not is_allowed("aiocqhttp:GroupMessage:123", config=cfg)


def test_whitelist_lets_listed_in() -> None:
    cfg = _cfg("whitelist", ("123",))
    assert is_allowed("aiocqhttp:GroupMessage:123", config=cfg)
    assert not is_allowed("aiocqhttp:GroupMessage:999", config=cfg)


def test_empty_origin_allowed() -> None:
    cfg = _cfg("whitelist", ("123",))
    assert is_allowed("", config=cfg)


def test_sender_id_in_list_is_matched_exactly() -> None:
    cfg = _cfg("whitelist", ("123",))
    assert is_allowed("aiocqhttp:GroupMessage:456", config=cfg, sender_id="123")
    assert not is_allowed("aiocqhttp:GroupMessage:456", config=cfg, sender_id="9123")


def test_manual_summary_cannot_bypass_total_scope() -> None:
    cfg = PluginConfig(
        access_mode="whitelist",
        access_list=("100",),
        manual_summary_mode="all",
    )
    assert is_manual_summary_allowed("aiocqhttp:GroupMessage:100", config=cfg)
    assert not is_manual_summary_allowed("aiocqhttp:GroupMessage:200", config=cfg)


def test_auto_summary_requires_both_scopes() -> None:
    cfg = PluginConfig(
        access_mode="all",
        auto_summary_mode="whitelist",
        auto_summary_list=("123",),
    )
    assert is_auto_summary_allowed(
        "aiocqhttp:GroupMessage:456", config=cfg, sender_id="123"
    )
    assert not is_auto_summary_allowed(
        "aiocqhttp:GroupMessage:456", config=cfg, sender_id="999"
    )

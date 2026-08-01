"""Unified access rules for plugin, manual-summary, and auto-summary use."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..core.config import PluginConfig

_PRIVATE_MARKERS = frozenset({"friendmessage", "privatemessage", "private", "friend", "person"})


def _context(subject: object | str, sender_id: str = "") -> tuple[str, str]:
    """Return a normalized origin and sender ID from an event or origin string."""
    if isinstance(subject, str):
        return subject.strip(), str(sender_id).strip()

    origin = str(getattr(subject, "unified_msg_origin", "") or "").strip()
    if not sender_id:
        get_sender_id = getattr(subject, "get_sender_id", None)
        if callable(get_sender_id):
            try:
                sender_id = str(get_sender_id() or "")
            except Exception:
                sender_id = ""
    return origin, str(sender_id).strip()


def _is_private(origin: str) -> bool:
    return any(part.casefold() in _PRIVATE_MARKERS for part in origin.split(":"))


def _matches_target(origin: str, sender_id: str, targets: Iterable[str]) -> bool:
    """Match configured IDs exactly against the conversation or sender."""
    if isinstance(targets, str):
        targets = targets.split(",")
    normalized = {str(item).strip() for item in targets if str(item).strip()}
    if not normalized:
        return False
    conversation_id = origin.rsplit(":", 1)[-1] if ":" in origin else origin
    return origin in normalized or conversation_id in normalized or sender_id in normalized


def _scope_allows(mode: str, targets: Iterable[str], origin: str, sender_id: str) -> bool:
    if mode == "all":
        return True
    if mode == "private_only":
        return _is_private(origin)

    matches = _matches_target(origin, sender_id, targets)
    if mode == "whitelist":
        return matches
    if mode == "blacklist":
        return not matches
    return True


def _access_targets(config: PluginConfig) -> tuple[str, ...]:
    """Prefer the new field while accepting the legacy group_list setting."""
    if config.access_list:
        return config.access_list
    if isinstance(config.group_list, str):
        return tuple(item.strip() for item in config.group_list.split(",") if item.strip())
    return tuple(str(item) for item in config.group_list)


def is_allowed(subject: object | str, *, config: PluginConfig, sender_id: str = "") -> bool:
    """Return whether the plugin's total access scope permits this request."""
    origin, sender_id = _context(subject, sender_id)
    if not origin:
        return True
    return _scope_allows(config.access_mode, _access_targets(config), origin, sender_id)


def is_manual_summary_allowed(
    subject: object | str, *, config: PluginConfig, sender_id: str = ""
) -> bool:
    """Manual summaries must pass both total and manual-summary scopes."""
    origin, sender_id = _context(subject, sender_id)
    return is_allowed(origin, config=config, sender_id=sender_id) and _scope_allows(
        config.manual_summary_mode,
        config.manual_summary_list,
        origin,
        sender_id,
    )


def is_auto_summary_allowed(
    subject: object | str, *, config: PluginConfig, sender_id: str = ""
) -> bool:
    """Automatic summaries must pass both total and auto-summary scopes."""
    origin, sender_id = _context(subject, sender_id)
    return is_allowed(origin, config=config, sender_id=sender_id) and _scope_allows(
        config.auto_summary_mode,
        config.auto_summary_list,
        origin,
        sender_id,
    )


async def is_admin(services: Any, event: object) -> bool:
    """Use AstrBot's permission manager for commands that change shared state."""
    context = getattr(services, "astrbot_context", None)
    manager = getattr(context, "permission_manager", None)
    checker = getattr(manager, "is_admin", None)
    if not callable(checker):
        return False
    try:
        return bool(await checker(event))
    except Exception:
        return False

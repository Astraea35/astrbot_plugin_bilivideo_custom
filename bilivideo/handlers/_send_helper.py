"""Helpers used by handlers to dispatch the rendered note.

Centralizes the "image-or-text + maybe forward" decision so each handler
stays focused on its own logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..messaging.forward import build_forward_nodes
from ..services import BiliVideoServices


def make_chain_results(
    event: object,
    rendered: list[Any] | str,
) -> Any:
    """Build the appropriate result for a handler `yield`."""

    if isinstance(rendered, list):
        return event.chain_result(rendered)  # type: ignore[attr-defined]
    return event.plain_result(rendered)  # type: ignore[attr-defined]


async def yield_note_response(
    services: BiliVideoServices,
    event: object,
    rendered: list[Any] | str,
    *,
    video_info: Any | None = None,
    meta: Any | None = None,
) -> AsyncIterator[Any]:
    """Yield either a single forward-message or the rendered components for any platform."""
    item = meta if meta is not None else video_info

    if services.config.enable_forward_message and item is not None:
        try:
            forward = build_forward_nodes(
                item,
                rendered,
                bot_name=services.config.forward_bot_name,
                bot_uin=services.config.forward_bot_uin,
            )
            yield event.chain_result([forward])  # type: ignore[attr-defined]
            return
        except Exception as exc:
            # Any forward-node failure (missing AstrBot stubs, version drift in
            # Node/Nodes, a bad cover URL, …) must still deliver the rendered
            # image/text below rather than crash the handler.
            services.logger.warning(f"forward fallback: {exc}")
    if isinstance(rendered, list):
        for component in rendered:
            yield event.chain_result([component])  # type: ignore[attr-defined]
        return
    yield make_chain_results(event, rendered)

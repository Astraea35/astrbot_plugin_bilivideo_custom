"""`/识别开关` handler."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from ..access.control import is_admin
from ..services import BiliVideoServices


async def handle_toggle_detect(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return
    enabled = not services.config.enable_miniapp_detect
    raw_config = getattr(services, "raw_config", {})
    if isinstance(raw_config, dict):
        detect_config = raw_config.get("detect")
        if not isinstance(detect_config, dict):
            detect_config = {}
            raw_config["detect"] = detect_config
        detect_config["enable_miniapp_detect"] = enabled

    services.config = replace(services.config, enable_miniapp_detect=enabled)
    services.enable_miniapp_detect = enabled
    try:
        context = getattr(services, "astrbot_context", None)
        if context is not None and hasattr(context, "save_config"):
            await context.save_config()
    except Exception as exc:
        services.logger.warning(f"保存自动识别配置失败: {exc}")

    status = "✅ 已开启" if enabled else "❌ 已关闭"
    yield event.plain_result(f"B站链接自动识别: {status}")  # type: ignore[attr-defined]

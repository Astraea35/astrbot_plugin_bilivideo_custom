"""卡片样式切换命令: /bili_card_style"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from ..access.control import is_admin
from ..core.constants import CARD_TEMPLATES, get_template_names, DEFAULT_TEMPLATE
from ..services import BiliVideoServices
from ._utils import parse_command_args


async def handle_card_style(services: BiliVideoServices, event: object) -> AsyncIterator[object]:
    """
    /bili_card_style [样式名] - 切换动态卡片样式
    不带参数显示可用样式列表
    """
    if not await is_admin(services, event):
        yield event.plain_result("⛔ 此命令仅限 AstrBot 管理员使用")
        return

    args = parse_command_args(getattr(event, "message_str", "") or "").strip()
    available = get_template_names()

    # 不带参数：显示样式列表
    if not args:
        current_style = services.config.renderer_template
        lines = ["📋 可用的卡片样式："]
        for tid in available:
            info = CARD_TEMPLATES[tid]
            current = " ← 当前" if tid == current_style else ""
            lines.append(f"  • {tid}: {info['name']}{current}")
            lines.append(f"    {info['description']}")
        lines.append("\n使用 /bili_card_style <样式名> 切换")
        yield event.plain_result("\n".join(lines))
        return

    # 检查样式是否存在
    if args not in available:
        yield event.plain_result(
            f"❌ 样式 '{args}' 不存在。\n可用样式: {', '.join(available)}"
        )
        return

    # 切换样式
    raw_config = getattr(services, "raw_config", {})
    if isinstance(raw_config, dict):
        render_config = raw_config.get("render")
        if not isinstance(render_config, dict):
            render_config = {}
            raw_config["render"] = render_config
        render_config["renderer_template"] = args

    services.config = replace(services.config, renderer_template=args)
    if hasattr(services, "dynamic_renderer") and services.dynamic_renderer:
        services.dynamic_renderer.style = args

    # 尝试保存配置（通过AstrBot的配置管理器）
    try:
        if hasattr(services, "astrbot_context") and services.astrbot_context:
            ctx = services.astrbot_context
            if hasattr(ctx, "save_config"):
                await ctx.save_config()
    except Exception as e:
        services.logger.warning(f"保存配置失败: {e}")

    info = CARD_TEMPLATES[args]
    yield event.plain_result(f"✅ 已切换样式为：{info['name']} ({args})")

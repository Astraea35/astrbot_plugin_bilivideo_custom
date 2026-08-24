"""Handlers package initialization."""

from __future__ import annotations

from .auto_detect import handle_auto_detect
from .help import handle_help
from .login import handle_login, handle_logout
from .model import handle_model
from .push_target import (
    handle_list_push,
    handle_remove_push,
)
from .status import handle_clear_cache, handle_status
from .subscription import (
    handle_check_updates,
    handle_list_subscriptions,
    handle_subscribe,
    handle_unsubscribe,
    handle_live_atall_toggle,  # 新增
)
from .summary import handle_latest_video, handle_summary
from .toggle import handle_toggle_detect
from .youtube import handle_youtube_login, handle_youtube_logout

from .space import (
    handle_space_view,
    handle_space_page_turn,
    handle_space_page_jump,
    handle_video_select,
    handle_visit_homepage,
)

from .admin_commands import (
    handle_global_list,
    handle_global_del,
    handle_global_sub,
)
from .style_switch import handle_card_style


__all__ = [
    "handle_auto_detect",
    "handle_check_updates",
    "handle_clear_cache",
    "handle_help",
    "handle_latest_video",
    "handle_list_push",
    "handle_list_subscriptions",
    "handle_login",
    "handle_logout",
    "handle_model",
    "handle_remove_push",
    "handle_space_page_jump",
    "handle_space_page_turn",
    "handle_space_view",
    "handle_status",
    "handle_subscribe",
    "handle_summary",
    "handle_toggle_detect",
    "handle_unsubscribe",
    "handle_video_select",
    "handle_visit_homepage",
    "handle_youtube_login",
    "handle_youtube_logout",
    "handle_global_list",
    "handle_global_del",
    "handle_global_sub",
    "handle_card_style",
    "handle_live_atall_toggle",  # 新增
]

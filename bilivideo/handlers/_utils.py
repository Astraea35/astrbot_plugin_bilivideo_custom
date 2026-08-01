"""Shared helpers for command handlers."""

from __future__ import annotations

import re
from typing import List, Tuple

from ..services import BiliVideoServices
from ..core.constants import VALID_FILTER_TYPES, LIVE_ATALL_OPTION


def parse_command_args(message: str) -> str:
    """Return everything after the command word, stripped.
    
    ``/订阅 123456`` -> ``123456``. Returns ``""`` when no argument is present.
    """
    if not message:
        return ""
    parts = message.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def parse_sub_args(input_text: str) -> Tuple[List[str], List[str], bool]:
    """
    解析订阅命令的过滤器参数
    
    Args:
        input_text: 命令参数部分，如 "123456 lottery video 关注"
    
    Returns:
        (filter_types, filter_regex, live_atall)
        - filter_types: 过滤类型列表，如 ["lottery", "video"]
        - filter_regex: 正则表达式列表，如 ["关注"]
        - live_atall: 是否开启直播@全体
    """
    args = input_text.strip().split() if input_text.strip() else []
    filter_types: List[str] = []
    filter_regex: List[str] = []
    live_atall = False

    for arg in args:
        if arg == LIVE_ATALL_OPTION:
            live_atall = True
            continue
        if arg in VALID_FILTER_TYPES:
            filter_types.append(arg)
        else:
            filter_regex.append(arg)

    return filter_types, filter_regex, live_atall


async def resolve_subscription_index(
    services: BiliVideoServices, origin: str, arg: str
) -> Tuple[str, str] | None:
    """
    仅当参数明确包含“序号”关键字时，才解析为订阅列表中的索引。
    
    支持格式：
        - "序号1" 或 "序号 1"
    
    返回 (mid, name)，否则返回 None。
    """
    if not origin or not arg:
        return None

    match = re.search(r"序号\s*(\d+)", arg)
    if not match:
        return None

    idx = int(match.group(1))
    subs = await services.subscription_manager.get_subscriptions(origin)
    if 1 <= idx <= len(subs):
        target = subs[idx - 1]
        return target.mid, target.name
    return None


def build_filter_desc(filter_types: List[str], filter_regex: List[str], live_atall: bool) -> str:
    """构建过滤条件描述文本"""
    desc = ""
    if filter_types:
        desc += f"<br>过滤类型: {', '.join(filter_types)}"
    if filter_regex:
        desc += f"<br>过滤正则: {filter_regex}"
    desc += f"<br>直播开播@全体: {'开启' if live_atall else '关闭'}"
    return desc
"""bilivideo - AstrBot Bilibili & Douyin 视频解析与 AI 总结的 Mod 魔改实现层。"""

from .core.config import PluginConfig
from .core.exceptions import (
    BiliVideoError,
    LLMError,
    NetworkError,
    NotLoggedInError,
    RenderError,
    TranscriptionError,
)

__all__ = [
    "BiliVideoError",
    "LLMError",
    "NetworkError",
    "NotLoggedInError",
    "PluginConfig",
    "RenderError",
    "TranscriptionError",
]

__version__ = "1.0.0"

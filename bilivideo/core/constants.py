"""Centralized constants: regexes, endpoints, user agents, defaults.

Keeping all magic strings/numbers in one file makes it trivial to:
  * audit network traffic surface
  * update User-Agent or endpoints when Bilibili changes them
  * unit-test regex behavior without importing handler code
"""

from __future__ import annotations

import os
import re
from typing import Final

# ==================== 路径定义 ====================
CURRENT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))  # 修正：多一层 dirname
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")


def _asset_path(*parts: str) -> str:
    return os.path.join(ASSETS_DIR, *parts)


# ──────────────────────────── HTTP ───────────────────────────────

DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_REFERER: Final[str] = "https://www.bilibili.com"

# Lower than 30s so a stalled server doesn't hold up the event loop forever
HTTP_TIMEOUT_SECONDS: Final[int] = 12
HTTP_MAX_RETRIES: Final[int] = 3
HTTP_BACKOFF_BASE: Final[float] = 0.6  # seconds; exponential backoff base

# Per-stage budgets (seconds). A single hung stage surfaces a specific error
# fast, instead of the whole request blocking until processing_timeout.
SUBTITLE_FETCH_TIMEOUT_SECONDS: Final[int] = 90
AUDIO_DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 150
ASR_TIMEOUT_SECONDS: Final[int] = 180
LLM_CHAT_TIMEOUT_SECONDS: Final[int] = 180
# yt-dlp per-socket timeout so a stalled connection can't hang a download.
YTDLP_SOCKET_TIMEOUT_SECONDS: Final[int] = 30

# ───────────────────────── Bilibili API ──────────────────────────

API_BASE: Final[str] = "https://api.bilibili.com"
PASSPORT_BASE: Final[str] = "https://passport.bilibili.com"

ENDPOINT_NAV: Final[str] = f"{API_BASE}/x/web-interface/nav"
ENDPOINT_VIEW: Final[str] = f"{API_BASE}/x/web-interface/view"
ENDPOINT_USER_INFO: Final[str] = f"{API_BASE}/x/space/wbi/acc/info"
ENDPOINT_USER_VIDEOS: Final[str] = f"{API_BASE}/x/space/wbi/arc/search"
ENDPOINT_USER_VIDEOS_LEGACY: Final[str] = f"{API_BASE}/x/space/arc/search"
ENDPOINT_SEARCH_TYPE_WBI: Final[str] = f"{API_BASE}/x/web-interface/wbi/search/type"
ENDPOINT_SEARCH_TYPE: Final[str] = f"{API_BASE}/x/web-interface/search/type"
ENDPOINT_QR_GENERATE: Final[str] = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/generate"
ENDPOINT_QR_POLL: Final[str] = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/poll"

# Necessary cookies for many search endpoints
ESSENTIAL_COOKIES: Final[tuple[str, ...]] = ("buvid3",)

BILI_DOMAINS: Final[tuple[str, ...]] = (
    "bilibili.com",
    "b23.tv",
    "bili2233.cn",
    "bili22.cn",
    "bili23.cn",
    "bili33.cn",
)

# ──────────────────────────── Regex ──────────────────────────────

BV_REGEX: Final[re.Pattern[str]] = re.compile(r"BV[0-9A-Za-z]{10}")
UID_REGEX: Final[re.Pattern[str]] = re.compile(r"space\.bilibili\.com/(\d+)")
LONG_URL_REGEX: Final[re.Pattern[str]] = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/[A-Za-z0-9/?=&_.\-]+"
)
SHORT_URL_REGEX: Final[re.Pattern[str]] = re.compile(
    r"https?://(?:b23\.tv|bili2233\.cn|bili22\.cn|bili23\.cn|bili33\.cn)/\S{1,256}",
    re.IGNORECASE,
)
QQDOC_URL_REGEX: Final[re.Pattern[str]] = re.compile(r'"qqdocurl"\s*:\s*"(https?://[^"]+)"')
TIMESTAMP_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?:\*?)Content-(?:\[(\d{2}):(\d{2})\]|(\d{2}):(\d{2}))"
)

# ──────────────────────────── Misc ───────────────────────────────

QUALITY_TO_KBPS: Final[dict[str, str]] = {
    "fast": "32",
    "medium": "64",
    "slow": "128",
}

NOTE_STYLES: Final[tuple[str, ...]] = ("concise", "detailed", "professional")
LLM_PROVIDERS: Final[tuple[str, ...]] = ("astrbot", "openai_compatible")
ACCESS_MODES: Final[tuple[str, ...]] = ("all", "private_only", "whitelist", "blacklist")

WBI_CACHE_TTL_SECONDS: Final[int] = 86_400  # 24h
VIDEO_INFO_CACHE_TTL_SECONDS: Final[int] = 600  # 10min
VIDEO_INFO_CACHE_MAX: Final[int] = 256

# A video's generated summary is stable, so it can be cached far longer than
# its mutable metadata. Avoids re-running download+ASR+LLM for repeat requests.
SUMMARY_CACHE_TTL_SECONDS: Final[int] = 604_800  # 7 days
SUMMARY_CACHE_MAX: Final[int] = 64
TRANSCRIPT_CACHE_TTL_SECONDS: Final[int] = 21_600  # 6h
TRANSCRIPT_CACHE_MAX: Final[int] = 64


# ==================== 动态订阅相关常量 ====================

# 有效过滤类型（用于订阅时指定过滤哪些动态类型）
VALID_FILTER_TYPES = {
    "forward",
    "lottery",
    "video",
    "article",
    "draw",
    "live",
    "forward_lottery",
}

# 直播@全体选项标记
LIVE_ATALL_OPTION = "live_atall"

# 所有合法的订阅选项（过滤类型 + 特殊标记）
VALID_SUB_OPTIONS = {LIVE_ATALL_OPTION} | VALID_FILTER_TYPES

# 重连静默阈值（秒）：距上次成功推送超过此时间则触发静默
RECONNECT_SILENT_THRESHOLD_SECS = 21600  # 6小时
RECONNECT_SILENT_PADDING_SECS = 60       # 静默额外缓冲时间

# 动态缓存大小（每个订阅保留的最近动态ID数量）
RECENT_DYNAMIC_CACHE = 4

# ==================== 模板注册表 ====================

# 模板路径定义
LOGO_PATH = _asset_path("logo.png")
BANNER_PATH = _asset_path("banner.png")

CARD_TEMPLATES = {
    "template_1": {
        "name": "经典风格",
        "description": "原版设计",
        "file": "template_1.html",
        "path": _asset_path("templates", "template_1.html"),
    },
    "template_2": {
        "name": "B站粉风格",
        "description": "B站风格设计",
        "file": "template_2.html",
        "path": _asset_path("templates", "template_2.html"),
    },
    "simple": {
        "name": "简约风格",
        "description": "简洁现代的设计",
        "file": "template_simple.html",
        "path": _asset_path("templates", "template_simple.html"),
    },
}

DEFAULT_TEMPLATE = "template_2"


def get_template_names() -> list:
    """获取所有模板的 ID 列表"""
    return list(CARD_TEMPLATES.keys())


def get_template_path(style: str) -> str:
    """获取指定样式的模板文件路径"""
    if style not in CARD_TEMPLATES:
        style = DEFAULT_TEMPLATE
    return CARD_TEMPLATES[style]["path"]

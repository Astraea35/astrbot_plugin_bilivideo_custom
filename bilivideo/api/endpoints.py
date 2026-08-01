"""High-level Bilibili API helpers.

Each function takes a `BilibiliHTTPClient` (allowing dependency-injection
in tests) and returns typed dataclasses from `core.types` instead of raw
dicts. Errors propagate as exceptions from `core.exceptions`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..cache.lru_ttl import LRUTTLCache
from ..core.constants import (
    ENDPOINT_SEARCH_TYPE,
    ENDPOINT_SEARCH_TYPE_WBI,
    ENDPOINT_USER_INFO,
    ENDPOINT_USER_VIDEOS,
    ENDPOINT_USER_VIDEOS_LEGACY,
    ENDPOINT_VIEW,
    VIDEO_INFO_CACHE_MAX,
    VIDEO_INFO_CACHE_TTL_SECONDS,
)
from ..core.exceptions import BilibiliAPIError, BiliVideoError, NetworkError
from ..core.logging import get_logger
from ..core.types import (
    LatestVideo,
    SearchResult,
    SearchVideoItem,
    UploaderInfo,
    VideoInfo,
)
from .client import BilibiliHTTPClient
from .wbi import sign_params

logger = get_logger("BiliVideo/API")

_HIGHLIGHT_RE = re.compile(r'</?em[^>]*>')


def _strip_highlight(text: str) -> str:
    """Remove `<em class="keyword">` tags wrapping the search query."""
    return _HIGHLIGHT_RE.sub("", text or "")


def _normalize_pic(pic: str) -> str:
    if pic.startswith("//"):
        return "https:" + pic
    return pic


# Cache scope: per-process. We don't bind it to a client instance so
# different short-lived BilibiliHTTPClients (e.g. in tests) still benefit.
_video_info_cache: LRUTTLCache[str, VideoInfo] = LRUTTLCache(
    max_size=VIDEO_INFO_CACHE_MAX, ttl_seconds=VIDEO_INFO_CACHE_TTL_SECONDS
)


def video_info_cache_size() -> int:
    return len(_video_info_cache)


async def clear_video_info_cache() -> None:
    await _video_info_cache.clear()


# ──────────────────────────── reading ───────────────────────────────


async def get_video_info(client: BilibiliHTTPClient, bvid: str) -> VideoInfo:
    """Fetch /x/web-interface/view, returning a typed `VideoInfo`."""

    async def fetch() -> VideoInfo:
        payload = await client.request_json("GET", ENDPOINT_VIEW, params={"bvid": bvid})
        data = payload.get("data") or {}
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        return VideoInfo(
            bvid=str(data.get("bvid") or bvid),
            title=str(data.get("title", "")),
            pic=_normalize_pic(str(data.get("pic", ""))),
            desc=str(data.get("desc", "")),
            pubdate=int(data.get("pubdate", 0) or 0),
            duration=int(data.get("duration", 0) or 0),
            owner_name=str(owner.get("name", "未知")),
            owner_mid=str(owner.get("mid", "")),
            view=int(stat.get("view", 0) or 0),
            danmaku=int(stat.get("danmaku", 0) or 0),
            like=int(stat.get("like", 0) or 0),
            raw=data,
        )

    return await _video_info_cache.get_or_set(bvid, fetch)


async def get_uploader_info(client: BilibiliHTTPClient, mid: str) -> UploaderInfo | None:
    signed = await sign_params({"mid": mid}, cookies=client.cookies)
    if "w_rid" not in signed:
        logger.warning(f"get_uploader_info({mid}) skipped: WBI signing unavailable")
        return None
    try:
        payload = await client.request_json("GET", ENDPOINT_USER_INFO, params=signed)
    except (BilibiliAPIError, NetworkError) as exc:
        logger.warning(f"get_uploader_info({mid}) failed: {exc}")
        return None
    data = payload.get("data") or {}
    return UploaderInfo(
        mid=str(data.get("mid", mid)),
        name=str(data.get("name", "未知")),
        face=str(data.get("face", "")),
        sign=str(data.get("sign", "")),
    )


async def get_latest_videos(
    client: BilibiliHTTPClient, mid: str, *, count: int = 5
) -> list[LatestVideo]:
    params = {"mid": mid, "ps": count, "pn": 1, "order": "pubdate", "order_avoided": "true"}
    signed = await sign_params(params, cookies=client.cookies)
    endpoint_candidates = (
        ((ENDPOINT_USER_VIDEOS, signed), (ENDPOINT_USER_VIDEOS_LEGACY, params))
        if "w_rid" in signed
        else ((ENDPOINT_USER_VIDEOS_LEGACY, params),)
    )

    for endpoint, query_params in endpoint_candidates:
        try:
            payload = await client.request_json("GET", endpoint, params=query_params)
        except BiliVideoError as exc:
            logger.warning(f"get_latest_videos({mid}) endpoint={endpoint} failed: {exc}")
            continue

        data = payload.get("data") or {}
        vlist = ((data.get("list") or {}).get("vlist") or [])
        return [
            LatestVideo(
                bvid=str(v.get("bvid", "")),
                title=str(v.get("title", "")),
                pic=_normalize_pic(str(v.get("pic", ""))),
                pubdate=int(v.get("created", 0) or 0),
                duration=str(v.get("length", "")),
                description=str(v.get("description", "")),
            )
            for v in vlist[:count]
        ]

    return []

async def search_uploader_by_name(
    client: BilibiliHTTPClient, keyword: str
) -> UploaderInfo | None:
    """Search by name, falling back to the legacy endpoint when WBI is rejected."""

    params: Mapping[str, object] = {
        "search_type": "bili_user",
        "keyword": keyword,
        "page": 1,
        "order": "fans",
        "order_sort": 0,
    }
    signed = await sign_params(params, cookies=client.cookies)

    endpoint_candidates = (
        ((ENDPOINT_SEARCH_TYPE_WBI, signed), (ENDPOINT_SEARCH_TYPE, params))
        if "w_rid" in signed
        else ((ENDPOINT_SEARCH_TYPE, params),)
    )

    for endpoint, qparams in endpoint_candidates:
        try:
            logger.debug(f"search_uploader keyword={keyword} endpoint={endpoint}")
            payload = await client.request_json("GET", endpoint, params=qparams)
        except BiliVideoError as exc:
            logger.warning(f"search_uploader_by_name endpoint={endpoint} failed: {exc}; fallback")
            continue

        results = ((payload.get("data") or {}).get("result") or [])
        if not results:
            continue
        # prefer exact match
        for r in results:
            uname = _strip_highlight(str(r.get("uname", "")))
            if uname == keyword:
                return UploaderInfo(mid=str(r.get("mid", "")), name=uname)
        first = results[0]
        return UploaderInfo(
            mid=str(first.get("mid", "")),
            name=_strip_highlight(str(first.get("uname", "未知"))),
        )

    return None


async def search_videos(
    client: BilibiliHTTPClient,
    keyword: str,
    *,
    page: int = 1,
    page_size: int = 20,
    order: str = "totalrank",
    duration: int = 0,
    tids: int = 0,
) -> SearchResult | None:
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": page,
        "page_size": page_size,
        "order": order,
        "duration": duration,
        "tids": tids,
    }
    signed = await sign_params(params, cookies=client.cookies)

    payload: dict[str, object] | None = None
    endpoint_candidates = (
        ((ENDPOINT_SEARCH_TYPE_WBI, signed), (ENDPOINT_SEARCH_TYPE, params))
        if "w_rid" in signed
        else ((ENDPOINT_SEARCH_TYPE, params),)
    )
    for endpoint, qparams in endpoint_candidates:
        try:
            logger.debug(f"search_videos keyword={keyword} endpoint={endpoint} page={page}")
            candidate = await client.request_json("GET", endpoint, params=qparams)
        except BiliVideoError as exc:
            logger.warning(f"search_videos endpoint={endpoint} failed: {exc}; fallback")
            continue
        raw = ((candidate.get("data") or {}).get("result") or [])
        if raw:
            payload = candidate
            break
        logger.debug(f"search_videos endpoint={endpoint} returned empty result; fallback")
        payload = candidate
    if payload is None:
        return None

    data = payload.get("data") or {}
    raw_results = data.get("result") or []
    items: list[SearchVideoItem] = []
    for r in raw_results:
        if r.get("type") != "video":
            continue
        items.append(
            SearchVideoItem(
                bvid=str(r.get("bvid", "")),
                aid=int(r.get("aid", 0) or 0),
                title=_strip_highlight(str(r.get("title", ""))),
                author=str(r.get("author", "")),
                mid=int(r.get("mid", 0) or 0),
                pic=_normalize_pic(str(r.get("pic", ""))),
                description=str(r.get("description", "")),
                play=int(r.get("play", 0) or 0),
                danmaku=int(r.get("danmaku", 0) or 0),
                like=int(r.get("like", 0) or 0),
                favorites=int(r.get("favorites", 0) or 0),
                duration=str(r.get("duration", "")),
                pubdate=int(r.get("pubdate", 0) or 0),
                tag=str(r.get("tag", "")),
            )
        )
    return SearchResult(
        results=tuple(items),
        num_results=int(data.get("numResults", len(items))),
        page=int(data.get("page", page)),
        num_pages=int(data.get("numPages", 0)),
    )


# ==================== 动态订阅新增接口 ====================

async def get_latest_dynamics(client: BilibiliHTTPClient, uid: int) -> dict | None:
    """
    获取用户的最新动态（包含视频、图文、转发等所有类型）
    接口: https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space
    """
    params = {
        "host_mid": uid,
        "platform": "web",
        "features": "itemOpusStyle",
    }
    try:
        payload = await client.request_json(
            "GET",
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params=params,
        )
        if payload.get("code") == 0:
            return payload.get("data", {})
        else:
            logger.warning(f"get_latest_dynamics({uid}) returned code {payload.get('code')}: {payload.get('message')}")
            return None
    except Exception as e:
        logger.error(f"get_latest_dynamics({uid}) failed: {e}")
        return None


async def get_live_info_by_uids(client: BilibiliHTTPClient, uids: list[int]) -> dict | None:
    """
    通过主播uid列表获取直播间状态信息（是否在直播、房间号等）
    接口: https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids
    """
    if not uids:
        return None
    params = {"uids[]": uids}
    try:
        payload = await client.request_json(
            "GET",
            "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids",
            params=params,
        )
        if payload.get("code") == 0:
            data = payload.get("data", {})
            if data:
                # 返回第一个直播间信息
                return next(iter(data.values()))
        return None
    except Exception as e:
        logger.error(f"get_live_info_by_uids({uids}) failed: {e}")
        return None
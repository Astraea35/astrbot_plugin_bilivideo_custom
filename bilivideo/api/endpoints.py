"""High-level Bilibili API helpers.

Each function takes a `BilibiliHTTPClient` (allowing dependency-injection
in tests) and returns typed dataclasses from `core.types` instead of raw
dicts. Errors propagate as exceptions from `core.exceptions`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from ..cache.lru_ttl import LRUTTLCache
from ..core.constants import (
    ENDPOINT_SEARCH_TYPE,
    ENDPOINT_SEARCH_TYPE_WBI,
    ENDPOINT_POPULAR,
    ENDPOINT_PLAYER_V2,
    ENDPOINT_REPLY,
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
    FeaturedComment,
    FeaturedCommentReply,
    LatestVideo,
    SearchResult,
    SearchVideoItem,
    TranscriptResult,
    TranscriptSegment,
    UploaderInfo,
    VideoInfo,
)
from ..parsing.url_extractor import extract_bvid
from .client import BilibiliHTTPClient
from .wbi import sign_params

logger = get_logger("BiliVideo/API")

_HIGHLIGHT_RE = re.compile(r'</?em[^>]*>')


def _safe_media_url(raw_url: object) -> str:
    """Accept only HTTPS Bilibili-hosted media for comment HTML."""
    url = str(raw_url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.startswith("https://"):
        return ""
    host = url.split("/", 3)[2].lower().split(":", 1)[0]
    if host == "bilibili.com" or host.endswith(".bilibili.com") or host.endswith(".hdslb.com"):
        return url
    return ""


def _comment_content_html(content: object, *, limit: int) -> tuple[str, str]:
    """Convert Bilibili comment text/emotes/pictures to safe inline HTML."""
    if not isinstance(content, Mapping):
        return "", ""
    message = str(content.get("message") or "").strip()
    if len(message) > limit:
        message = message[: limit - 1].rstrip() + "…"
    escaped = html.escape(message)

    emotes = content.get("emote")
    if isinstance(emotes, Mapping):
        for placeholder, raw in emotes.items():
            if not isinstance(placeholder, str) or not isinstance(raw, Mapping):
                continue
            icon_url = _safe_media_url(raw.get("url") or raw.get("icon_url"))
            if icon_url and placeholder in message:
                icon = (
                    f'<img class="comment-emote" src="{html.escape(icon_url, quote=True)}" '
                    'alt="表情" loading="eager" referrerpolicy="no-referrer">'
                )
                escaped = escaped.replace(html.escape(placeholder), icon)

    media_urls: list[str] = []
    raw_pictures = content.get("pictures") or content.get("picture") or content.get("image_urls")
    if isinstance(raw_pictures, Mapping):
        raw_pictures = [raw_pictures]
    if isinstance(raw_pictures, list):
        for picture in raw_pictures:
            if not isinstance(picture, Mapping):
                continue
            picture_url = _safe_media_url(
                picture.get("img_src") or picture.get("url") or picture.get("src")
            )
            if picture_url:
                media_urls.append(picture_url)
    elif isinstance(raw_pictures, str):
        picture_url = _safe_media_url(raw_pictures)
        if picture_url:
            media_urls.append(picture_url)

    if media_urls:
        escaped += "".join(
            f'<img class="comment-picture" src="{html.escape(url, quote=True)}" '
            'alt="评论图片" loading="eager" referrerpolicy="no-referrer">'
            for url in media_urls
        )
    return message, escaped


def _strip_highlight(text: str) -> str:
    """Remove `<em class="keyword">` tags wrapping the search query."""
    return _HIGHLIGHT_RE.sub("", text or "")


def _normalize_pic(pic: str) -> str:
    if pic.startswith("//"):
        return "https:" + pic
    return pic


def _is_trusted_bilibili_asset_url(url: str) -> bool:
    """Only request subtitle JSON from Bilibili-controlled HTTPS hosts."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
        and (host.endswith(".hdslb.com") or host.endswith(".bilibili.com"))
    )


def _is_ai_subtitle(entry: Mapping[str, object]) -> bool:
    language = str(entry.get("lan") or "").lower()
    label = str(entry.get("lan_doc") or "").lower()
    return language.startswith("ai-") or "ai" in label


async def get_bilibili_ai_subtitle(
    client: BilibiliHTTPClient,
    video_url: str,
    *,
    langs: tuple[str, ...] | None = None,
) -> TranscriptResult | None:
    """Fetch a logged-in user's Bilibili AI subtitle as a transcript.

    The player endpoint may expose an ``ai-*`` subtitle only when the active
    Bilibili account can access it. A missing subtitle is expected and must
    not interrupt the audio-ASR fallback.
    """

    if not client.cookies.get("SESSDATA"):
        return None
    bvid = extract_bvid(video_url)
    if not bvid:
        return None

    try:
        view = await client.request_json("GET", ENDPOINT_VIEW, params={"bvid": bvid})
        view_data = view.get("data")
        cid = view_data.get("cid") if isinstance(view_data, Mapping) else None
        if not cid:
            return None
        video_duration = float(view_data.get("duration", 0) or 0) if isinstance(view_data, Mapping) else 0.0
        player = await client.request_json(
            "GET", ENDPOINT_PLAYER_V2, params={"bvid": bvid, "cid": int(cid)}
        )
    except (BiliVideoError, ValueError, TypeError) as exc:
        logger.info(f"Bilibili AI subtitle lookup unavailable for {bvid}: {exc}")
        return None

    player_data = player.get("data")
    subtitle = player_data.get("subtitle") if isinstance(player_data, Mapping) else None
    entries = subtitle.get("subtitles") if isinstance(subtitle, Mapping) else None
    if not isinstance(entries, list):
        return None

    preferred = {lang.lower(): index for index, lang in enumerate(langs or ())}
    candidates = [entry for entry in entries if isinstance(entry, Mapping) and _is_ai_subtitle(entry)]
    candidates.sort(
        key=lambda entry: (
            0 if "zh" in str(entry.get("lan") or "").lower() else 1,
            preferred.get(str(entry.get("lan") or "").lower(), len(preferred)),
        )
    )

    for entry in candidates:
        subtitle_url = str(entry.get("subtitle_url") or "").strip()
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        if not _is_trusted_bilibili_asset_url(subtitle_url):
            continue
        try:
            payload = await client.request_json("GET", subtitle_url)
        except BiliVideoError as exc:
            logger.info(f"Bilibili AI subtitle download failed for {bvid}: {exc}")
            continue
        body = payload.get("body")
        if not isinstance(body, list):
            continue
        segments: list[TranscriptSegment] = []
        for item in body:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("content") or "").strip()
            if not text:
                continue
            try:
                start = float(item.get("from", 0) or 0)
                end = float(item.get("to", start) or start)
            except (TypeError, ValueError):
                start = end = 0.0
            segments.append(TranscriptSegment(start=start, end=max(start, end), text=text))
        if segments:
            last_end = max(segment.end for segment in segments)
            if video_duration > 0:
                if last_end > video_duration * 1.25 + 30:
                    logger.warning(
                        f"Bilibili AI subtitle discarded for {bvid}: subtitle timestamp ({last_end:.1f}s) "
                        f"far exceeds video duration ({video_duration:.1f}s), possible stale/polluted data"
                    )
                    continue
                if video_duration > 60 and last_end < video_duration * 0.3 and len(segments) < 20:
                    logger.warning(
                        f"Bilibili AI subtitle discarded for {bvid}: incomplete subtitle coverage "
                        f"({last_end:.1f}s / {video_duration:.1f}s)"
                    )
                    continue
            language = str(entry.get("lan") or "ai-zh")
            logger.info(f"Bilibili AI subtitle hit for {bvid} ({language}, {len(segments)} segments)")
            return TranscriptResult(
                language=language,
                full_text=" ".join(segment.text for segment in segments),
                segments=tuple(segments),
                raw={"source": "bilibili_ai_subtitle", "bvid": bvid},
            )
    return None


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
            category=str(data.get("tname", "")),
            aid=int(data.get("aid", 0) or 0),
            owner_name=str(owner.get("name", "未知")),
            owner_mid=str(owner.get("mid", "")),
            view=int(stat.get("view", 0) or 0),
            danmaku=int(stat.get("danmaku", 0) or 0),
            like=int(stat.get("like", 0) or 0),
            coin=int(stat.get("coin", 0) or 0),
            favorite=int(stat.get("favorite", 0) or 0),
            reply=int(stat.get("reply", 0) or 0),
            share=int(stat.get("share", 0) or 0),
            raw=data,
        )

    return await _video_info_cache.get_or_set(bvid, fetch)


async def get_featured_comments(
    client: BilibiliHTTPClient,
    info: VideoInfo,
    *,
    count: int = 2,
    reply_count: int = 1,
) -> tuple[FeaturedComment, ...]:
    """Return leading hot comments for a Bilibili video without failing its summary."""

    if count <= 0 or info.aid <= 0:
        return ()
    try:
        payload = await client.request_json(
            "GET",
            ENDPOINT_REPLY,
            params={"oid": info.aid, "type": 1, "pn": 1, "sort": 2},
        )
    except BiliVideoError as exc:
        logger.info(f"featured comments unavailable for {info.bvid}: {exc}")
        return ()

    data = payload.get("data")
    source_comments = data.get("replies") if isinstance(data, Mapping) else None
    if not isinstance(source_comments, list):
        return ()

    comments: list[FeaturedComment] = []
    for item in source_comments:
        if not isinstance(item, Mapping):
            continue
        member = item.get("member")
        content = item.get("content")
        author_name = str(member.get("uname") or "B站用户") if isinstance(member, Mapping) else "B站用户"
        message, message_html = _comment_content_html(content, limit=300)
        if not message and not message_html:
            continue
        raw_replies = item.get("replies")
        replies: list[FeaturedCommentReply] = []
        if reply_count > 0 and isinstance(raw_replies, list):
            for reply in raw_replies[:reply_count]:
                if not isinstance(reply, Mapping):
                    continue
                reply_member = reply.get("member")
                reply_content = reply.get("content")
                reply_name = (
                    str(reply_member.get("uname") or "B站用户")
                    if isinstance(reply_member, Mapping)
                    else "B站用户"
                )
                reply_message, reply_html = _comment_content_html(reply_content, limit=160)
                if reply_message or reply_html:
                    replies.append(
                        FeaturedCommentReply(
                            author_name=reply_name,
                            content=reply_message,
                            like=int(reply.get("like", 0) or 0),
                            content_html=reply_html,
                        )
                    )
        comments.append(
            FeaturedComment(
                author_name=author_name,
                content=message,
                like=int(item.get("like", 0) or 0),
                replies=tuple(replies),
                content_html=message_html,
            )
        )
        if len(comments) >= count:
            break
    return tuple(comments)


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


async def get_hot_videos(
    client: BilibiliHTTPClient,
    *,
    pn: int = 1,
    ps: int = 20,
) -> dict | None:
    """获取 B 站全站热门视频列表。
    接口: https://api.bilibili.com/x/web-interface/popular
    """
    params = {"pn": pn, "ps": ps}
    try:
        payload = await client.request_json(
            "GET",
            ENDPOINT_POPULAR,
            params=params,
        )
        if payload.get("code") == 0:
            return payload.get("data", {})
        logger.warning(
            f"get_hot_videos(pn={pn}, ps={ps}) returned code {payload.get('code')}: {payload.get('message')}"
        )
        return None
    except Exception as e:
        logger.error(f"get_hot_videos(pn={pn}, ps={ps}) failed: {e}")
        return None


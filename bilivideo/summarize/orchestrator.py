"""End-to-end note generation pipeline.

Orchestrates:
  1. Fetch transcript via the pipeline (subtitle preferred, BCut fallback).
  2. Build prompt and ask the configured LLM.
  3. Post-process (timestamp markers, smart truncation).
  4. Clean up downloaded audio (if any).

Each step is wrapped in fine-grained try/except blocks so we can surface
specific user-facing errors (`BiliVideoError.user_message`).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..api.client import BilibiliHTTPClient
from ..api.endpoints import get_video_info
from ..cache.lru_ttl import LRUTTLCache
from ..core.config import PluginConfig
from ..core.constants import (
    LLM_CHAT_TIMEOUT_SECONDS,
    SUMMARY_CACHE_MAX,
    SUMMARY_CACHE_TTL_SECONDS,
    TRANSCRIPT_CACHE_MAX,
    TRANSCRIPT_CACHE_TTL_SECONDS,
)
from ..core.exceptions import BiliVideoError
from ..core.logging import get_logger
from ..core.types import VideoInfo
from ..coolapk import CoolapkPost, build_coolapk_prompt
from ..llm.prompts import build_prompt
from ..llm.provider import LLMProvider
from ..parsing.url_extractor import extract_bvid
from ..subscription.store import JsonStore
from ..transcription.pipeline import PipelineOutput, TranscriptPipeline
from .post_process import replace_timestamp_markers, smart_truncate

logger = get_logger("BiliVideo/Summary")


@dataclass(slots=True)
class NoteResult:
    markdown: str
    video_info: VideoInfo | None
    used_subtitle: bool


class SummaryOrchestrator:
    """Coordinates pipeline + LLM + post-processing for a single URL."""

    def __init__(
        self,
        *,
        config: PluginConfig,
        llm: LLMProvider,
        pipeline: TranscriptPipeline,
        http_client: BilibiliHTTPClient,
        data_dir: str | None = None,
        coolapk_llm: LLMProvider | None = None,
    ) -> None:
        self._config = config
        self._llm = llm
        self._coolapk_llm = coolapk_llm or llm
        self._pipeline = pipeline
        self._http = http_client
        self._cache: LRUTTLCache[str, NoteResult] = LRUTTLCache(
            max_size=SUMMARY_CACHE_MAX, ttl_seconds=SUMMARY_CACHE_TTL_SECONDS
        )
        self._transcript_cache: LRUTTLCache[str, PipelineOutput] = LRUTTLCache(
            max_size=TRANSCRIPT_CACHE_MAX, ttl_seconds=TRANSCRIPT_CACHE_TTL_SECONDS
        )
        self._persistent_cache = (
            JsonStore(
                Path(data_dir) / "summary_cache.json",
                default={"entries": {}},
            )
            if data_dir
            else None
        )

    async def clear_cache(self) -> None:
        await self._cache.clear()
        await self._transcript_cache.clear()
        if self._persistent_cache is not None:
            await self._persistent_cache.mutate(
                lambda data: data.update({"entries": {}})
            )

    async def generate(self, video_url: str) -> NoteResult:
        """Run the pipeline under the configured processing timeout."""

        timeout = self._config.processing_timeout
        if timeout and timeout > 0:
            try:
                return await asyncio.wait_for(self._generate(video_url), timeout=timeout)
            except asyncio.TimeoutError as exc:
                logger.warning(f"summary generation timed out after {timeout}s: {video_url}")
                raise BiliVideoError(
                    f"processing timeout after {timeout}s",
                    user_message="❌ 处理超时,请稍后重试或换一个视频",
                ) from exc
        return await self._generate(video_url)

    async def _generate(self, video_url: str) -> NoteResult:
        bvid = extract_bvid(video_url)
        if bvid:
            cached = await self._cache.get(bvid)
            if cached is not None:
                logger.info(f"summary cache hit for {bvid}")
                return cached
            cached = await self._get_persistent_cache(bvid)
            if cached is not None:
                await self._cache.set(bvid, cached)
                logger.info(f"persistent summary cache hit for {bvid}")
                return cached
        info: VideoInfo | None = None
        if bvid:
            try:
                info = await get_video_info(self._http, bvid)
            except BiliVideoError as exc:
                logger.warning(f"video info lookup failed for {bvid}: {exc}")

        transcript_cache_key = bvid or video_url.strip()

        async def fetch_transcript() -> PipelineOutput:
            return await self._pipeline.fetch(
                video_url,
                prefer_subtitle=self._config.prefer_subtitle,
                enable_bilibili_ai_subtitle=self._config.enable_bilibili_ai_subtitle,
                quality=self._config.download_quality,
                subtitle_langs=self._config.subtitle_langs,
            )

        try:
            output = await self._transcript_cache.get_or_set(
                transcript_cache_key, fetch_transcript
            )
        except BiliVideoError:
            raise
        except Exception as exc:
            logger.error(f"transcript pipeline failed: {exc}", exc_info=True)
            raise BiliVideoError(
                f"transcript pipeline error: {exc}",
                user_message="❌ 转写流程异常,请稍后重试",
            ) from exc

        title = (output.audio.title if output.audio else (info.title if info else "")) or "视频总结"
        tags = ""
        if output.audio:
            raw_tags = (output.audio.raw_info or {}).get("tags")
            if isinstance(raw_tags, list):
                tags = ", ".join(str(t) for t in raw_tags)
            elif isinstance(raw_tags, str):
                tags = raw_tags

        prompt = build_prompt(
            title=title,
            segments=output.transcript.segments,
            tags=tags,
            style=self._config.note_style,
            enable_link=self._config.enable_link,
            enable_summary=self._config.enable_summary,
        )

        try:
            markdown = await asyncio.wait_for(
                self._llm.chat(prompt, session_id="BiliVideo_plugin"),
                timeout=LLM_CHAT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise BiliVideoError(
                f"LLM chat timed out after {LLM_CHAT_TIMEOUT_SECONDS}s",
                user_message="❌ AI 总结超时:当前对话模型无响应,请在 AstrBot 检查或更换对话模型",
            ) from exc
        finally:
            self._pipeline.cleanup_audio(output.audio)

        if not markdown:
            raise BiliVideoError("empty LLM output", user_message="❌ AI 返回内容为空,请重试")

        if self._config.enable_link and bvid:
            markdown = replace_timestamp_markers(markdown)

        markdown = smart_truncate(markdown, self._config.max_note_length)

        result = NoteResult(
            markdown=markdown,
            video_info=info,
            used_subtitle=output.audio is None,
        )
        if bvid:
            await self._cache.set(bvid, result)
            await self._set_persistent_cache(bvid, result)
        return result

    async def generate_coolapk(self, post: CoolapkPost) -> NoteResult:
        """Summarize a Coolapk post while preserving its original media for rendering."""
        prompt = build_coolapk_prompt(post, self._config.note_style)
        try:
            markdown = await asyncio.wait_for(
                self._coolapk_llm.chat(
                    prompt,
                    session_id="BiliVideo_coolapk",
                    image_urls=post.images,
                    model=getattr(self._config, "coolapk_summary_model", ""),
                ),
                timeout=LLM_CHAT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise BiliVideoError("coolapk LLM timeout", user_message="❌ AI 总结超时，请稍后重试") from exc
        if not markdown:
            raise BiliVideoError("empty Coolapk LLM output", user_message="❌ AI 返回内容为空，请重试")
        body = post.render_markdown + "\n\n## AI 总结\n\n" + markdown
        return NoteResult(markdown=body, video_info=None, used_subtitle=True)

    def set_coolapk_llm(self, provider: LLMProvider) -> None:
        self._coolapk_llm = provider

    async def _get_persistent_cache(self, bvid: str) -> NoteResult | None:
        if self._persistent_cache is None:
            return None

        entries = (await self._persistent_cache.read()).get("entries", {})
        entry = entries.get(bvid) if isinstance(entries, Mapping) else None
        if not isinstance(entry, Mapping):
            return None
        if int(entry.get("expires_at", 0) or 0) <= int(time.time()):
            await self._remove_persistent_cache_entry(bvid)
            return None

        try:
            raw_info = entry.get("video_info")
            video_info = VideoInfo(**raw_info) if isinstance(raw_info, Mapping) else None
            return NoteResult(
                markdown=str(entry["markdown"]),
                video_info=video_info,
                used_subtitle=bool(entry.get("used_subtitle", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"invalid persistent summary cache entry for {bvid}: {exc}")
            await self._remove_persistent_cache_entry(bvid)
            return None

    async def _set_persistent_cache(self, bvid: str, result: NoteResult) -> None:
        if self._persistent_cache is None:
            return

        payload: dict[str, Any] = {
            "expires_at": int(time.time()) + SUMMARY_CACHE_TTL_SECONDS,
            "markdown": result.markdown,
            "video_info": asdict(result.video_info) if result.video_info else None,
            "used_subtitle": result.used_subtitle,
        }
        # The API's raw metadata should be JSON already, but stringify any
        # unexpected value so caching never interrupts a completed summary.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))

        def mutate(data: dict[str, Any]) -> None:
            entries = data.setdefault("entries", {})
            if not isinstance(entries, dict):
                data["entries"] = entries = {}
            now = int(time.time())
            for key, entry in list(entries.items()):
                if not isinstance(entry, dict) or int(entry.get("expires_at", 0) or 0) <= now:
                    entries.pop(key, None)
            entries[bvid] = payload
            while len(entries) > SUMMARY_CACHE_MAX:
                entries.pop(next(iter(entries)))

        await self._persistent_cache.mutate(mutate)

    async def _remove_persistent_cache_entry(self, bvid: str) -> None:
        if self._persistent_cache is None:
            return

        def mutate(data: dict[str, Any]) -> None:
            entries = data.get("entries")
            if isinstance(entries, dict):
                entries.pop(bvid, None)

        await self._persistent_cache.mutate(mutate)

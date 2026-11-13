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
from dataclasses import asdict, dataclass, field
from hashlib import sha256
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
from ..zhihu import ZhihuPost
from ..llm.prompts import build_prompt, build_video_prompt
from ..llm.gemini_video import GeminiVideoProvider, resolve_gemini_settings
from ..llm.provider import LLMProvider
from ..parsing.url_extractor import extract_bvid
from ..subscription.store import JsonStore
from ..transcription.pipeline import PipelineOutput, TranscriptPipeline
from .post_process import replace_timestamp_markers, smart_truncate
from .video_media import VideoMediaService
from .adaptive_video import summarize_adaptive
from .relevance import (
    summary_conflicts_with_title,
    summary_conflicts_with_transcript,
    title_conflicts_with_text,
)

logger = get_logger("BiliVideo/Summary")
_SUMMARY_MISMATCH_MAX_RETRIES = 3
_SUMMARY_CACHE_VERSION = 4


@dataclass(slots=True)
class NoteResult:
    markdown: str
    video_info: VideoInfo | None
    used_subtitle: bool
    source_mode: str = "subtitle"
    processing_mode: str = ""
    fallback_reason: str = ""
    usage: dict = field(default_factory=dict)


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
        zhihu_llm: LLMProvider | None = None,
        video_llm: LLMProvider | None = None,
        video_media: VideoMediaService | None = None,
    ) -> None:
        self._config = config
        self._llm = llm
        self._coolapk_llm = coolapk_llm or llm
        self._zhihu_llm = zhihu_llm or llm
        self._video_llm = video_llm or llm
        self._video_media = video_media
        self._video_slots = asyncio.Semaphore(1)
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
        cache_key = self.cache_key(video_url)
        if bvid:
            cached = await self._cache.get(cache_key)
            if cached is not None and cached.video_info and summary_conflicts_with_title(
                cached.video_info.title, cached.markdown
            ):
                logger.warning(f"discarding off-topic memory summary for {bvid}")
                await self._cache.invalidate(cache_key)
                await self._remove_persistent_cache_entry(cache_key)
                cached = None
            if cached is not None:
                logger.info(f"summary cache hit for {bvid}")
                return cached
            cached = await self._get_persistent_cache(cache_key)
            if cached is not None:
                await self._cache.set(cache_key, cached)
                logger.info(f"persistent summary cache hit for {bvid}")
                return cached
        info: VideoInfo | None = None
        if bvid:
            try:
                info = await get_video_info(self._http, bvid)
            except BiliVideoError as exc:
                logger.warning(f"video info lookup failed for {bvid}: {exc}")

        if self._config.video_summary_mode == "subtitle":
            result = await self._generate_text(video_url, info)
        else:
            try:
                result = await self._generate_video(video_url, info)
            except BiliVideoError as exc:
                if not self._config.video_fallback:
                    raise
                logger.warning(f"video understanding failed ({type(exc).__name__}); falling back to transcript")
                result = await self._generate_text(video_url, info)
                result.fallback_reason = exc.user_message.lstrip("❌ ") + "，已改用字幕或音频转写总结"

        markdown = result.markdown
        if info and markdown.startswith("# "):
            _, newline, body = markdown.partition("\n")
            markdown = f"# {info.title} - {info.owner_name}{newline}{body}"
        if self._config.enable_link and bvid:
            markdown = replace_timestamp_markers(markdown)
        markdown = smart_truncate(markdown, self._config.max_note_length)
        if result.source_mode == "frames":
            if result.processing_mode == "adaptive":
                markdown += (
                    f"\n\n> 总结依据：字幕与模型按需查看的画面（共 {result.usage.get('video_frames', 0)} 张，"
                    f"补看 {result.usage.get('video_review_rounds', 0)} 轮）；无可用字幕时仅依据画面，未逐帧检查整段视频。"
                )
            else:
                markdown += "\n\n> 总结依据：字幕与抽样画面（无可用字幕时仅依据画面），未逐帧检查整段视频。"
        elif result.source_mode == "native":
            label = {"static": "Static", "agentic": "Agentic", "agentic_unverified": "Agentic（渠道未确认）"}
            markdown += f"\n\n> 总结依据：原生视频理解 · {label.get(result.processing_mode, result.processing_mode)}。"
        if result.fallback_reason:
            markdown += f"\n\n> {result.fallback_reason}。"
        result.markdown = markdown
        # Do not preserve a degraded result as if it satisfied the requested video mode.
        if bvid and not result.fallback_reason:
            await self._cache.set(cache_key, result)
            await self._set_persistent_cache(cache_key, result)
        return result

    def cache_key(self, video_url: str) -> str:
        keys = (
            "note_style", "enable_link", "enable_summary", "max_note_length", "prefer_subtitle",
            "enable_bilibili_ai_subtitle", "subtitle_langs", "download_quality", "video_summary_mode",
            "video_summary_provider", "video_processing_mode", "video_native_transport",
            "video_fallback", "video_frame_count", "video_frame_width",
            "video_max_duration", "video_max_size_mb", "video_frame_strategy",
            "video_preview_count", "video_max_rounds", "video_download_height",
        )
        signature = {key: getattr(self._config, key) for key in keys}
        signature["llm"] = getattr(self._llm, "cache_identity", type(self._llm).__name__)
        signature["video_llm"] = getattr(self._video_llm, "cache_identity", type(self._video_llm).__name__)
        digest = sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
        return f"{extract_bvid(video_url) or video_url.strip()}:{digest}"

    async def _generate_video(self, video_url: str, info: VideoInfo | None) -> NoteResult:
        if self._video_media is None:
            raise BiliVideoError("video media service unavailable", user_message="视频理解服务尚未初始化")
        if info and info.duration > self._config.video_max_duration:
            raise BiliVideoError("video duration limit", user_message="视频超过视频理解的时长上限")
        native = None
        warning = ""
        if self._config.video_summary_mode == "native":
            try:
                native = GeminiVideoProvider(
                    resolve_gemini_settings(self._video_llm),
                    timeout=min(self._config.processing_timeout * 0.6, LLM_CHAT_TIMEOUT_SECONDS),
                )
            except BiliVideoError as exc:
                if not self._config.video_fallback:
                    raise
                warning = exc.user_message.lstrip("❌ ") + "，已改用抽样画面"
        async with self._video_slots:
            async with self._video_media.prepare(video_url, self._config) as media:
                title = info.title if info else media.title
                if native is not None:
                    prompt = build_video_prompt(title=title, config=self._config, native=True)
                    try:
                        result = await native.summarize(media.file_path, prompt, self._config)
                        if summary_conflicts_with_title(title, result.text):
                            raise BiliVideoError("video summary topic mismatch", user_message="视频总结与标题不符")
                        return NoteResult(
                            result.text, info, False, "native", result.processing_mode, result.warning, result.usage,
                        )
                    except (BiliVideoError, asyncio.TimeoutError) as exc:
                        if not self._config.video_fallback:
                            if isinstance(exc, BiliVideoError):
                                raise
                            raise BiliVideoError("video timeout", user_message="原生视频总结超时") from exc
                        logger.warning(f"native video unavailable ({type(exc).__name__}); trying frames")
                        reason = exc.user_message.lstrip("❌ ") if isinstance(exc, BiliVideoError) else "原生视频总结超时"
                        warning = reason + "，已改用抽样画面"
                return await self._generate_frames(video_url, info, media, warning)

    async def _generate_frames(self, video_url: str, info, media, warning: str) -> NoteResult:
        output = None
        transcript_key = extract_bvid(video_url) or video_url.strip()
        try:
            async def fetch():
                return await self._pipeline.fetch(
                    video_url, prefer_subtitle=self._config.prefer_subtitle,
                    enable_bilibili_ai_subtitle=self._config.enable_bilibili_ai_subtitle,
                    quality=self._config.download_quality, subtitle_langs=self._config.subtitle_langs,
                )
            output = await self._transcript_cache.get_or_set(transcript_key, fetch)
            if title_conflicts_with_text(info.title if info else media.title, output.transcript.full_text):
                self._pipeline.cleanup_audio(output.audio)
                await self._transcript_cache.invalidate(transcript_key)
                output = await self._pipeline.fetch(video_url, quality=self._config.download_quality, force_asr=True)
                if title_conflicts_with_text(info.title if info else media.title, output.transcript.full_text):
                    raise BiliVideoError("visual transcript mismatch")
        except BiliVideoError:
            if output:
                self._pipeline.cleanup_audio(output.audio)
            output = None
            warning = (warning + "；" if warning else "") + "未获得可靠字幕，本次仅依据抽样画面"
        try:
            if self._config.video_frame_strategy == "adaptive":
                result = await summarize_adaptive(
                    llm=self._video_llm, media_service=self._video_media, media=media,
                    config=self._config, title=info.title if info else media.title,
                    segments=output.transcript.segments if output else (),
                )
                if not result.text or summary_conflicts_with_title(info.title if info else media.title, result.text):
                    raise BiliVideoError("invalid visual summary", user_message="画面总结为空或与视频内容不符")
                if result.warning:
                    warning = (warning + "；" if warning else "") + result.warning
                return NoteResult(
                    result.text, info, output is not None and output.audio is None,
                    "frames", "adaptive", warning, result.usage,
                )
            frames = await self._video_media.frames(media, self._config)
            prompt = build_video_prompt(
                title=info.title if info else media.title, config=self._config,
                segments=output.transcript.segments if output else (),
                timestamps=tuple(frame.timestamp for frame in frames),
            )
            markdown = await asyncio.wait_for(
                self._video_llm.chat(
                    prompt, session_id="BiliVideo_video", image_urls=tuple(frame.image_url for frame in frames),
                ), timeout=LLM_CHAT_TIMEOUT_SECONDS,
            )
            if not markdown or summary_conflicts_with_title(info.title if info else media.title, markdown):
                raise BiliVideoError("invalid visual summary", user_message="画面总结为空或与视频内容不符")
            return NoteResult(markdown, info, output is not None and output.audio is None, "frames", "", warning)
        except asyncio.TimeoutError as exc:
            raise BiliVideoError("visual LLM timeout", user_message="画面总结超时") from exc
        finally:
            if output:
                self._pipeline.cleanup_audio(output.audio)

    async def _generate_text(self, video_url: str, info: VideoInfo | None) -> NoteResult:
        bvid = extract_bvid(video_url)

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

        summary_retries = 0
        asr_refresh_attempted = False
        while True:
            title = (info.title if info else (output.audio.title if output.audio else "")) or "视频总结"
            transcript_text = output.transcript.full_text
            transcript_mismatch = title_conflicts_with_text(title, transcript_text)
            if transcript_mismatch and output.audio is not None:
                self._pipeline.cleanup_audio(output.audio)
                await self._transcript_cache.invalidate(transcript_cache_key)
                raise BiliVideoError(
                    "audio transcript does not match video title",
                    user_message="❌ 音频转写与视频标题不符，已停止生成总结；请稍后重试",
                )
            if transcript_mismatch:
                logger.warning(f"subtitle topic mismatch for {bvid or video_url}; retrying with audio ASR")
            else:
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
                    max_length=self._config.max_note_length,
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
                if not (
                    summary_conflicts_with_title(title, markdown)
                    or summary_conflicts_with_transcript(transcript_text, markdown)
                ):
                    break
                summary_retries += 1
                logger.warning(
                    f"summary topic mismatch for {bvid or video_url}; "
                    f"retry {summary_retries}/{_SUMMARY_MISMATCH_MAX_RETRIES}"
                )
                if summary_retries > _SUMMARY_MISMATCH_MAX_RETRIES:
                    await self._transcript_cache.invalidate(transcript_cache_key)
                    raise BiliVideoError(
                        "summary does not match video content",
                        user_message="❌ 总结与视频内容不符，已停止发送；请稍后重试",
                    )

            if output.audio is None and not asr_refresh_attempted:
                asr_refresh_attempted = True
                # Do not retain rejected subtitles if the ASR fallback fails.
                await self._transcript_cache.invalidate(transcript_cache_key)
                output = await self._pipeline.fetch(
                    video_url,
                    quality=self._config.download_quality,
                    force_asr=True,
                )
                await self._transcript_cache.set(transcript_cache_key, output)
            # With audio already transcribed, retry the LLM with the same transcript.

        result = NoteResult(
            markdown=markdown,
            video_info=info,
            used_subtitle=output.audio is None,
        )
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

    def set_zhihu_llm(self, provider: LLMProvider) -> None:
        self._zhihu_llm = provider

    async def generate_zhihu(self, post: ZhihuPost) -> NoteResult:
        prompt = (
            "你是中文内容总结助手。请根据下面的知乎文本帖原文和图片，输出结构化、准确、易读的总结。"
            "先给出一句话结论，再列出关键观点、事实/数据、争议或局限，最后给出适合普通读者的阅读建议。"
            "不要编造原文没有的信息；图片中的文字、表格和图示也要纳入判断。\n\n"
            f"标题：{post.title}\n作者：{post.author}\n原文：\n{post.content}"
        )
        try:
            markdown = await asyncio.wait_for(
                self._zhihu_llm.chat(
                    prompt,
                    session_id="BiliVideo_zhihu",
                    image_urls=post.images,
                ),
                timeout=LLM_CHAT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise BiliVideoError("zhihu LLM timeout", user_message="❌ AI 总结超时，请稍后重试") from exc
        if not markdown:
            raise BiliVideoError("empty Zhihu LLM output", user_message="❌ AI 返回内容为空，请重试")
        return NoteResult(
            markdown=post.render_markdown + "\n\n## AI 总结\n\n" + smart_truncate(markdown, self._config.max_note_length),
            video_info=None,
            used_subtitle=True,
        )

    async def _get_persistent_cache(self, bvid: str) -> NoteResult | None:
        if self._persistent_cache is None:
            return None

        entries = (await self._persistent_cache.read()).get("entries", {})
        entry = entries.get(bvid) if isinstance(entries, Mapping) else None
        if not isinstance(entry, Mapping):
            return None
        if entry.get("version") != _SUMMARY_CACHE_VERSION:
            await self._remove_persistent_cache_entry(bvid)
            return None
        if int(entry.get("expires_at", 0) or 0) <= int(time.time()):
            await self._remove_persistent_cache_entry(bvid)
            return None

        try:
            raw_info = entry.get("video_info")
            video_info = VideoInfo(**raw_info) if isinstance(raw_info, Mapping) else None
            if video_info and summary_conflicts_with_title(
                video_info.title, str(entry["markdown"])
            ):
                logger.warning(f"discarding off-topic persistent summary for {bvid}")
                await self._remove_persistent_cache_entry(bvid)
                return None
            return NoteResult(
                markdown=str(entry["markdown"]),
                video_info=video_info,
                used_subtitle=bool(entry.get("used_subtitle", False)),
                source_mode=str(entry.get("source_mode", "subtitle")),
                processing_mode=str(entry.get("processing_mode", "")),
                fallback_reason=str(entry.get("fallback_reason", "")),
                usage=entry.get("usage", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"invalid persistent summary cache entry for {bvid}: {exc}")
            await self._remove_persistent_cache_entry(bvid)
            return None

    async def _set_persistent_cache(self, bvid: str, result: NoteResult) -> None:
        if self._persistent_cache is None:
            return

        payload: dict[str, Any] = {
            "version": _SUMMARY_CACHE_VERSION,
            "expires_at": int(time.time()) + SUMMARY_CACHE_TTL_SECONDS,
            "markdown": result.markdown,
            "video_info": asdict(result.video_info) if result.video_info else None,
            "used_subtitle": result.used_subtitle,
            "source_mode": result.source_mode,
            "processing_mode": result.processing_mode,
            "fallback_reason": result.fallback_reason,
            "usage": result.usage,
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

"""Transcript acquisition pipeline.

Strategy:
  1. Try platform subtitles via yt-dlp (cheap, fast).
  2. If unavailable (or `prefer_subtitle=False`), download audio and run
     BCut ASR.
  3. Return the first non-empty `TranscriptResult`, plus the audio metadata
     so the caller can clean up the file.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

from ..core.constants import (
    ASR_TIMEOUT_SECONDS,
    AUDIO_DOWNLOAD_TIMEOUT_SECONDS,
    SUBTITLE_FETCH_TIMEOUT_SECONDS,
)
from ..core.exceptions import BiliVideoError, TranscriptionError
from ..core.logging import get_logger
from ..api.client import BilibiliHTTPClient
from ..api.endpoints import get_bilibili_ai_subtitle
from ..core.types import AudioDownloadResult, TranscriptResult
from ..downloader.ytdlp_downloader import YtDlpDownloader
from .bcut_provider import BCutTranscriber

logger = get_logger("BiliVideo/Pipeline")


@dataclass(slots=True)
class PipelineOutput:
    transcript: TranscriptResult
    audio: AudioDownloadResult | None  # set when we downloaded audio for ASR


class TranscriptPipeline:
    """Coordinates yt-dlp + BCut to obtain a `TranscriptResult`."""

    def __init__(
        self,
        downloader: YtDlpDownloader,
        transcriber: BCutTranscriber,
        http_client: BilibiliHTTPClient | None = None,
    ) -> None:
        self._downloader = downloader
        self._transcriber = transcriber
        self._http_client = http_client

    async def fetch(
        self,
        video_url: str,
        *,
        prefer_subtitle: bool = True,
        quality: str = "fast",
        subtitle_langs: tuple[str, ...] | None = None,
        enable_bilibili_ai_subtitle: bool = True,
    ) -> PipelineOutput:
        loop = asyncio.get_running_loop()
        transcript: TranscriptResult | None = None
        audio_meta: AudioDownloadResult | None = None

        if prefer_subtitle:
            try:
                transcript = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self._downloader.download_subtitles(video_url, langs=subtitle_langs),
                    ),
                    timeout=SUBTITLE_FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise BiliVideoError(
                    f"subtitle fetch timed out after {SUBTITLE_FETCH_TIMEOUT_SECONDS}s",
                    user_message="❌ 字幕获取超时(B 站访问慢,或需重新 /B站登录)",
                ) from exc
            if transcript and transcript.has_content:
                logger.info(f"subtitle hit ({len(transcript.segments)} segments)")
                return PipelineOutput(transcript=transcript, audio=None)
            transcript = await self._fetch_bilibili_ai_subtitle(
                video_url,
                subtitle_langs=subtitle_langs,
                enabled=enable_bilibili_ai_subtitle,
            )
            if transcript and transcript.has_content:
                return PipelineOutput(transcript=transcript, audio=None)
            logger.info("no platform subtitle or Bilibili AI subtitle, fall back to ASR")

        try:
            audio_meta = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: self._downloader.download_audio(video_url, quality=quality)
                ),
                timeout=AUDIO_DOWNLOAD_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise BiliVideoError(
                f"audio download timed out after {AUDIO_DOWNLOAD_TIMEOUT_SECONDS}s",
                user_message="❌ 音频下载超时(视频较长或 B 站访问慢)",
            ) from exc

        if not prefer_subtitle:
            transcript = await loop.run_in_executor(
                None,
                lambda: self._downloader.download_subtitles(video_url, langs=subtitle_langs),
            )
            if transcript is None or not transcript.has_content:
                transcript = await self._fetch_bilibili_ai_subtitle(
                    video_url,
                    subtitle_langs=subtitle_langs,
                    enabled=enable_bilibili_ai_subtitle,
                )

        if transcript is None or not transcript.has_content:
            cancel_event = threading.Event()
            try:
                transcript = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, self._transcriber.transcribe, audio_meta.file_path, cancel_event
                    ),
                    timeout=ASR_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                cancel_event.set()
                raise BiliVideoError(
                    f"ASR timed out after {ASR_TIMEOUT_SECONDS}s",
                    user_message="❌ 语音转写(ASR)超时(视频较长或转写服务繁忙)",
                ) from exc
            except asyncio.CancelledError:
                cancel_event.set()
                raise

        if transcript is None or not transcript.has_content:
            raise TranscriptionError("subtitle and BCut both yielded empty transcripts")

        return PipelineOutput(transcript=transcript, audio=audio_meta)

    async def _fetch_bilibili_ai_subtitle(
        self,
        video_url: str,
        *,
        subtitle_langs: tuple[str, ...] | None,
        enabled: bool,
    ) -> TranscriptResult | None:
        if not enabled or self._http_client is None:
            return None
        return await get_bilibili_ai_subtitle(
            self._http_client, video_url, langs=subtitle_langs
        )

    @staticmethod
    def cleanup_audio(audio_meta: AudioDownloadResult | None) -> None:
        if not audio_meta or not audio_meta.file_path:
            return
        path = Path(audio_meta.file_path)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning(f"audio cleanup failed: {exc}")

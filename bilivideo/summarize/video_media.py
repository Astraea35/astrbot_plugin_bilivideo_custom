"""Temporary video files and bounded frame sampling using the existing FFmpeg dependency."""

from __future__ import annotations

import asyncio
import base64
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from ..core.config import PluginConfig
from ..core.exceptions import BiliVideoError
from ..downloader.ytdlp_downloader import YtDlpDownloader, _ffmpeg_location


@dataclass(frozen=True)
class VideoMedia:
    file_path: str
    duration: float
    title: str


@dataclass(frozen=True)
class VideoFrame:
    timestamp: float
    image_url: str


class VideoMediaService:
    def __init__(self, downloader: YtDlpDownloader, data_dir: str) -> None:
        self.downloader = downloader
        self.root = Path(data_dir) / "video"

    @asynccontextmanager
    async def prepare(self, video_url: str, config: PluginConfig):
        self.root.mkdir(parents=True, exist_ok=True)
        # Each request owns its directory; no shared download filename can be deleted by another job.
        with TemporaryDirectory(prefix="summary-", dir=self.root) as directory:
            cancel = threading.Event()
            worker = asyncio.create_task(asyncio.to_thread(
                self.downloader.download_video, video_url, output_dir=directory,
                max_duration=config.video_max_duration,
                max_size_bytes=config.video_max_size_mb * 1024 * 1024,
                cancel_event=cancel,
            ))
            try:
                metadata = await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancel.set()
                # A cancelled asyncio waiter does not stop a thread. Drain it before deleting its files.
                try:
                    await worker
                except Exception:
                    pass
                raise
            except OSError as exc:
                raise BiliVideoError("video file I/O failed", user_message="视频文件读取或写入失败") from exc
            media = VideoMedia(**metadata)
            if not 0 < media.duration <= config.video_max_duration:
                raise BiliVideoError("invalid video duration", user_message="无法确认视频时长或视频超过时长上限")
            yield media

    async def frames(self, media: VideoMedia, config: PluginConfig) -> tuple[VideoFrame, ...]:
        location = _ffmpeg_location()
        if not location:
            raise BiliVideoError("ffmpeg unavailable", user_message="缺少 FFmpeg，无法提取视频画面")
        binary = Path(location)
        if binary.is_dir():
            binary = binary / "ffmpeg"
        count = min(config.video_frame_count, max(1, int(media.duration)))
        frames: list[VideoFrame] = []
        for index in range(count):
            timestamp = media.duration * (index + 0.5) / count
            destination = Path(media.file_path).parent / f"frame-{index}.jpg"
            try:
                process = await asyncio.create_subprocess_exec(
                    str(binary), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{timestamp:.3f}", "-i", media.file_path, "-frames:v", "1",
                    "-vf", f"scale=min({config.video_frame_width}\\,iw):-2", "-q:v", "4",
                    "-threads", "1", str(destination),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                raise BiliVideoError("ffmpeg launch failed", user_message="无法启动 FFmpeg 提取画面") from exc
            try:
                await asyncio.wait_for(process.wait(), timeout=30)
            except asyncio.TimeoutError as exc:
                raise BiliVideoError("frame extraction timeout", user_message="视频画面提取超时") from exc
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode != 0 or not destination.is_file():
                raise BiliVideoError("frame extraction failed", user_message="视频画面提取失败")
            encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
            frames.append(VideoFrame(timestamp, 'data:image/jpeg;base64,' + encoded))
        return tuple(frames)

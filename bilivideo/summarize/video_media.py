"""Temporary video files and bounded frame sampling using the existing FFmpeg dependency."""

from __future__ import annotations

import asyncio
import base64
import math
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from ..core.config import PluginConfig
from ..core.exceptions import BiliVideoError
from ..core.logging import get_logger
from ..downloader.ytdlp_downloader import YtDlpDownloader, _ffmpeg_location

logger = get_logger("BiliVideo/VideoMedia")


@dataclass(frozen=True)
class VideoMedia:
    file_path: str
    duration: float
    title: str


@dataclass(frozen=True)
class VideoFrame:
    timestamp: float
    image_url: str
    crop: tuple[float, float, float, float] | None = None


def preview_times(duration: float, count: int, scenes=()) -> tuple[float, ...]:
    """Keep timeline coverage while reserving half the previews for scene changes."""
    count = min(count, max(1, int(duration)))
    candidates = sorted({min(float(stamp) + 0.2, duration - 0.05) for stamp in scenes
                         if math.isfinite(float(stamp)) and 0 <= float(stamp) < duration})
    wanted = min(len(candidates), count // 2)
    selected = [candidates[int((index + 0.5) * len(candidates) / wanted)] for index in range(wanted)]
    selected.extend(duration * (index + 0.5) / (count - wanted) for index in range(count - wanted))
    return tuple(sorted({round(stamp, 3) for stamp in selected}))


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
                max_height=config.video_download_height,
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

    @staticmethod
    def _binary() -> str:
        location = _ffmpeg_location()
        if not location:
            raise BiliVideoError("ffmpeg unavailable", user_message="缺少 FFmpeg，无法提取视频画面")
        binary = Path(location)
        if binary.is_dir():
            binary = binary / "ffmpeg"
        return str(binary)

    async def scene_times(self, media: VideoMedia) -> tuple[float, ...]:
        """Bounded one-FPS low-resolution scan; no model request and no persistent files."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._binary(), "-nostdin", "-hide_banner", "-loglevel", "info", "-i", media.file_path,
                "-vf", "fps=1,scale=160:-2,select=gt(scene\\,0.25),showinfo", "-frames:v", "256",
                "-an", "-f", "null", "-", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, errors = await asyncio.wait_for(process.communicate(), timeout=45)
            if process.returncode == 0:
                return tuple(float(value) for value in re.findall(rb"pts_time:([\d.]+)", errors))[:256]
        except (OSError, asyncio.TimeoutError):
            logger.info("scene scan unavailable; using timeline previews")
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
        return ()

    async def frames(self, media: VideoMedia, config: PluginConfig) -> tuple[VideoFrame, ...]:
        count = min(config.video_frame_count, max(1, int(media.duration)))
        times = tuple(media.duration * (index + 0.5) / count for index in range(count))
        return await self.extract(media, times, width=config.video_frame_width)

    async def previews(self, media: VideoMedia, config: PluginConfig) -> tuple[VideoFrame, ...]:
        count = min(config.video_preview_count, config.video_frame_count)
        scenes = await self.scene_times(media)
        return await self.extract(media, preview_times(media.duration, count, scenes),
                                  width=min(config.video_frame_width, 640))

    async def extract(self, media: VideoMedia, timestamps, *, width: int, crops=None) -> tuple[VideoFrame, ...]:
        if len(timestamps) > 96 or not 320 <= width <= 1920:
            raise BiliVideoError("invalid frame request", user_message="画面请求超出范围")
        crops = tuple(crops) if crops is not None else (None,) * len(timestamps)
        if len(crops) != len(timestamps):
            raise BiliVideoError("invalid crop request")
        frames: list[VideoFrame] = []
        for timestamp, crop in zip(timestamps, crops):
            if isinstance(timestamp, bool) or not math.isfinite(timestamp) or not 0 <= timestamp < media.duration:
                raise BiliVideoError("invalid frame timestamp")
            filters = []
            if crop is not None:
                if (len(crop) != 4 or any(isinstance(value, bool) or not math.isfinite(value) for value in crop)
                        or min(crop[:2]) < 0 or min(crop[2:]) < 0.05
                        or crop[0] + crop[2] > 1 or crop[1] + crop[3] > 1):
                    raise BiliVideoError("invalid frame crop")
                x, y, w, h = crop
                filters.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")
            filters.append(f"scale=min({width}\\,iw):-2")
            identity = sha256(repr((timestamp, width, crop)).encode()).hexdigest()[:16]
            destination = Path(media.file_path).parent / f"frame-{identity}.jpg"
            try:
                process = await asyncio.create_subprocess_exec(
                    self._binary(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{timestamp:.3f}", "-i", media.file_path, "-frames:v", "1",
                    "-vf", ",".join(filters), "-q:v", "3",
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
            try:
                encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
            except OSError as exc:
                raise BiliVideoError("frame file I/O failed", user_message="补看画面文件读取失败") from exc
            frames.append(VideoFrame(timestamp, 'data:image/jpeg;base64,' + encoded, crop))
        return tuple(frames)

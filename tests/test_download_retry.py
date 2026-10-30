"""Transient TLS failures recover without retrying authentication or conversion errors."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yt_dlp

from bilivideo.core.exceptions import BiliVideoError, DownloadError
from bilivideo.downloader import ytdlp_downloader as downloader
from bilivideo.transcription import pipeline


@pytest.mark.parametrize("method", ["download_audio", "download_subtitles"])
def test_initial_tls_handshake_retries_before_audio_or_subtitle_success(monkeypatch, tmp_path, method):
    info = {
        "id": "BVtest", "title": "test", "duration": 1,
        "requested_subtitles": {"zh": {"data": "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n\n"}},
    }
    extract = Mock(side_effect=[
        yt_dlp.utils.DownloadError("Unable to download webpage: [SSL: UNEXPECTED_EOF_WHILE_READING]"),
        yt_dlp.utils.DownloadError("Unable to download JSON metadata: Connection reset by peer"), info,
    ])
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(extract_info=extract))
    factory.return_value.__exit__ = Mock(return_value=False)
    sleep = Mock()
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(downloader.time, "sleep", sleep)
    monkeypatch.setattr(downloader, "_ffmpeg_location", lambda: None)
    result = getattr(downloader.YtDlpDownloader(tmp_path), method)("https://www.bilibili.com/video/BVtest")
    assert result is not None
    assert extract.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [1, 2]
    if method == "download_audio":
        assert result.video_id == "BVtest"
    else:
        assert result.full_text == "测试字幕"


@pytest.mark.parametrize("error", [
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
    "HTTP Error 403: Forbidden", "HTTP Error 412: Precondition Failed",
    "Postprocessing: unable to obtain file audio codec with ffprobe",
])
def test_permanent_failure_is_not_retried(monkeypatch, tmp_path, error):
    extract = Mock(side_effect=yt_dlp.utils.DownloadError(error))
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(extract_info=extract))
    factory.return_value.__exit__ = Mock(return_value=False)
    sleep = Mock()
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(downloader.time, "sleep", sleep)
    monkeypatch.setattr(downloader, "_ffmpeg_location", lambda: None)
    with pytest.raises(DownloadError) as caught:
        downloader.YtDlpDownloader(tmp_path).download_audio("https://www.bilibili.com/video/BVtest")
    assert not caught.value.retryable
    assert extract.call_count == 1
    sleep.assert_not_called()


def test_exhausted_connection_retries_remain_eligible_for_delayed_recovery(monkeypatch, tmp_path):
    extract = Mock(side_effect=yt_dlp.utils.DownloadError("Unable to download webpage: Remote end closed connection without response"))
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(extract_info=extract))
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(downloader.time, "sleep", Mock())
    monkeypatch.setattr(downloader, "_ffmpeg_location", lambda: None)
    with pytest.raises(DownloadError) as caught:
        downloader.YtDlpDownloader(tmp_path).download_audio("https://www.bilibili.com/video/BVtest")
    assert caught.value.retryable
    assert extract.call_count == 3


def test_media_transfer_retries_are_not_multiplied_by_extractor_retries(monkeypatch, tmp_path):
    extract = Mock(side_effect=yt_dlp.utils.DownloadError("[download] Got error: Read timed out."))
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(extract_info=extract))
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(downloader.time, "sleep", Mock())
    monkeypatch.setattr(downloader, "_ffmpeg_location", lambda: None)
    with pytest.raises(DownloadError) as caught:
        downloader.YtDlpDownloader(tmp_path).download_audio("https://www.bilibili.com/video/BVtest")
    assert caught.value.retryable
    assert extract.call_count == 1
    assert factory.call_args.args[0]["retries"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prefer_subtitle", [True, False])
async def test_download_stage_timeout_is_eligible_for_recovery(monkeypatch, prefer_subtitle):
    async def timed_out(future, *, timeout):
        future.cancel()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(pipeline.asyncio, "wait_for", timed_out)
    stub = SimpleNamespace(download_subtitles=lambda *_a, **_k: None, download_audio=lambda *_a, **_k: None)
    worker = pipeline.TranscriptPipeline(stub, None)
    with pytest.raises(BiliVideoError) as caught:
        await worker.fetch("https://www.bilibili.com/video/BVtest", prefer_subtitle=prefer_subtitle)
    assert caught.value.retryable
    assert ("字幕获取超时" if prefer_subtitle else "音频下载超时") in caught.value.user_message


def test_ffmpeg_location_is_resolved_again_after_path_changes(monkeypatch, tmp_path):
    executable = [tmp_path / "first" / "ffmpeg.exe"]
    monkeypatch.setattr(downloader.shutil, "which", lambda _name: str(executable[0]))
    assert downloader._ffmpeg_location() == str(tmp_path / "first")
    executable[0] = tmp_path / "second" / "ffmpeg.exe"
    assert downloader._ffmpeg_location() == str(tmp_path / "second")


def test_ffmpeg_location_uses_bundled_binary_when_path_is_empty(monkeypatch, tmp_path):
    binary = str(tmp_path / "bundled-ffmpeg.exe")
    monkeypatch.setattr(downloader.shutil, "which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", SimpleNamespace(get_ffmpeg_exe=lambda: binary))
    assert downloader._ffmpeg_location() == binary

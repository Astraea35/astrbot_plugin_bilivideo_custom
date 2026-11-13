"""Video routing, proxy compatibility, bounded media handling and cleanup."""

from __future__ import annotations

import asyncio
import base64
import io
import shutil
import subprocess
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from PIL import Image

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import BiliVideoError, DownloadError, LLMError, TranscriptionError
from bilivideo.core.types import TranscriptResult, TranscriptSegment, VideoInfo
from bilivideo.downloader import ytdlp_downloader
from bilivideo.llm.astrbot_provider import AstrbotProvider
from bilivideo.llm.gemini_video import (
    GeminiVideoProvider, GeminiVideoSettings, NativeVideoResult, VideoCapabilityError, resolve_gemini_settings,
)
from bilivideo.summarize.orchestrator import SummaryOrchestrator
from bilivideo.summarize.video_media import VideoFrame, VideoMedia, VideoMediaService
from bilivideo.transcription.pipeline import PipelineOutput
from bilivideo.webui import PluginWebUI


URL = "https://www.bilibili.com/video/BV1xx411c7mD"
INFO = VideoInfo(bvid="BV1xx411c7mD", title="测试视频", owner_name="UP", duration=30)
ANSWER = "# 测试视频 - UP\n\n## 内容\n字幕与图表展示了测试结果。"
IMAGE_PREFIX = "data:" + "image/jpeg;base64,"


class FakeMedia:
    def __init__(self, error=None):
        self.error = error
        self.prepared = self.cleaned = 0

    @asynccontextmanager
    async def prepare(self, url, config):
        self.prepared += 1
        try:
            if self.error:
                raise self.error
            yield VideoMedia("test.mp4", 30, INFO.title)
        finally:
            self.cleaned += 1

    async def frames(self, media, config):
        return (VideoFrame(7.5, IMAGE_PREFIX + "eA=="), VideoFrame(22.5, IMAGE_PREFIX + "eQ=="))


def make_orchestrator(monkeypatch, tmp_path, mode="frames", **settings):
    monkeypatch.setattr("bilivideo.summarize.orchestrator.get_video_info", AsyncMock(return_value=INFO))
    transcript = TranscriptResult(
        language="zh", full_text="字幕讲解了图表和测试结果。",
        segments=(TranscriptSegment(0, 30, "字幕讲解了图表和测试结果。"),),
    )
    pipeline = SimpleNamespace(fetch=AsyncMock(return_value=PipelineOutput(transcript, None)), cleanup_audio=lambda audio: None)
    llm = SimpleNamespace(chat=AsyncMock(return_value=ANSWER), cache_identity="model-a")
    media = FakeMedia()
    config = PluginConfig.from_mapping({"video_summary_mode": mode, **settings})
    return SummaryOrchestrator(
        config=config, llm=llm, pipeline=pipeline, video_media=media,
        http_client=object(), data_dir=str(tmp_path),
    ), llm, pipeline, media


def test_video_config_validation_and_defaults():
    assert PluginConfig().video_summary_mode == "subtitle"
    config = PluginConfig.from_mapping({"video": {
        "video_summary_mode": "bad", "video_processing_mode": "bad", "video_frame_count": 100,
        "video_frame_width": 2, "video_max_duration": -1, "video_max_size_mb": 9999,
    }})
    assert (config.video_summary_mode, config.video_processing_mode) == ("subtitle", "auto")
    assert (config.video_frame_count, config.video_frame_width) == (24, 320)
    assert (config.video_max_duration, config.video_max_size_mb) == (10, 512)


@pytest.mark.asyncio
async def test_subtitle_mode_never_downloads_video(monkeypatch, tmp_path):
    orch, llm, _, media = make_orchestrator(monkeypatch, tmp_path, mode="subtitle")
    note = await orch.generate(URL)
    assert note.markdown == ANSWER
    assert media.prepared == 0
    assert "image_urls" not in llm.chat.call_args.kwargs


@pytest.mark.asyncio
async def test_frames_send_ordered_timestamps_and_images(monkeypatch, tmp_path):
    orch, llm, _, media = make_orchestrator(monkeypatch, tmp_path, video_summary_model="removed")
    note = await orch.generate(URL)
    assert note.source_mode == "frames"
    assert "抽样画面" in note.markdown
    assert "第1张=00:07" in llm.chat.call_args.args[0]
    assert "第2张=00:22" in llm.chat.call_args.args[0]
    assert len(llm.chat.call_args.kwargs["image_urls"]) == 2
    assert "model" not in llm.chat.call_args.kwargs
    assert media.prepared == media.cleaned == 1


@pytest.mark.asyncio
async def test_frames_can_summarize_silent_video_without_fabricated_audio(monkeypatch, tmp_path):
    orch, llm, pipeline, media = make_orchestrator(monkeypatch, tmp_path)
    pipeline.fetch.side_effect = TranscriptionError("no speech")
    note = await orch.generate(URL)
    assert note.source_mode == "frames" and not note.used_subtitle
    assert "仅依据抽样画面" in note.fallback_reason
    assert "不得声称听到了声音" in llm.chat.call_args.args[0]
    assert media.cleaned == 1


@pytest.mark.asyncio
async def test_missing_native_endpoint_falls_back_to_frames(monkeypatch, tmp_path):
    orch, llm, _, media = make_orchestrator(monkeypatch, tmp_path, mode="native")
    note = await orch.generate(URL)
    assert note.source_mode == "frames"
    assert "未配置 Gemini 原生视频接口" in note.fallback_reason
    assert media.prepared == 1
    assert "image_urls" in llm.chat.call_args.kwargs
    assert not (await orch._persistent_cache.read())["entries"]


@pytest.mark.asyncio
async def test_text_only_vision_failure_falls_back_without_images(monkeypatch, tmp_path):
    orch, llm, _, media = make_orchestrator(monkeypatch, tmp_path)

    async def chat(prompt, **kwargs):
        if kwargs.get("image_urls"):
            raise LLMError("images not supported")
        return ANSWER

    llm.chat.side_effect = chat
    note = await orch.generate(URL)
    assert note.source_mode == "subtitle"
    assert "已改用字幕" in note.fallback_reason
    assert llm.chat.await_count == 2
    assert media.cleaned == 1


@pytest.mark.asyncio
async def test_disabled_fallback_stops_on_native_capability_error(monkeypatch, tmp_path):
    orch, llm, pipeline, media = make_orchestrator(monkeypatch, tmp_path, mode="native", video_fallback=False)
    with pytest.raises(VideoCapabilityError):
        await orch.generate(URL)
    assert media.prepared == 0
    llm.chat.assert_not_awaited()
    pipeline.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_duration_limit_avoids_video_download_and_can_fallback(monkeypatch, tmp_path):
    orch, _, _, media = make_orchestrator(monkeypatch, tmp_path, video_max_duration=10)
    note = await orch.generate(URL)
    assert note.source_mode == "subtitle" and media.prepared == 0


@pytest.mark.asyncio
async def test_cache_isolates_mode_model_settings_and_preserves_evidence(monkeypatch, tmp_path):
    orch, llm, _, media = make_orchestrator(monkeypatch, tmp_path, mode="subtitle")
    first = await orch.generate(URL)
    assert await orch.generate(URL) is first
    assert llm.chat.await_count == 1
    first_key = orch.cache_key(URL)
    orch._config = replace(orch._config, video_summary_mode="frames")
    note = await orch.generate(URL)
    assert note.source_mode == "frames" and llm.chat.await_count == 2
    assert media.prepared == 1
    assert first_key != orch.cache_key(URL)
    saved = await orch._get_persistent_cache(orch.cache_key(URL))
    assert saved.source_mode == "frames" and saved.markdown == note.markdown
    old_key = orch.cache_key(URL)
    llm.cache_identity = "model-b"
    assert old_key != orch.cache_key(URL)


@pytest.mark.asyncio
async def test_native_summary_skips_transcription_and_frames(monkeypatch, tmp_path):
    orch, llm, pipeline, media = make_orchestrator(monkeypatch, tmp_path, mode="native")
    monkeypatch.setattr("bilivideo.summarize.orchestrator.resolve_gemini_settings", lambda *args: object())
    call = AsyncMock(return_value=NativeVideoResult(ANSWER, "static", usage={"total_tokens": 123}))
    monkeypatch.setattr("bilivideo.summarize.orchestrator.GeminiVideoProvider", lambda *args, **kwargs: SimpleNamespace(summarize=call))
    note = await orch.generate(URL)
    assert note.source_mode == "native" and note.processing_mode == "static"
    assert note.usage == {"total_tokens": 123}
    assert media.cleaned == 1
    pipeline.fetch.assert_not_awaited()
    llm.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_astrbot_current_text_chat_receives_images():
    method = AsyncMock(return_value=SimpleNamespace(completion_text=ANSWER))
    provider = SimpleNamespace(text_chat=method)
    adapter = AstrbotProvider(SimpleNamespace(get_using_provider=lambda: provider))
    assert await adapter.chat("test", image_urls=("image",)) == ANSWER
    assert method.call_args.kwargs["image_urls"] == ["image"]


@pytest.mark.asyncio
async def test_single_image_adapter_does_not_silently_drop_extra_frames():
    async def image_chat(*, prompt, image_url):
        raise AssertionError("single-image method must not be called with multiple frames")

    provider = SimpleNamespace(image_chat=image_chat)
    adapter = AstrbotProvider(SimpleNamespace(get_using_provider=lambda: provider))
    with pytest.raises(LLMError, match="only one image"):
        await adapter.chat("test", image_urls=("frame-one", "frame-two"))


def test_resolve_native_settings_does_not_guess_reverse_proxy_routes():
    selected = SimpleNamespace(
        provider_config={"type": "openai_chat_completion", "api_base": "https://proxy.example/v1"},
        get_model=lambda: "gemini-alias", get_current_key=lambda: "secret",
    )
    provider = AstrbotProvider(SimpleNamespace(get_using_provider=lambda: selected))
    with pytest.raises(VideoCapabilityError):
        resolve_gemini_settings(provider, PluginConfig())
    same = resolve_gemini_settings(provider, replace(PluginConfig(), video_api_base="https://proxy.example/gemini"))
    assert same.api_key == "secret" and same.model == "gemini-alias"
    with pytest.raises(VideoCapabilityError, match="missing video credentials"):
        resolve_gemini_settings(provider, replace(PluginConfig(), video_api_base="https://other.example"))
    other = resolve_gemini_settings(provider, replace(PluginConfig(), video_api_base="https://other.example", video_api_key="other-key"))
    assert other.api_key == "other-key"


def test_resolve_astrbot_native_provider_inherits_endpoint_headers_and_model():
    provider = SimpleNamespace(
        provider_config={"type": "googlegenai_chat_completion", "api_base": "https://reverse.example/gemini", "proxy": "http://localhost:7890"},
        get_model=lambda: "models/gemini-alias", get_current_key=lambda: "selected-key",
        request_headers={"X-Channel": "selected"},
    )
    context = SimpleNamespace(get_provider_by_id=lambda key: provider)
    settings = resolve_gemini_settings(
        AstrbotProvider(context, "selected"),
        PluginConfig.from_mapping({"video_summary_model": "removed"}),
    )
    assert settings.api_base == "https://reverse.example/gemini"
    assert settings.model == "gemini-alias" and settings.headers == {"X-Channel": "selected"}
    assert "selected-key" not in repr(settings)


@asynccontextmanager
async def fake_gemini(*, interaction_status=200, processing_steps=True, cross_origin=False, static_status=200,
                      state="ACTIVE", upload_status=200, response_delay=0):
    records = []
    address = {}

    async def handle(request):
        records.append((request.method, request.path, dict(request.headers)))
        if request.path == "/upload/v1beta/files":
            if upload_status != 200:
                return web.Response(status=upload_status)
            host = address["base"].replace("127.0.0.1", "localhost") if cross_origin else address["base"]
            return web.Response(headers={"X-Goog-Upload-URL": host + "/finalize"})
        if request.path == "/finalize":
            records.append(("bytes", await request.read()))
            return web.json_response({"file": {"name": "files/test", "uri": "https://files.example/test", "state": state, "mimeType": "video/mp4"}})
        if request.method == "DELETE":
            return web.Response(status=204)
        if request.path == "/v1beta/files/test":
            return web.json_response({"state": "ACTIVE"})
        if request.path == "/v1beta/interactions":
            body = await request.json()
            records.append(("interaction", body))
            await asyncio.sleep(response_delay)
            if interaction_status != 200:
                return web.json_response({"error": "secret-that-must-not-be-logged"}, status=interaction_status)
            steps = [{"type": "processing_call"}] if processing_steps else []
            steps += [{"type": "thought", "content": [{"type": "text", "text": "hidden thought"}]},
                      {"type": "model_output", "content": [{"type": "text", "text": ANSWER}]}]
            return web.json_response({"status": "completed", "steps": steps, "usage": {"total_tokens": 10}})
        records.append(("static", await request.json()))
        await asyncio.sleep(response_delay)
        return web.json_response({"candidates": [{"content": {"parts": [
            {"text": "hidden thought", "thought": True}, {"text": ANSWER},
        ]}}], "usageMetadata": {"totalTokenCount": 20}}, status=static_status)

    application = web.Application()
    application.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address["base"] = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield address["base"], records
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("auto", "static"), ("static", "static"), ("agentic", "agentic")])
async def test_native_http_upload_mode_payload_and_delete(tmp_path, mode, expected):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"local-video")
    async with fake_gemini() as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "test-key", "gemini-alias"), 5)
        note = await provider.summarize(str(path), "summarize", replace(PluginConfig(), video_processing_mode=mode))
    assert note.text == ANSWER and note.processing_mode == expected
    assert ("bytes", b"local-video") in records
    if expected == "agentic":
        payload = next(item[1] for item in records if item[0] == "interaction")
        assert payload["input"][0]["processing"] == "agentic"
        assert payload["store"] is False
        assert not any(item[0] == "static" for item in records)
    else:
        assert any(item[0] == "static" for item in records)
    assert any(item[:2] == ("DELETE", "/v1beta/files/test") for item in records)


@pytest.mark.asyncio
async def test_agentic_unsupported_falls_back_to_static_without_uploading_twice(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(interaction_status=404) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        note = await provider.summarize(str(path), "test", replace(PluginConfig(), video_processing_mode="agentic"))
    assert note.processing_mode == "static" and "已改用 Static" in note.warning
    assert sum(item[:2] == ("POST", "/finalize") for item in records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,fallback", [(404, False), (401, True), (429, True)])
async def test_native_errors_cleanup_without_repeating_auth_or_rate_limit(tmp_path, status, fallback):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(interaction_status=status) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        with pytest.raises(BiliVideoError) as caught:
            await provider.summarize(str(path), "test", replace(PluginConfig(), video_processing_mode="agentic", video_fallback=fallback))
    assert "secret-that-must-not-be-logged" not in str(caught.value)
    assert not any(item[0] == "static" for item in records)
    assert any(item[:2] == ("DELETE", "/v1beta/files/test") for item in records)


@pytest.mark.asyncio
async def test_agentic_success_without_processing_evidence_is_marked_unverified(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(processing_steps=False) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        result = await provider.summarize(str(path), "test", replace(PluginConfig(), video_processing_mode="agentic"))
    assert result.processing_mode == "agentic_unverified"
    assert "未返回可确认" in result.warning


@pytest.mark.asyncio
async def test_cross_origin_upload_does_not_forward_api_key_or_custom_auth(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(cross_origin=True) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias", {"Authorization": "Bearer private"}), 5)
        await provider.summarize(str(path), "test", PluginConfig())
    headers = next(item[2] for item in records if item[:2] == ("POST", "/finalize"))
    assert not any(name.lower() in {"authorization", "x-goog-api-key"} for name in headers)


@pytest.mark.asyncio
async def test_failed_remote_preprocessing_still_deletes_uploaded_file(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(state="FAILED") as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        with pytest.raises(BiliVideoError, match="processing failed"):
            await provider.summarize(str(path), "test", PluginConfig())
    assert any(item[:2] == ("DELETE", "/v1beta/files/test") for item in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("processing", ["static", "agentic"])
async def test_proxy_without_files_api_accepts_small_inline_video(tmp_path, processing):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"small-video")
    async with fake_gemini(upload_status=404) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        note = await provider.summarize(str(path), "test", replace(PluginConfig(), video_processing_mode=processing))
    assert note.text == ANSWER
    if processing == "agentic":
        payload = next(item[1] for item in records if item[0] == "interaction")
        assert base64.b64decode(payload["input"][0]["data"]) == b"small-video"
    else:
        payload = next(item[1] for item in records if item[0] == "static")
        assert base64.b64decode(payload["contents"][0]["parts"][0]["inlineData"]["data"]) == b"small-video"
    assert not any(item[0] == "DELETE" for item in records)


@pytest.mark.asyncio
async def test_native_timeout_deletes_remote_file(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(response_delay=0.3) as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 0.1)
        with pytest.raises(BiliVideoError, match="network failure"):
            await provider.summarize(str(path), "test", PluginConfig())
    assert any(item[:2] == ("DELETE", "/v1beta/files/test") for item in records)


@pytest.mark.asyncio
async def test_processing_poll_keeps_file_name_for_cleanup(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async with fake_gemini(state="PROCESSING") as (base, records):
        provider = GeminiVideoProvider(GeminiVideoSettings(base, "key", "alias"), 5)
        await provider.summarize(str(path), "test", PluginConfig())
    assert any(item[:2] == ("GET", "/v1beta/files/test") for item in records)
    assert any(item[:2] == ("DELETE", "/v1beta/files/test") for item in records)


def test_download_limits_are_checked_before_and_during_transfer(tmp_path, monkeypatch):
    downloader = ytdlp_downloader.YtDlpDownloader(tmp_path / "audio")

    def extract(url, opts):
        opts["match_filter"]({"duration": 100}, incomplete=False)

    monkeypatch.setattr(ytdlp_downloader, "_extract_info_with_retry", extract)
    with pytest.raises(DownloadError, match="duration limit"):
        downloader.download_video(URL, output_dir=tmp_path / "video", max_duration=10, max_size_bytes=50, cancel_event=threading.Event())

    def transfer(url, opts):
        opts["progress_hooks"][0]({"filename": "video", "downloaded_bytes": 40})
        opts["progress_hooks"][0]({"filename": "audio", "downloaded_bytes": 20})

    monkeypatch.setattr(ytdlp_downloader, "_extract_info_with_retry", transfer)
    with pytest.raises(DownloadError, match="size limit"):
        downloader.download_video(URL, output_dir=tmp_path / "video", max_duration=10, max_size_bytes=50, cancel_event=threading.Event())


@pytest.mark.asyncio
async def test_cancelled_download_worker_finishes_before_directory_cleanup(tmp_path):
    started = threading.Event()
    finished = threading.Event()

    def download(url, *, output_dir, cancel_event, **kwargs):
        started.set()
        cancel_event.wait(2)
        (Path(output_dir) / "late-file").write_bytes(b"late")
        finished.set()
        raise DownloadError("cancelled")

    service = VideoMediaService(SimpleNamespace(download_video=download), str(tmp_path))

    async def job():
        async with service.prepare(URL, PluginConfig()):
            pass

    task = asyncio.create_task(job())
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert list(service.root.iterdir()) == []


@pytest.mark.asyncio
async def test_download_io_failure_is_reported_and_temp_directory_is_removed(tmp_path):
    def download(url, **kwargs):
        raise OSError("disk is unavailable")

    service = VideoMediaService(SimpleNamespace(download_video=download), str(tmp_path))
    with pytest.raises(BiliVideoError, match="file I/O"):
        async with service.prepare(URL, PluginConfig()):
            pass
    assert list(service.root.iterdir()) == []


@pytest.mark.asyncio
async def test_ffmpeg_launch_failure_can_trigger_outer_fallback(tmp_path, monkeypatch):
    service = VideoMediaService(object(), str(tmp_path))
    monkeypatch.setattr("bilivideo.summarize.video_media._ffmpeg_location", lambda: "missing-ffmpeg")
    monkeypatch.setattr("bilivideo.summarize.video_media.asyncio.create_subprocess_exec", AsyncMock(side_effect=OSError("missing")))
    with pytest.raises(BiliVideoError, match="ffmpeg launch"):
        await service.frames(VideoMedia("video.mp4", 1, "test"), PluginConfig())


@pytest.mark.asyncio
async def test_real_ffmpeg_frame_sampling_and_local_file_cleanup(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("system FFmpeg is not installed")
    source = tmp_path / "fixture.mp4"
    subprocess.run([
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=2", "-t", "4", "-pix_fmt", "yuv420p", str(source),
    ], check=True, timeout=30)

    def download(url, *, output_dir, **kwargs):
        destination = Path(output_dir) / "source.mp4"
        shutil.copyfile(source, destination)
        return {"file_path": str(destination), "duration": 4, "title": "fixture"}

    service = VideoMediaService(SimpleNamespace(download_video=download), str(tmp_path))
    config = replace(PluginConfig(), video_frame_count=3, video_frame_width=320)
    async with service.prepare(URL, config) as media:
        frames = await service.frames(media, config)
        assert [frame.timestamp for frame in frames] == pytest.approx([2 / 3, 2, 10 / 3])
        for frame in frames:
            image = Image.open(io.BytesIO(base64.b64decode(frame.image_url.split(",", 1)[1])))
            assert image.size == (320, 180)
        assert Path(media.file_path).is_file()
    assert list(service.root.iterdir()) == []


@pytest.mark.asyncio
async def test_video_settings_mask_secrets_on_read_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr("bilivideo.webui.json_response", lambda payload: payload)
    config = {"video": {"video_summary_mode": "frames", "video_api_key": "private"}}
    ui = PluginWebUI(SimpleNamespace(config=config), object(), SimpleNamespace(), str(tmp_path))
    state = await ui.get_state()
    assert state["config"]["video_api_key"] == ""
    monkeypatch.setattr("bilivideo.webui.request", SimpleNamespace(json=AsyncMock(return_value={
        "settings": {"video_summary_mode": "native", "video_api_key": ""},
    })))
    response = await ui.save_settings()
    assert config["video"]["video_api_key"] == "private"
    assert response["config"]["video_api_key"] == ""
    assert response["config"]["video_summary_mode"] == "native"

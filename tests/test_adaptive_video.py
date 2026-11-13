"""Budgeted model-directed inspection: validation, stops, integration and real FFmpeg."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import subprocess
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import BiliVideoError, LLMError
from bilivideo.downloader.ytdlp_downloader import YtDlpDownloader
from bilivideo.summarize.adaptive_video import parse_frame_plan, summarize_adaptive
from bilivideo.summarize.video_media import VideoFrame, VideoMedia, VideoMediaService, preview_times
from tests.test_video_understanding import ANSWER, URL, FakeMedia, make_orchestrator


def plan(requests=(), done=False):
    return json.dumps({"done": done, "requests": list(requests)})


@pytest.mark.parametrize("item", [
    {"timestamp": -1}, {"timestamp": 60}, {"timestamp": True}, {"timestamp": "5"},
    {"timestamp": float("nan")}, {"timestamp": float("inf")},
    {"timestamp": 2, "crop": [0, 0, 2, 1]}, {"timestamp": 2, "crop": [0.5, 0, 0.6, 1]},
    {"timestamp": 2, "crop": [-0.1, 0, 0.5, 1]}, {"timestamp": 2, "crop": [0, 0, 0.001, 1]},
    {"timestamp": 2, "crop": [True, 0, 0.5, 1]}, {"timestamp": 2, "crop": [0, 0, 0.5]},
    {"timestamp": 2, "crop": "crop=iw/2"}, {"timestamp": 2, "crop": [0, 0, float("nan"), 1]},
    {"timestamp": "$(unexpected)"}, None,
])
def test_untrusted_model_timestamp_and_crop_cannot_reach_ffmpeg(item):
    with pytest.raises(ValueError):
        parse_frame_plan(plan([item]), 60, 48, set())


@pytest.mark.parametrize("body", ['[]', '{}', '{"done":"false","requests":[]}', 'not json', 'x' * 32001])
def test_malformed_plans_are_rejected(body):
    with pytest.raises(ValueError):
        parse_frame_plan(body, 60, 48, set())


def test_plan_budget_deduplication_and_crop_identity():
    seen = set()
    requests = [{"timestamp": 5}, {"timestamp": 5.01}, {"timestamp": 5, "crop": [0, 0, 0.5, 1]},
                *({"timestamp": stamp} for stamp in range(10, 59))]
    first = parse_frame_plan(plan(requests), 60, 3, seen)
    assert len(first) == 3 and [item.timestamp for item in first] == [5, 5, 10]
    assert first[1].crop == (0, 0, 0.5, 1)
    assert len(parse_frame_plan(plan(requests), 60, 48, set())) == 12
    with pytest.raises(ValueError):
        parse_frame_plan(plan([{"timestamp": 5}]), 60, 48, seen)
    assert parse_frame_plan(plan([{"timestamp": 2}], done=True), 60, 48, set()) == ()
    assert parse_frame_plan('```json\n' + plan([{"timestamp": 1}]) + '\n```', 60, 48, set())[0].timestamp == 1


class MediaStub:
    def __init__(self, preview_count=2):
        self.preview_count = preview_count
        self.calls = []

    async def previews(self, media, config):
        return tuple(VideoFrame(index + 0.5, f"preview-{index}")
                     for index in range(min(config.video_frame_count, self.preview_count)))

    async def extract(self, media, timestamps, *, width, crops):
        self.calls.append((timestamps, width, crops))
        return tuple(VideoFrame(stamp, f"detail-{stamp}-{crop}", crop) for stamp, crop in zip(timestamps, crops))


async def run_review(responses, *, budget=48, rounds=3, media_service=None, **kwargs):
    llm = SimpleNamespace(chat=AsyncMock(side_effect=responses))
    service = media_service or MediaStub()
    config = replace(PluginConfig(), video_frame_count=budget, video_max_rounds=rounds)
    result = await summarize_adaptive(
        llm=llm, media_service=service, media=VideoMedia("source.mp4", 60, "测试"),
        config=config, title="测试", **kwargs,
    )
    return result, llm, service


@pytest.mark.asyncio
async def test_model_can_finish_after_previews_without_spending_entire_budget():
    result, llm, service = await run_review([plan(done=True), ANSWER])
    assert result.text == ANSWER and not result.warning
    assert result.usage["video_frames"] == 2 and result.usage["video_review_rounds"] == 0
    assert result.usage["video_stop_reason"] == "model_done"
    assert not service.calls and llm.chat.await_count == 2


@pytest.mark.asyncio
async def test_model_requests_high_resolution_and_crop_then_synthesizes_all_evidence():
    result, llm, service = await run_review([
        plan([{"timestamp": 5}, {"timestamp": 12.25, "crop": [0.1, 0.2, 0.7, 0.5]}]),
        plan(done=True), ANSWER,
    ])
    assert result.usage["video_frames"] == 4 and result.usage["video_review_rounds"] == 1
    assert service.calls == [((5, 12.25), 1280, (None, (0.1, 0.2, 0.7, 0.5)))]
    final = llm.chat.call_args
    assert len(final.kwargs["image_urls"]) == 4
    assert "12.250 秒" in final.args[0] and "局部区域" in final.args[0]
    assert "测试条件" in final.args[0] and "禁止输出补看 JSON" in final.args[0]


@pytest.mark.asyncio
async def test_budget_caps_oversized_model_request_and_keeps_final_summary_call():
    result, llm, service = await run_review([plan([{"timestamp": i} for i in range(30)]), ANSWER], budget=5)
    assert len(service.calls[0][0]) == 3
    assert result.usage["video_frames"] == 5 and result.usage["video_stop_reason"] == "frame_limit"
    assert llm.chat.await_count == 2


@pytest.mark.asyncio
async def test_previews_alone_can_exhaust_budget_without_planner():
    result, llm, service = await run_review([ANSWER], budget=1)
    assert llm.chat.await_count == 1 and not service.calls
    assert result.usage["video_frames"] == 1


@pytest.mark.asyncio
async def test_round_limit_stops_even_if_model_keeps_asking():
    result, llm, service = await run_review([plan([{"timestamp": 5}]), plan([{"timestamp": 10}]), ANSWER], rounds=2)
    assert len(service.calls) == 2 and llm.chat.await_count == 3
    assert result.usage["video_stop_reason"] == "round_limit"


@pytest.mark.asyncio
async def test_repeated_requests_stop_without_reloading_the_same_frame():
    result, llm, service = await run_review([plan([{"timestamp": 5}]), plan([{"timestamp": 5}]), ANSWER])
    assert len(service.calls) == 1 and llm.chat.await_count == 3
    assert result.usage["video_stop_reason"] == "invalid_plan" and result.warning


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [LLMError("unsupported"), asyncio.TimeoutError(), "# markdown instead of JSON"])
async def test_unavailable_planning_is_visible_and_still_allows_final_summary(failure):
    result, llm, service = await run_review([failure, ANSWER])
    assert result.text == ANSWER and result.warning and not service.calls
    assert llm.chat.await_count == 2


@pytest.mark.asyncio
async def test_final_synthesis_error_propagates_for_existing_fallback():
    with pytest.raises(LLMError):
        await run_review([plan(done=True), LLMError("final failed")])


@pytest.mark.asyncio
async def test_final_control_json_is_not_sent_to_the_user_as_a_summary():
    with pytest.raises(BiliVideoError, match='frame plan returned as summary'):
        await run_review([plan(done=True), plan(done=True)])


@pytest.mark.asyncio
async def test_cancelled_review_is_not_converted_into_a_fallback_summary():
    with pytest.raises(asyncio.CancelledError):
        await run_review([asyncio.CancelledError()])


@pytest.mark.asyncio
async def test_spent_planning_time_still_leaves_final_synthesis():
    llm = SimpleNamespace(chat=AsyncMock(return_value=ANSWER))
    result = await summarize_adaptive(
        llm=llm, media_service=MediaStub(), media=VideoMedia('source.mp4', 60, 'test'),
        config=replace(PluginConfig(), processing_timeout=0), title='test',
    )
    assert result.usage['video_stop_reason'] == 'planning_timeout'
    assert result.warning and llm.chat.await_count == 1


@pytest.mark.asyncio
async def test_detail_extraction_failure_reaches_existing_fallback():
    media = MediaStub()
    media.extract = AsyncMock(side_effect=BiliVideoError('frame failed'))
    with pytest.raises(BiliVideoError, match='frame failed'):
        await run_review([plan([{'timestamp': 5}])], media_service=media)


@pytest.mark.asyncio
async def test_scene_scan_failure_keeps_uniform_previews(tmp_path, monkeypatch):
    service = VideoMediaService(object(), str(tmp_path))
    monkeypatch.setattr('bilivideo.summarize.video_media._ffmpeg_location', lambda: 'missing-ffmpeg')
    monkeypatch.setattr('bilivideo.summarize.video_media.asyncio.create_subprocess_exec',
                        AsyncMock(side_effect=OSError('missing')))
    service.extract = AsyncMock(return_value=(VideoFrame(1, 'image'),))
    await service.previews(VideoMedia('source.mp4', 60, 'test'), PluginConfig())
    assert service.extract.call_args.args[1] == preview_times(60, 12)
    assert service.extract.call_args.kwargs['width'] == 640


@pytest.mark.asyncio
async def test_orchestrator_uses_adaptive_mode_and_caches_counts(monkeypatch, tmp_path):
    orch, llm, _, _ = make_orchestrator(monkeypatch, tmp_path, video_frame_strategy="adaptive")
    orch._video_media = MediaStub()
    orch._video_media.prepare = FakeMedia().prepare
    llm.chat.side_effect = [plan([{"timestamp": 15}]), plan(done=True), ANSWER]
    result = await orch.generate(URL)
    assert result.processing_mode == "adaptive" and "共 3 张" in result.markdown
    assert not result.fallback_reason
    saved = await orch._get_persistent_cache(orch.cache_key(URL))
    assert saved.usage["video_review_rounds"] == 1
    assert await orch.generate(URL) is result and llm.chat.await_count == 3


def test_cache_changes_when_any_inspection_setting_changes(monkeypatch, tmp_path):
    orch, _, _, _ = make_orchestrator(monkeypatch, tmp_path)
    first = orch.cache_key(URL)
    for key, value in {"video_frame_strategy": "adaptive", "video_preview_count": 4,
                       "video_max_rounds": 5, "video_download_height": 720}.items():
        before = orch._config
        orch._config = replace(before, **{key: value})
        assert orch.cache_key(URL) != first
        orch._config = before


def test_scene_candidates_do_not_replace_all_timeline_coverage():
    assert preview_times(60, 4, []) == (7.5, 22.5, 37.5, 52.5)
    times = preview_times(60, 4, [1, 10, 20, 30])
    assert times == (10.2, 15.0, 30.2, 45.0)
    assert all(0 <= time < 0.01 for time in preview_times(0.01, 12))


def test_all_video_defaults_match_schema_and_saved_overrides_remain():
    from pathlib import Path
    schema = json.loads(Path('_conf_schema.json').read_text(encoding='utf-8'))
    config = PluginConfig()
    parsed = PluginConfig.from_mapping({})
    for key, field in schema['video']['items'].items():
        assert getattr(config, key) == getattr(parsed, key) == field['default']
    custom = PluginConfig.from_mapping({'video': {'video_frame_count': 8, 'video_frame_width': 768,
        'video_max_duration': 1800, 'video_max_size_mb': 80, 'video_preview_count': 99,
        'video_max_rounds': 999, 'video_download_height': 9999}})
    assert (custom.video_frame_count, custom.video_frame_width, custom.video_max_duration, custom.video_max_size_mb) == (8, 768, 1800, 80)
    assert (custom.video_preview_count, custom.video_max_rounds, custom.video_download_height) == (24, 6, 1080)


def test_download_height_sets_the_actual_source_quality(tmp_path, monkeypatch):
    def extract(url, options):
        assert options['format'] == 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
        raise BiliVideoError('checked')

    monkeypatch.setattr('bilivideo.downloader.ytdlp_downloader._extract_info_with_retry', extract)
    with pytest.raises(BiliVideoError, match='checked'):
        YtDlpDownloader(tmp_path).download_video(
            URL, output_dir=tmp_path, max_duration=3600, max_size_bytes=256*1024*1024,
            cancel_event=threading.Event(), max_height=1080,
        )


@pytest.mark.asyncio
async def test_real_scene_scan_hd_frames_and_crop_dimensions(tmp_path):
    binary = shutil.which('ffmpeg')
    if not binary:
        pytest.skip('FFmpeg not installed')
    path = tmp_path / 'scene-fixture.mp4'
    subprocess.run([
        binary, '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=c=red:size=1920x1080:rate=2:duration=2',
        '-f', 'lavfi', '-i', 'color=c=blue:size=1920x1080:rate=2:duration=2',
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[out]', '-map', '[out]',
        '-pix_fmt', 'yuv420p', str(path),
    ], check=True, timeout=30)
    service = VideoMediaService(object(), str(tmp_path))
    media = VideoMedia(str(path), 4, 'fixture')
    scenes = await service.scene_times(media)
    assert any(1 <= value <= 3 for value in scenes)
    frames = await service.extract(media, (0.5, 3), width=1280, crops=(None, (0.25, 0.25, 0.5, 0.5)))
    decoded = [Image.open(io.BytesIO(base64.b64decode(frame.image_url.split(',', 1)[1]))) for frame in frames]
    assert decoded[0].size == (1280, 720)
    assert decoded[1].size == (960, 540)  # Cropping precedes scaling; source detail is never invented.
    assert decoded[0].getpixel((1, 1))[0] > 200
    assert decoded[1].getpixel((1, 1))[2] > 200

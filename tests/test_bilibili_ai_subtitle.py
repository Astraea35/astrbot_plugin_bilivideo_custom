"""Tests for the logged-in Bilibili AI subtitle fallback."""

from __future__ import annotations

import pytest

from bilivideo.api.endpoints import get_bilibili_ai_subtitle
from bilivideo.core.constants import ENDPOINT_PLAYER_V2, ENDPOINT_VIEW
from bilivideo.core.types import TranscriptResult, TranscriptSegment
from bilivideo.transcription.pipeline import TranscriptPipeline


class _StubHTTP:
    def __init__(self, *, logged_in: bool = True) -> None:
        self.cookies = {"SESSDATA": "token"} if logged_in else {}
        self.calls: list[tuple[str, dict | None]] = []

    async def request_json(self, method: str, url: str, *, params=None):
        self.calls.append((url, dict(params) if params else None))
        if url == ENDPOINT_VIEW:
            return {"code": 0, "data": {"cid": 42}}
        if url == ENDPOINT_PLAYER_V2:
            return {
                "code": 0,
                "data": {
                    "subtitle": {
                        "subtitles": [
                            {"lan": "zh-Hans", "subtitle_url": "https://i0.hdslb.com/public.json"},
                            {"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/ai.json"},
                        ]
                    }
                },
            }
        if url == "https://aisubtitle.hdslb.com/ai.json":
            return {
                "body": [
                    {"from": 1.25, "to": 2.5, "content": "第一句 AI 字幕"},
                    {"from": 2.5, "to": 4.0, "content": "第二句 AI 字幕"},
                ]
            }
        raise AssertionError(f"unexpected request: {url}")


@pytest.mark.asyncio
async def test_ai_subtitle_requires_logged_in_bilibili_account() -> None:
    client = _StubHTTP(logged_in=False)

    result = await get_bilibili_ai_subtitle(client, "https://www.bilibili.com/video/BV1xx411c7mD")

    assert result is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_ai_subtitle_is_parsed_after_selecting_trusted_ai_track() -> None:
    client = _StubHTTP()

    result = await get_bilibili_ai_subtitle(
        client,
        "https://www.bilibili.com/video/BV1xx411c7mD",
        langs=("zh-Hans", "ai-zh"),
    )

    assert result is not None
    assert result.language == "ai-zh"
    assert result.full_text == "第一句 AI 字幕 第二句 AI 字幕"
    assert result.raw == {"source": "bilibili_ai_subtitle", "bvid": "BV1xx411c7mD"}
    assert [segment.start for segment in result.segments] == [1.25, 2.5]
    assert [url for url, _ in client.calls] == [
        ENDPOINT_VIEW,
        ENDPOINT_PLAYER_V2,
        "https://aisubtitle.hdslb.com/ai.json",
    ]


class _NoSubtitleDownloader:
    def download_subtitles(self, _url, *, langs=None):
        return None

    def download_audio(self, *_args, **_kwargs):
        raise AssertionError("audio must not download when an AI subtitle is available")


class _UnusedTranscriber:
    def transcribe(self, *_args, **_kwargs):
        raise AssertionError("ASR must not run when an AI subtitle is available")


@pytest.mark.asyncio
async def test_ai_subtitle_discarded_if_timestamps_exceed_video_duration() -> None:
    class _MismatchHTTP:
        def __init__(self) -> None:
            self.cookies = {"SESSDATA": "token"}

        async def request_json(self, method: str, url: str, *, params=None):
            if url == ENDPOINT_VIEW:
                return {"code": 0, "data": {"cid": 42, "duration": 100}}  # 100s video
            if url == ENDPOINT_PLAYER_V2:
                return {
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {"lan": "ai-zh", "subtitle_url": "https://aisubtitle.hdslb.com/polluted.json"},
                            ]
                        }
                    },
                }
            if url == "https://aisubtitle.hdslb.com/polluted.json":
                return {
                    "body": [
                        {"from": 0, "to": 10, "content": "开头"},
                        {"from": 500, "to": 600, "content": "污染时间戳(600s)"},
                    ]
                }
            raise AssertionError(f"unexpected request: {url}")

    client = _MismatchHTTP()
    result = await get_bilibili_ai_subtitle(client, "https://www.bilibili.com/video/BV1xx411c7mD")
    assert result is None


@pytest.mark.asyncio
async def test_ai_subtitle_discarded_if_coverage_is_too_short() -> None:
    """Simulates the 120s video receiving a 50s fake subtitle (the chicken soup bug)."""
    class _ShortCoverageHTTP:
        def __init__(self) -> None:
            self.cookies = {"SESSDATA": "token"}

        async def request_json(self, method: str, url: str, *, params=None):
            if url == ENDPOINT_VIEW:
                return {"code": 0, "data": {"cid": 42, "duration": 120}}  # 120s video
            if url == ENDPOINT_PLAYER_V2:
                return {
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {"lan": "ai-zh", "subtitle_url": "https://aisubtitle.hdslb.com/short.json"},
                            ]
                        }
                    },
                }
            if url == "https://aisubtitle.hdslb.com/short.json":
                return {
                    "body": [
                        {"from": i * 1.2, "to": (i + 1) * 1.2, "content": f"鸡汤内容片段 {i}"}
                        for i in range(40)  # ends at 48.0s
                    ]
                }
            raise AssertionError(f"unexpected request: {url}")

    client = _ShortCoverageHTTP()
    result = await get_bilibili_ai_subtitle(client, "https://www.bilibili.com/video/BV1xx411c7mD")
    assert result is None


@pytest.mark.asyncio
async def test_ai_subtitle_discarded_if_starts_too_late() -> None:
    class _LateStartHTTP:
        def __init__(self) -> None:
            self.cookies = {"SESSDATA": "token"}

        async def request_json(self, method: str, url: str, *, params=None):
            if url == ENDPOINT_VIEW:
                return {"code": 0, "data": {"cid": 42, "duration": 100}}
            if url == ENDPOINT_PLAYER_V2:
                return {
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {"lan": "ai-zh", "subtitle_url": "https://aisubtitle.hdslb.com/latestart.json"},
                            ]
                        }
                    },
                }
            if url == "https://aisubtitle.hdslb.com/latestart.json":
                return {
                    "body": [
                        {"from": 50, "to": 90, "content": "很晚才开始的字幕内容"},
                    ]
                }
            raise AssertionError(f"unexpected request: {url}")

    client = _LateStartHTTP()
    result = await get_bilibili_ai_subtitle(client, "https://www.bilibili.com/video/BV1xx411c7mD")
    assert result is None


@pytest.mark.asyncio
async def test_pipeline_uses_ai_subtitle_before_audio_asr(monkeypatch) -> None:
    expected = TranscriptResult(
        language="ai-zh",
        full_text="AI 字幕",
        segments=(TranscriptSegment(start=0, end=1, text="AI 字幕"),),
    )

    async def _ai_subtitle(*_args, **_kwargs):
        return expected

    monkeypatch.setattr("bilivideo.transcription.pipeline.get_bilibili_ai_subtitle", _ai_subtitle)
    pipeline = TranscriptPipeline(_NoSubtitleDownloader(), _UnusedTranscriber(), object())

    output = await pipeline.fetch(
        "https://www.bilibili.com/video/BV1xx411c7mD",
        enable_bilibili_ai_subtitle=True,
    )

    assert output.transcript is expected
    assert output.audio is None
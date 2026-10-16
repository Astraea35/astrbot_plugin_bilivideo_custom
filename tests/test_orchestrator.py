"""End-to-end orchestrator test using stub HTTP / pipeline / LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bilivideo.core.config import PluginConfig
from bilivideo.core.exceptions import BiliVideoError, LLMError
from bilivideo.core.types import (
    AudioDownloadResult,
    TranscriptResult,
    TranscriptSegment,
    VideoInfo,
)
from bilivideo.summarize.orchestrator import SummaryOrchestrator
from bilivideo.summarize.relevance import (
    summary_conflicts_with_transcript,
    title_conflicts_with_text,
)
from bilivideo.transcription.pipeline import PipelineOutput

# ──────────────────────── stubs ────────────────────────


class _StubLLM:
    def __init__(self, response: str = "# 视频标题 - UP\n\n## 章节A\n要点 *Content-[02:30]*\n") -> None:
        self.response = response
        self.calls: list[str] = []

    async def chat(self, prompt: str, *, session_id: str | None = None) -> str:
        self.calls.append(prompt)
        return self.response


class _ErrorLLM:
    async def chat(self, prompt: str, *, session_id: str | None = None) -> str:
        raise LLMError("boom")


class _FailOnceLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, prompt: str, *, session_id: str | None = None) -> str:
        self.calls += 1
        if self.calls == 1:
            raise LLMError("temporary failure")
        return "# recovered"


class _StubPipeline:
    def __init__(self, output: PipelineOutput, *, raise_exc: Exception | None = None) -> None:
        self.output = output
        self.raise_exc = raise_exc
        self.fetch_calls = 0
        self.cleanup_calls: list[Any] = []

    async def fetch(self, video_url: str, **kwargs: Any) -> PipelineOutput:
        self.fetch_calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.output

    def cleanup_audio(self, audio: Any) -> None:
        self.cleanup_calls.append(audio)


_GAME_TITLE = "7.1周年庆限定五星自选+常驻五星自选推荐"
_FOOD_TEXT = "贵州美食锅巴糍粑蛋包洋芋折耳根和豆豉火锅令人回味。" * 12
_GAME_TEXT = "周年庆限定五星自选推荐，常驻五星自选角色分析和抽取建议。" * 12
_FOOD_NOTE = "# 7.1周年庆限定五星自选+常驻五星自选推荐 - 美食博主\n\n" + _FOOD_TEXT
_GAME_NOTE = "# 7.1周年庆限定五星自选+常驻五星自选推荐 - 一诚xy\n\n" + _GAME_TEXT


def _transcript(text: str) -> TranscriptResult:
    return TranscriptResult(
        language="ai-zh",
        full_text=text,
        segments=(TranscriptSegment(start=0, end=30, text=text),),
    )


def test_relevance_guard_catches_unrelated_food_summary() -> None:
    assert title_conflicts_with_text(_GAME_TITLE, _FOOD_TEXT)
    assert not title_conflicts_with_text(_GAME_TITLE, _GAME_TEXT)
    assert summary_conflicts_with_transcript(_GAME_TEXT, _FOOD_NOTE)
    assert not summary_conflicts_with_transcript(_GAME_TEXT, _GAME_NOTE)
    assert not title_conflicts_with_text("今天真开心", _FOOD_TEXT)


@pytest.mark.asyncio
async def test_unrelated_subtitle_falls_back_to_audio_asr(monkeypatch) -> None:
    info = VideoInfo(bvid="BV1xx411c7mD", title=_GAME_TITLE, owner_name="一诚xy")
    _patch_get_video_info(monkeypatch, info)
    audio = AudioDownloadResult(
        file_path="/tmp/game.mp3", title=_GAME_TITLE, duration=30,
        cover_url=None, platform="bilibili", video_id=info.bvid,
    )

    class _RetryPipeline(_StubPipeline):
        async def fetch(self, video_url: str, **kwargs: Any) -> PipelineOutput:
            self.fetch_calls += 1
            if kwargs.get("force_asr"):
                return PipelineOutput(_transcript(_GAME_TEXT), audio)
            return PipelineOutput(_transcript(_FOOD_TEXT), None)

    pipeline = _RetryPipeline(PipelineOutput(_transcript(_FOOD_TEXT), None))
    llm = _StubLLM(_GAME_NOTE)
    orch = SummaryOrchestrator(
        config=PluginConfig.from_mapping({}), llm=llm,
        pipeline=pipeline, http_client=_StubHTTP(),  # type: ignore[arg-type]
    )
    result = await orch.generate(f"https://www.bilibili.com/video/{info.bvid}")

    assert result.used_subtitle is False
    assert pipeline.fetch_calls == 2
    assert len(llm.calls) == 1
    assert _GAME_TEXT in llm.calls[0]
    assert pipeline.cleanup_calls == [audio]


@pytest.mark.asyncio
async def test_unrelated_llm_answer_is_not_cached(monkeypatch) -> None:
    info = VideoInfo(bvid="BV1xx411c7mD", title=_GAME_TITLE, owner_name="一诚xy")
    _patch_get_video_info(monkeypatch, info)
    audio = AudioDownloadResult(
        file_path="/tmp/game.mp3", title=_GAME_TITLE, duration=30,
        cover_url=None, platform="bilibili", video_id=info.bvid,
    )

    class _RetryPipeline(_StubPipeline):
        async def fetch(self, video_url: str, **kwargs: Any) -> PipelineOutput:
            self.fetch_calls += 1
            if kwargs.get("force_asr"):
                return PipelineOutput(_transcript(_GAME_TEXT), audio)
            return self.output

    pipeline = _RetryPipeline(PipelineOutput(_transcript(_GAME_TEXT), None))
    llm = _StubLLM(_FOOD_NOTE)
    orch = SummaryOrchestrator(
        config=PluginConfig.from_mapping({}), llm=llm,
        pipeline=pipeline, http_client=_StubHTTP(),  # type: ignore[arg-type]
    )

    with pytest.raises(BiliVideoError, match="summary does not match"):
        await orch.generate(f"https://www.bilibili.com/video/{info.bvid}")
    assert len(llm.calls) == 4
    assert pipeline.fetch_calls == 2
    assert await orch._cache.get(info.bvid) is None


@pytest.mark.asyncio
async def test_summary_retries_until_content_matches(monkeypatch) -> None:
    info = VideoInfo(bvid="BV1xx411c7mD", title=_GAME_TITLE, owner_name="一诚xy")
    _patch_get_video_info(monkeypatch, info)
    audio = AudioDownloadResult(
        file_path="/tmp/game.mp3", title=_GAME_TITLE, duration=30,
        cover_url=None, platform="bilibili", video_id=info.bvid,
    )
    pipeline = _StubPipeline(PipelineOutput(_transcript(_GAME_TEXT), audio))

    class _EventuallyCorrectLLM(_StubLLM):
        async def chat(self, prompt: str, *, session_id: str | None = None) -> str:
            self.calls.append(prompt)
            if len(self.calls) < 4:
                return _FOOD_NOTE
            return _GAME_NOTE

    llm = _EventuallyCorrectLLM()
    orch = SummaryOrchestrator(
        config=PluginConfig.from_mapping({}), llm=llm,
        pipeline=pipeline, http_client=_StubHTTP(),  # type: ignore[arg-type]
    )

    result = await orch.generate(f"https://www.bilibili.com/video/{info.bvid}")

    assert result.markdown == _GAME_NOTE
    assert len(llm.calls) == 4


@pytest.mark.asyncio
async def test_off_topic_persistent_cache_is_discarded(monkeypatch, tmp_path) -> None:
    info = VideoInfo(bvid="BV1xx411c7mD", title=_GAME_TITLE, owner_name="一诚xy")
    _patch_get_video_info(monkeypatch, info)
    pipeline = _StubPipeline(PipelineOutput(_transcript(_GAME_TEXT), None))
    llm = _StubLLM(_GAME_NOTE)
    orch = SummaryOrchestrator(
        config=PluginConfig.from_mapping({}), llm=llm,
        pipeline=pipeline, http_client=_StubHTTP(),  # type: ignore[arg-type]
        data_dir=str(tmp_path),
    )
    from bilivideo.summarize.orchestrator import NoteResult

    await orch._set_persistent_cache(info.bvid, NoteResult(_FOOD_NOTE, info, True))
    result = await orch.generate(f"https://www.bilibili.com/video/{info.bvid}")
    assert result.markdown == _GAME_NOTE
    assert pipeline.fetch_calls == 1
    assert len(llm.calls) == 1


@dataclass
class _StubHTTP:
    """No-op HTTP client stub. The orchestrator only calls `get_video_info`
    on it via the api.endpoints helper, which we monkeypatch separately.
    """

    cookies: dict[str, str] = None  # type: ignore[assignment]


# ──────────────────────── fixtures ────────────────────────


def _make_pipeline_output(*, with_audio: bool = False) -> PipelineOutput:
    transcript = TranscriptResult(
        language="zh",
        full_text="hello world",
        segments=(
            TranscriptSegment(start=0, end=5, text="hello"),
            TranscriptSegment(start=5, end=10, text="world"),
        ),
    )
    audio = (
        AudioDownloadResult(
            file_path="/tmp/x.mp3",
            title="测试视频",
            duration=30.0,
            cover_url=None,
            platform="bilibili",
            video_id="BV1xx411c7mD",
            raw_info={"tags": ["python", "教程"]},
        )
        if with_audio
        else None
    )
    return PipelineOutput(transcript=transcript, audio=audio)


def _patch_get_video_info(monkeypatch, info: VideoInfo | None) -> None:
    async def _stub(http, bvid):
        if info is None:
            from bilivideo.core.exceptions import BiliVideoError

            raise BiliVideoError("not found")
        return info

    monkeypatch.setattr(
        "bilivideo.summarize.orchestrator.get_video_info", _stub
    )


# ──────────────────────── tests ────────────────────────


@pytest.mark.asyncio
async def test_happy_path_with_subtitle(monkeypatch) -> None:
    config = PluginConfig.from_mapping({"enable_link": True, "max_note_length": 3000})
    info = VideoInfo(bvid="BV1xx411c7mD", title="测试视频", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)

    pipeline = _StubPipeline(_make_pipeline_output(with_audio=False))
    llm = _StubLLM()

    orch = SummaryOrchestrator(config=config, llm=llm, pipeline=pipeline, http_client=_StubHTTP())  # type: ignore[arg-type]
    result = await orch.generate("https://www.bilibili.com/video/BV1xx411c7mD")

    assert result.video_info is info
    assert result.used_subtitle is True
    assert "⏱ 02:30" in result.markdown  # timestamp marker replaced
    assert pipeline.cleanup_calls == [None]  # no audio to clean
    assert len(llm.calls) == 1
    assert "BV1xx411c7mD" not in llm.calls[0]  # we don't leak BVID into prompt directly


@pytest.mark.asyncio
async def test_happy_path_with_audio(monkeypatch) -> None:
    config = PluginConfig.from_mapping({})
    info = VideoInfo(bvid="BV1abc", title="t", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)

    output = _make_pipeline_output(with_audio=True)
    pipeline = _StubPipeline(output)
    llm = _StubLLM()
    orch = SummaryOrchestrator(config=config, llm=llm, pipeline=pipeline, http_client=_StubHTTP())  # type: ignore[arg-type]
    result = await orch.generate("https://www.bilibili.com/video/BV1abc")

    assert result.used_subtitle is False
    assert pipeline.cleanup_calls == [output.audio]


@pytest.mark.asyncio
async def test_llm_failure_propagates(monkeypatch) -> None:
    config = PluginConfig.from_mapping({})
    _patch_get_video_info(monkeypatch, None)

    pipeline = _StubPipeline(_make_pipeline_output())
    orch = SummaryOrchestrator(
        config=config, llm=_ErrorLLM(), pipeline=pipeline, http_client=_StubHTTP()  # type: ignore[arg-type]
    )
    with pytest.raises(LLMError):
        await orch.generate("https://www.bilibili.com/video/BV1abc")
    # cleanup must still happen
    assert pipeline.cleanup_calls == [None]


@pytest.mark.asyncio
async def test_llm_retry_reuses_cached_transcript(monkeypatch) -> None:
    config = PluginConfig.from_mapping({})
    _patch_get_video_info(monkeypatch, None)
    pipeline = _StubPipeline(_make_pipeline_output())
    llm = _FailOnceLLM()
    orch = SummaryOrchestrator(
        config=config, llm=llm, pipeline=pipeline, http_client=_StubHTTP()  # type: ignore[arg-type]
    )

    url = "https://www.bilibili.com/video/BV1abc"
    with pytest.raises(LLMError):
        await orch.generate(url)
    await orch.generate(url)

    assert llm.calls == 2
    assert pipeline.fetch_calls == 1
    assert pipeline.cleanup_calls == [None, None]


@pytest.mark.asyncio
async def test_truncation_when_oversized(monkeypatch) -> None:
    # max_note_length below 500 is clamped up to 500 by PluginConfig
    config = PluginConfig.from_mapping({"max_note_length": 500, "enable_link": False})
    info = VideoInfo(bvid="BV1abc", title="t", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)

    long_md = "# t\n\n" + "## 章节\n" + ("内容 " * 400)  # ~1200 chars
    pipeline = _StubPipeline(_make_pipeline_output())
    orch = SummaryOrchestrator(
        config=config, llm=_StubLLM(long_md), pipeline=pipeline, http_client=_StubHTTP()  # type: ignore[arg-type]
    )
    result = await orch.generate("https://www.bilibili.com/video/BV1abc")
    assert "内容过长提示" in result.markdown
    # original length was ~1200; truncated to ~500 + tail message (~150)
    assert len(result.markdown) < 1100


@pytest.mark.asyncio
async def test_summary_is_cached_per_bvid(monkeypatch) -> None:
    config = PluginConfig.from_mapping({})
    info = VideoInfo(bvid="BV1xx411c7mD", title="t", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)

    pipeline = _StubPipeline(_make_pipeline_output())
    llm = _StubLLM()
    orch = SummaryOrchestrator(config=config, llm=llm, pipeline=pipeline, http_client=_StubHTTP())  # type: ignore[arg-type]

    url = "https://www.bilibili.com/video/BV1xx411c7mD"
    first = await orch.generate(url)
    second = await orch.generate(url)

    assert second is first  # second request served straight from cache
    assert len(llm.calls) == 1  # LLM invoked only once
    assert len(pipeline.cleanup_calls) == 1  # pipeline ran only once

    await orch.clear_cache()
    await orch.generate(url)
    assert len(llm.calls) == 2  # regenerated after the cache was cleared


@pytest.mark.asyncio
async def test_summary_cache_survives_restart(monkeypatch, tmp_path) -> None:
    config = PluginConfig.from_mapping({})
    info = VideoInfo(bvid="BV1xx411c7mD", title="t", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)

    first_llm = _StubLLM()
    first = SummaryOrchestrator(
        config=config,
        llm=first_llm,
        pipeline=_StubPipeline(_make_pipeline_output()),
        http_client=_StubHTTP(),  # type: ignore[arg-type]
        data_dir=str(tmp_path),
    )
    await first.generate("https://www.bilibili.com/video/BV1xx411c7mD")

    second_llm = _StubLLM()
    second = SummaryOrchestrator(
        config=config,
        llm=second_llm,
        pipeline=_StubPipeline(_make_pipeline_output()),
        http_client=_StubHTTP(),  # type: ignore[arg-type]
        data_dir=str(tmp_path),
    )
    await second.generate("https://www.bilibili.com/video/BV1xx411c7mD")

    assert len(first_llm.calls) == 1
    assert not second_llm.calls


@pytest.mark.asyncio
async def test_llm_timeout_surfaces_specific_error(monkeypatch) -> None:
    import asyncio as _asyncio

    from bilivideo.core.exceptions import BiliVideoError
    from bilivideo.summarize import orchestrator as orch_mod

    config = PluginConfig.from_mapping({})
    info = VideoInfo(bvid="BV1abc", title="t", owner_name="UP")
    _patch_get_video_info(monkeypatch, info)
    monkeypatch.setattr(orch_mod, "LLM_CHAT_TIMEOUT_SECONDS", 0.05)

    class _SlowLLM:
        async def chat(self, prompt: str, *, session_id: str | None = None) -> str:
            await _asyncio.sleep(1)
            return "too late"

    output = _make_pipeline_output(with_audio=True)
    pipeline = _StubPipeline(output)
    orch = SummaryOrchestrator(
        config=config, llm=_SlowLLM(), pipeline=pipeline, http_client=_StubHTTP()  # type: ignore[arg-type]
    )
    with pytest.raises(BiliVideoError) as excinfo:
        await orch.generate("https://www.bilibili.com/video/BV1abc")

    assert "AI 总结超时" in excinfo.value.user_message
    assert pipeline.cleanup_calls == [output.audio]  # cleanup still runs in finally

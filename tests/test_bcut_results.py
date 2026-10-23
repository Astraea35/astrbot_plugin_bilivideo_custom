"""BCut successful-empty results must not be confused with service failures."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from bilivideo.core.exceptions import TranscriptionError
from bilivideo.core.types import AudioDownloadResult, TranscriptResult
from bilivideo.transcription.bcut_provider import (
    BCutTranscriber,
    ENDPOINT_COMMIT,
    ENDPOINT_CREATE,
    ENDPOINT_TASK,
    MODEL_ID,
)
from bilivideo.transcription.pipeline import TranscriptPipeline


def _transcribe_result(monkeypatch, result):
    provider = BCutTranscriber()
    monkeypatch.setattr(provider, "_upload", lambda *_: {"download_url": "audio"})
    monkeypatch.setattr(provider, "_create_task", lambda *_: "task")
    monkeypatch.setattr(provider, "_await_result", lambda *_: {"result": result})
    return provider.transcribe("unused.mp3")


@pytest.mark.parametrize("utterances", [[], [{"transcript": "  "}]])
def test_success_without_speech_is_valid_empty_result(monkeypatch, utterances):
    result = _transcribe_result(monkeypatch, json.dumps({
        "utterances": utterances, "statusCode": 0, "version": "1.2.0",
    }))
    assert not result.has_content
    assert result.full_text == ""


def test_speech_and_millisecond_timestamps_are_preserved(monkeypatch):
    result = _transcribe_result(monkeypatch, json.dumps({"utterances": [{
        "transcript": " 测试语音 ", "start_time": 1200, "end_time": 2400,
    }]}))
    assert result.full_text == "测试语音"
    assert result.segments[0].start == 1.2
    assert result.segments[0].end == 2.4


@pytest.mark.parametrize("result,expected", [
    ('{"statusCode": 500, "utterances": []}', "statusCode=500"),
    ("not json", "invalid recognition result"),
    ("null", "invalid recognition result"),
    ("{}", "invalid recognition result"),
    ('{"utterances": null}', "invalid recognition result"),
    ('{"utterances": [null]}', "invalid utterance"),
    ('{"utterances": [{"transcript": "hello", "start_time": "bad"}]}', "invalid utterance"),
])
def test_bad_result_is_service_error_not_no_speech(monkeypatch, result, expected):
    with pytest.raises(TranscriptionError, match=expected) as caught:
        _transcribe_result(monkeypatch, result)
    assert "视频转写失败" in caught.value.user_message


def test_same_model_used_throughout_upload_create_and_poll(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    result = {"utterances": [], "statusCode": 0}
    responses = {
        ENDPOINT_CREATE: {"upload_urls": ["upload"], "per_size": 5,
                          "in_boss_key": "key", "resource_id": "resource", "upload_id": "upload"},
        ENDPOINT_COMMIT: {"download_url": "download"},
        ENDPOINT_TASK: {"task_id": "task"},
    }

    def post(url, *, data, **kwargs):
        assert json.loads(data)["model_id"] == MODEL_ID
        return Mock(json=lambda: {"code": 0, "data": responses[url]})

    def get(url, *, params, **kwargs):
        assert params == {"model_id": MODEL_ID, "task_id": "task"}
        return Mock(json=lambda: {"code": 0, "data": {"state": 4, "result": json.dumps(result)}})

    session = Mock(post=Mock(side_effect=post), get=Mock(side_effect=get))
    session.put.return_value.headers = {"Etag": '"tag"'}
    provider = BCutTranscriber()
    upload = provider._upload(session, str(audio))
    task = provider._create_task(session, upload["download_url"])
    assert json.loads(provider._await_result(session, task)["result"]) == result
    assert session.post.call_count == 3


@pytest.mark.parametrize("operation", ["post", "get"])
def test_api_errors_include_stage_and_code(operation):
    session = Mock()
    getattr(session, operation).return_value.json.return_value = {"code": -400, "message": "bad request"}
    provider = BCutTranscriber()
    with pytest.raises(TranscriptionError, match="code=-400.*bad request"):
        if operation == "post":
            provider._post_json(session, ENDPOINT_TASK, {})
        else:
            provider._await_result(session, "task")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, TranscriptionError("BCut result query failed (code=-400)")])
async def test_pipeline_reports_cause_and_cleans_audio(tmp_path, caplog, failure):
    audio = tmp_path / "video.mp3"
    audio.write_bytes(b"audio")
    meta = AudioDownloadResult(str(audio), "video", 56, None, "bilibili", "BVtest")
    downloader = SimpleNamespace(
        download_subtitles=Mock(return_value=None), download_audio=Mock(return_value=meta),
    )
    transcriber = SimpleNamespace(transcribe=Mock(
        return_value=TranscriptResult("zh", "", ()), side_effect=failure,
    ))
    pipeline = TranscriptPipeline(downloader, transcriber)
    with pytest.raises(TranscriptionError) as caught:
        await pipeline.fetch("https://www.bilibili.com/video/BVtest")
    assert not audio.exists()
    assert "BVtest" in caplog.text
    if failure is None:
        assert "未识别出语音" in caught.value.user_message
        assert "纯音乐" in caught.value.user_message
        assert "转写失败" not in caught.value.user_message
        assert "without recognized speech" in caplog.text
    else:
        assert caught.value is failure
        assert "code=-400" in caplog.text


def test_transport_error_stays_transcription_error(monkeypatch):
    provider = BCutTranscriber()
    monkeypatch.setattr(provider, "_upload", Mock(side_effect=requests.Timeout("timed out")))
    with pytest.raises(TranscriptionError, match="timed out"):
        provider.transcribe("unused.mp3")

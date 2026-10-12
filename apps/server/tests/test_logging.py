from types import SimpleNamespace
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from pipecat.frames.frames import (InterruptionFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, LLMTextFrame)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

from app.interaction import Scoped, ScopedRTVI, Session, tag
from app.logs import configure_logging, log_responses
from app.main import log_http_errors


@pytest.fixture
def console():
    configure_logging()


async def test_user_logs_skip_partial_and_expired_asr(console, capsys, monkeypatch):
    state = Session("dialogue", "mock", "session-secret")
    state.begin({"generation": 1})
    await state.send("input.transcribed", 1, text="临时文本", final=False)
    assert not capsys.readouterr().err
    await state.send("input.transcribed", 1, text="你在等谁？", final=True)
    state.begin({"generation": 2})
    await state.send("input.transcribed", 1, text="旧轮语音", final=True)
    called = []
    async def send_text(processor, data):
        called.append(data.content)
    monkeypatch.setattr(RTVIProcessor, "_handle_send_text", send_text)
    await ScopedRTVI(state=state)._handle_send_text(SimpleNamespace(content="我陪你等。"))
    output = capsys.readouterr().err
    assert output.count("[用户/语音]") == 1 and output.count("[用户/文字]") == 1
    assert "你在等谁？" in output and "我陪你等。" in output
    assert "旧轮语音" not in output and "session-secret" not in output
    assert called == ["我陪你等。"]


async def test_reply_logs_merge_chunks_and_keep_interrupted_origin(console, capsys):
    class Model(Scoped, FrameProcessor):
        pass
    state = Session("stream", "mock", "session-secret")
    state.begin({"generation": 1})
    model = Model(state=state)
    flush = log_responses(model, state)
    for frame in (LLMFullResponseStartFrame(), LLMTextFrame("你好，"), LLMTextFrame("坐一会儿吧。")):
        await model.push_frame(tag(frame, 1))
    assert not capsys.readouterr().err
    await model.push_frame(tag(LLMFullResponseEndFrame(), 1))
    state.begin({"generation": 2})
    await model.push_frame(tag(LLMFullResponseStartFrame(), 2))
    await model.push_frame(tag(LLMTextFrame("刚才我想说"), 2))
    state.begin({"generation": 3})
    await model.push_frame(tag(InterruptionFrame(), 3))
    await model.push_frame(tag(LLMTextFrame("旧回复不应打印"), 2))
    await model.push_frame(tag(LLMFullResponseEndFrame(), 2))
    flush("会话结束")
    output = capsys.readouterr().err
    assert output.count("[角色回复/") == 2
    assert "[角色回复/完成] 你好，坐一会儿吧。" in output
    assert "轮次=2 | [角色回复/已打断] 刚才我想说" in output
    assert "旧回复不应打印" not in output


def test_exception_trace_redacts_keys_and_does_not_dump_locals(console, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-llm-private-key")
    monkeypatch.setenv("VOLCENGINE_API_KEY", "fake-speech-private-key")
    private_header = "fake-llm-private-key"
    audio = b"raw-audio-not-for-console"
    try:
        raise RuntimeError(private_header)
    except RuntimeError:
        logger.exception("提供商拒绝 fake-speech-private-key")
    output = capsys.readouterr().err
    assert "RuntimeError: ***" in output and "Traceback" in output
    assert private_header not in output and "fake-speech-private-key" not in output
    assert audio.decode() not in output


def test_http_failure_keeps_response_behavior_and_logs_exception(console, capsys):
    app = FastAPI()
    app.middleware("http")(log_http_errors)
    @app.get("/boom")
    async def boom():
        raise RuntimeError("本地模拟异常")
    with TestClient(app) as client, pytest.raises(RuntimeError):
        client.get("/boom")
    output = capsys.readouterr().err
    assert "[HTTP] GET /boom 请求异常" in output
    assert "RuntimeError: 本地模拟异常" in output


def test_console_is_utf8_even_if_python_starts_with_gbk():
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp936", "LOGURU_LEVEL": "INFO",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    code = "from app.logs import configure_logging; from loguru import logger; configure_logging(); logger.info('中文对白')"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=10, check=True)
    assert "中文对白" in result.stderr.decode("utf-8")

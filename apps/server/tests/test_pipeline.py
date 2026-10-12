import asyncio
import json

import httpx
import pytest

from app.bot import make_bot
from app.interaction import Session
from app.services import live_services
from app.tools import SceneTools
from pipecat.frames.frames import LLMContextFrame, LLMTextFrame, FunctionCallResultFrame, LLMFullResponseEndFrame, ErrorFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner
from app.settings import LIVE_KEYS
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from app.interaction import tag


async def test_native_pipeline_can_be_configured():
    state = Session("test", "mock", "key")
    peer = SmallWebRTCConnection(ice_servers=[])
    run = make_bot(state, peer)
    run.close()
    assert state.runner is not None and state.rtvi is not None
    await peer.disconnect()


@pytest.mark.parametrize("thinking", [None, "disabled", "enabled"])
async def test_live_llm_streams_to_configured_url_and_passes_thinking(monkeypatch, thinking):
    # 使用本地 MockTransport 捕获请求，不访问真实提供商。
    for key in LIVE_KEYS:
        monkeypatch.setenv(key, "test-not-a-real-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("LLM_MODEL", "ep-test-doubao")
    if thinking is None:
        monkeypatch.delenv("LLM_THINKING", raising=False)
    else:
        monkeypatch.setenv("LLM_THINKING", thinking)
    state = Session("test", "live", "key")
    stt, llm, tts = live_services(state)
    assert stt.state is llm.state is tts.state is state
    def handler(request):
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-not-a-real-key"
        body = json.loads(request.content)
        assert body["model"] == "ep-test-doubao" and body["stream"] is True
        if thinking is None:
            assert "thinking" not in body
        else:
            assert body["thinking"] == {"type": thinking}
        chunk = {"id": "test", "object": "chat.completion.chunk", "created": 0,
            "model": "ep-test-doubao", "choices": [{"index": 0, "finish_reason": None,
                "delta": {"role": "assistant", "content": "接入成功"}}]}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            original = llm._client
            llm._client = original.with_options(http_client=http)
            await original.close()
            stream = await llm.get_chat_completions(LLMContext([{"role": "user", "content": "你好"}]))
            chunks = [chunk async for chunk in stream]
            assert chunks[0].choices[0].delta.content == "接入成功"
    finally:
        await llm._client.close()


@pytest.mark.parametrize("has_text", [False, True])
async def test_live_tool_result_reaches_native_context_and_generates_reply(monkeypatch, has_text):
    for key in LIVE_KEYS:
        monkeypatch.setenv(key, "test-not-a-real-key")
    state = Session("native-tools", "live", "key")
    state.begin({"generation": 1})
    _, llm, _ = live_services(state)
    context = LLMContext([{"role": "system", "content": "test"}, {"role": "user", "content": "你好"}])
    context.set_tools(SceneTools(state).register(llm))
    requests, results, replies = [], [], []
    done, tool_done = asyncio.Event(), asyncio.Event()
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "tool-1", "type": "function", "function": {
                "name": "perform_character", "arguments": '{"emotion":"curious","action":"none"}'}}]}
            finish = "tool_calls"
        else:
            assert len(requests) == 2
            assert any(message.get("role") == "tool" and message.get("tool_call_id") == "tool-1"
                       for message in body["messages"])
            delta, finish = {"content": "你好，坐一会儿吧。"}, "stop"
        chunks = []
        if len(requests) == 1 and has_text:
            chunks.append({"id": "test", "object": "chat.completion.chunk", "created": 0, "model": "test",
                           "choices": [{"index": 0, "delta": {"content": "你好，坐一会儿吧。"}, "finish_reason": None}]})
        chunks.append({"id": "test", "object": "chat.completion.chunk", "created": 0, "model": "test",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            content="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n")
    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, FunctionCallResultFrame):
                results.append(frame)
                tool_done.set()
            if isinstance(frame, LLMTextFrame):
                replies.append(frame.text)
            if isinstance(frame, LLMFullResponseEndFrame) and replies:
                done.set()
            await self.push_frame(frame, direction)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(Pipeline([Forward(), llm, Forward(), LLMAssistantAggregator(context)]),
                            app_resources=state, idle_timeout_secs=None)
    async def run():
        await runner.add_workers(worker)
        await runner.run()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            original = llm._client
            llm._client = original.with_options(http_client=http)
            await original.close()
            task = asyncio.create_task(run())
            try:
                await worker.queue_frames([tag(LLMContextFrame(context), 1)])
                await asyncio.wait_for(done.wait(), 2)
                await asyncio.wait_for(tool_done.wait(), 2)
                await asyncio.wait_for(worker.flush_pipeline(), 2)
                assert len(requests) == (1 if has_text else 2) and replies == ["你好，坐一会儿吧。"]
                assert results and all(frame.metadata.get("generation") == 1 for frame in results)
                assert all(frame.properties.run_llm is False for frame in results) if has_text else all(frame.properties is None for frame in results)
                assert state.dropped == 0
            finally:
                await runner.cancel()
                await asyncio.wait_for(task, 2)
    finally:
        await llm._client.close()


@pytest.mark.parametrize("failure", ["stall", "server_error", "empty"])
async def test_live_llm_failure_is_bounded_and_leaves_thinking(monkeypatch, failure):
    for key in LIVE_KEYS:
        monkeypatch.setenv(key, "test-not-a-real-key")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.1")
    state = Session("failure", "live", "key")
    state.begin({"generation": 1})
    messages = []
    async def send(type, generation=None, **payload):
        messages.append((type, payload))
    state.send = send
    _, llm, _ = live_services(state)
    context = LLMContext([{"role": "user", "content": "你好"}])
    calls = 0
    class Stall(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"id":"test","object":"chat.completion.chunk","created":0,"model":"test","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    stalled = Stall()
    def handler(request):
        nonlocal calls
        calls += 1
        if failure == "server_error":
            return httpx.Response(500, json={"error": {"message": "local simulated failure"}})
        if failure == "stall":
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stalled)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="data: [DONE]\n\n")
    frames, done = [], asyncio.Event()
    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            frames.append(frame)
            if isinstance(frame, LLMFullResponseEndFrame):
                done.set()
            await self.push_frame(frame, direction)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(Pipeline([Forward(), llm, Forward()]), app_resources=state, idle_timeout_secs=None)
    async def run():
        await runner.add_workers(worker)
        await runner.run()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            original = llm._client
            llm._client = original.with_options(http_client=http)
            await original.close()
            task = asyncio.create_task(run())
            try:
                await worker.queue_frames([tag(LLMContextFrame(context), 1)])
                await asyncio.wait_for(done.wait(), 2)
                assert calls == 1  # SDK 不为 HTTP 500 自动重试。
                assert ("response.state", {"state": "idle"}) in messages
                if failure == "stall":
                    assert stalled.closed
                    assert any(isinstance(frame, ErrorFrame) and isinstance(frame.exception, TimeoutError) for frame in frames)
                elif failure == "server_error":
                    assert any(isinstance(frame, ErrorFrame) for frame in frames)
                else:
                    assert any(type == "error" and "没有返回" in payload["message"] for type, payload in messages)
            finally:
                await runner.cancel()
                await asyncio.wait_for(task, 2)
    finally:
        await llm._client.close()

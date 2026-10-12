import asyncio
import base64
import gzip
import json
import struct

import httpx
import pytest
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame, LLMContextFrame, TranscriptionFrame, TTSAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.workers.runner import WorkerRunner
from websockets.asyncio.server import serve

from app import volcengine
from app.bot import PushStart, PushStop, User
from app.interaction import Controls, Session, scope


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("VOLCENGINE_STT_RESOURCE_ID", "volc.seedasr.sauc.duration")
    state = Session("test", "live", "secret")
    state.begin({"generation": 1, "kind": "voice"})
    return state


@pytest.fixture
def mock_http(monkeypatch):
    original = httpx.AsyncClient
    clients = []
    def install(handler):
        def create(**kwargs):
            client = original(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client
        monkeypatch.setattr(volcengine.httpx, "AsyncClient", create)
        return clients
    return install


@pytest.fixture
async def mock_ws(monkeypatch):
    servers = []
    async def install(handler):
        server = await serve(handler, "127.0.0.1", 0)
        servers.append(server)
        monkeypatch.setattr(volcengine, "STT_URL", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
    yield install
    for server in servers:
        server.close()
        await server.wait_closed()


def server_result(text=None, *, final=False, compression=True, sequence=True):
    body = json.dumps({"result": {"text": text} if text is not None else None}).encode()
    if compression:
        body = gzip.compress(body)
    flags = (1 if sequence else 0) | (2 if final else 0)
    wire = bytes((0x11, 0x90 | flags, 0x10 | int(compression), 0))
    if sequence:
        wire += struct.pack(">i", -2 if final else 1)
    return wire + struct.pack(">I", len(body)) + body


def server_error(code):
    body = b"provider error"
    return b"\x11\xf0\x00\x00" + struct.pack(">II", code, len(body)) + body


async def test_asr_streams_pcm_and_receives_text_before_release(state, mock_ws):
    partial, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def provider(socket):
        headers = socket.request.headers
        assert headers["X-Api-Key"] == "test-not-a-real-key"
        assert headers["X-Api-Resource-Id"] == "volc.seedasr.sauc.duration"
        assert headers["X-Api-Connect-Id"] == headers["X-Api-Request-Id"]
        full = await socket.recv()
        assert full[:4] == b"\x11\x10\x11\x00"
        assert struct.unpack_from(">I", full, 4)[0] == len(full) - 8
        body = json.loads(gzip.decompress(full[8:]))
        assert body["audio"] == {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
        assert body["request"]["result_type"] == "full" and body["request"]["enable_nonstream"]
        await socket.send(server_result())  # 初始确认可没有转写结果。
        audio = await socket.recv()
        assert audio[:4] == b"\x11\x20\x01\x00"
        assert gzip.decompress(audio[8:]) == b"\0\1" * 3200
        await socket.send(server_result("你好米"))
        last = await socket.recv()
        assert release.is_set() and last[:4] == b"\x11\x22\x01\x00"
        await socket.send(server_result("你好米拉", final=True))
    await mock_ws(provider)
    async def audio():
        yield b"\0\1" * 3200
        await release.wait()
    async def on_text(text):
        seen.append(text)
        partial.set()
    task = asyncio.create_task(volcengine.stream_asr(audio(), state.id, on_text))
    try:
        await asyncio.wait_for(partial.wait(), 2)
        assert not task.done() and seen == ["你好米"]
        release.set()
        assert await asyncio.wait_for(task, 2) == "你好米拉"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("compression,sequence", [(True, True), (False, True), (True, False)])
def test_asr_accepts_final_flag_with_optional_sequence(compression, sequence):
    result, final = volcengine.asr_response(server_result("最终文本", final=True, compression=compression, sequence=sequence))
    assert final and result["result"]["text"] == "最终文本"


def test_asr_handles_silence_and_rejects_errors_or_truncated_packets():
    assert volcengine.asr_response(server_error(45000002)) == ({"result": {"text": ""}}, True)
    for wire in (server_error(45000001), server_result("不采纳")[:-1], b"\x11\x91\x11\x00\0\0\0\1", "text"):
        with pytest.raises(ValueError):
            volcengine.asr_response(wire)


async def test_asr_does_not_accept_partial_text_when_socket_closes(state, mock_ws):
    async def provider(socket):
        await socket.recv()
        await socket.send(server_result("只有临时文本"))
        await socket.close()
    await mock_ws(provider)
    async def audio():
        if False:
            yield b""
    async def partial(text):
        pass
    with pytest.raises(ValueError):
        await asyncio.wait_for(volcengine.stream_asr(audio(), state.id, partial), 2)


async def test_asr_provider_error_ends_stream_while_button_is_held(state, mock_ws):
    async def provider(socket):
        await socket.recv()
        await socket.send(server_error(45000001))
        await socket.wait_closed()
    await mock_ws(provider)
    cancelled = asyncio.Event()
    async def audio():
        try:
            await asyncio.Event().wait()
            yield b""
        finally:
            cancelled.set()
    async def partial(text):
        pass
    with pytest.raises(ValueError):
        await asyncio.wait_for(volcengine.stream_asr(audio(), state.id, partial), 2)
    assert cancelled.is_set()


async def test_native_ptt_aggregator_waits_for_release_and_final_asr(state, mock_ws):
    state = Session("pipeline", "live", "secret")
    partial, ended, allow_final, model_called = (asyncio.Event() for _ in range(4))
    async def provider(socket):
        await socket.recv()
        await socket.recv()
        await socket.send(server_result("临时文本不要提交"))
        last = await socket.recv()
        assert last[1] & 2
        ended.set()
        await allow_final.wait()
        await socket.send(server_result("可以看看那张照片吗？", final=True))
    await mock_ws(provider)
    async def capture_message(type, generation=None, **payload):
        if type == "input.transcribed" and payload.get("final") is False:
            partial.set()
    state.send = capture_message
    context = LLMContext([{"role": "system", "content": "test"}])
    user = User(context, state=state, params=LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(start=[PushStart()], stop=[PushStop(timeout=.3)])))
    inputs = []
    class CaptureModel(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                inputs.extend(message["content"] for message in frame.context.get_messages() if message["role"] == "user")
                model_called.set()
            await self.push_frame(frame, direction)
    stt = volcengine.VolcSTT(state=state)
    worker = PipelineWorker(Pipeline([Controls(state), stt, user, CaptureModel()]), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    async def run():
        await runner.add_workers(worker)
        await runner.run()
    task = asyncio.create_task(run())
    try:
        await worker.queue_frames([
            RTVIClientMessageFrame("1", "push_to_talk", {"state": "start", "generation": 1}),
            InputAudioRawFrame(b"\0\1" * 3200, 16000, 1)])
        await asyncio.wait_for(partial.wait(), 2)
        await asyncio.sleep(.4)  # 超过聚合器 timeout，按住仍不提交。
        assert not model_called.is_set() and not stt.request.done()
        await worker.queue_frames([RTVIClientMessageFrame("2", "push_to_talk", {"state": "stop", "generation": 1})])
        await asyncio.wait_for(ended.wait(), 2)
        await asyncio.sleep(.4)
        assert not model_called.is_set()  # 松开也必须等待最终识别结果。
        allow_final.set()
        await asyncio.wait_for(model_called.wait(), 2)
        assert inputs == ["可以看看那张照片吗？"]
    finally:
        allow_final.set()
        await runner.cancel()
        await asyncio.wait_for(task, 2)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aiter__(self):
        for item in self.items:
            yield item
            await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


def envelope(audio=b"", code=0):
    return (json.dumps({"code": code, "data": base64.b64encode(audio).decode()}) + "\n").encode()


def finished():
    # 与官方 V3 完成包一致；不能用空音频 code=0 代替此消息。
    return b'{"code":20000000,"message":"ok","data":null}\n'


async def test_tts_streams_pcm_before_eof_and_preserves_split_samples(state, mock_http):
    metadata = b'{"code":0,"data":null,"sentence":{"text":"subtitle"}}\n'
    wire = envelope(b"\1\2\3") + metadata + envelope(b"\4") + finished()
    stream = Chunks([wire[:9], wire[9:20], wire[20:]])
    def handler(request):
        assert str(request.url) == volcengine.TTS_URL
        assert request.headers["X-Api-Resource-Id"] == "seed-tts-2.0"
        params = json.loads(request.content)["req_params"]
        assert params["audio_params"] == {"format": "pcm", "sample_rate": 24000}
        assert params["speaker"] == "zh_female_vv_uranus_bigtts"
        return httpx.Response(200, stream=stream)
    clients = mock_http(handler)
    tts = volcengine.VolcTTS(state=state)
    messages = []
    async def capture(type, generation=None, **payload):
        messages.append(type)
    state.send = capture
    token = scope.set(1)
    try:
        frames = [frame async for frame in tts.run_tts("你好", "voice-1")]
    finally:
        scope.reset(token)
    audio = [frame for frame in frames if isinstance(frame, TTSAudioRawFrame)]
    assert [frame.audio for frame in audio] == [b"\1\2", b"\3\4"]
    assert all(frame.context_id == "voice-1" for frame in audio)
    assert state.audio["voice-1"] == (1, "你好")
    assert stream.closed and all(c.is_closed for c in clients)
    assert "error" not in messages


async def test_tts_requires_finish_and_stops_reading_after_success(state, mock_http):
    # 完成后即关闭响应，不等待 EOF，也不继续读取后续帧。
    stream = Chunks([envelope(b"\0\1"), finished(), b"should not be parsed"])
    mock_http(lambda _: httpx.Response(200, stream=stream))
    messages = []
    async def capture(type, generation=None, **payload):
        messages.append(type)
    state.send = capture
    token = scope.set(1)
    try:
        frames = [frame async for frame in volcengine.VolcTTS(state=state).run_tts("你好", "finished")]
        assert sum(isinstance(frame, TTSAudioRawFrame) for frame in frames) == 1
        assert "error" not in messages and stream.closed
    finally:
        scope.reset(token)


async def test_tts_reports_truncated_stream_instead_of_success(state, mock_http):
    mock_http(lambda _: httpx.Response(200, stream=Chunks([envelope(b"\0\1")])))
    messages = []
    async def capture(type, generation=None, **payload):
        messages.append((type, payload))
    state.send = capture
    token = scope.set(1)
    try:
        _ = [frame async for frame in volcengine.VolcTTS(state=state).run_tts("保留文本", "truncated")]
        assert any(type == "error" for type, _ in messages)
        assert ("subtitle.update", {"text": "保留文本"}) in messages
    finally:
        scope.reset(token)


async def test_tts_closes_http_stream_when_interrupted(state, mock_http):
    stream = Chunks([envelope(b"\0\1"), envelope(b"\2\3")])
    mock_http(lambda _: httpx.Response(200, stream=stream))
    token = scope.set(1)
    generator = volcengine.VolcTTS(state=state).run_tts("旧回答", "old")
    try:
        assert isinstance(await anext(generator), TTSAudioRawFrame)
        state.begin({"generation": 2})
        assert [frame async for frame in generator] == []
        assert stream.closed
    finally:
        await generator.aclose()
        scope.reset(token)


async def test_tts_provider_error_keeps_text_and_allows_retry(state, mock_http, capsys):
    calls = 0
    messages = []
    async def capture(type, generation=None, **payload):
        messages.append((type, payload))
    state.send = capture
    def handler(_):
        nonlocal calls
        calls += 1
        wire = envelope(code=45000001) if calls == 1 else envelope(b"\0\1") + finished()
        return httpx.Response(200, stream=Chunks([wire]))
    mock_http(handler)
    tts = volcengine.VolcTTS(state=state)
    token = scope.set(1)
    try:
        first = [frame async for frame in tts.run_tts("保留这句话", "failed")]
        assert not any(isinstance(frame, TTSAudioRawFrame) for frame in first)
        assert ("subtitle.update", {"text": "保留这句话"}) in messages
        second = [frame async for frame in tts.run_tts("重试", "retry")]
        assert any(isinstance(frame, TTSAudioRawFrame) for frame in second)
        assert sum(type == "error" for type, _ in messages) == 1
        output = capsys.readouterr().err
        assert "[TTS] 语音合成失败" in output and "45000001" in output
    finally:
        scope.reset(token)


async def test_ptt_buffers_bounded_audio_and_drops_late_asr(state, monkeypatch):
    stt = volcengine.VolcSTT(state=state)
    await stt.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=TaskManager(loop=asyncio.get_running_loop()), pipeline_worker=None))
    received = []
    async def capture(frame, direction=None):
        received.append(frame)
    stt.push_frame = capture
    entered, finish = asyncio.Event(), asyncio.Event()
    async def provider(audio, uid, on_text):
        chunks = [chunk async for chunk in audio]
        assert sum(map(len, chunks)) == volcengine.MAX_AUDIO_BYTES
        entered.set()
        await finish.wait()
        return "旧语音"
    monkeypatch.setattr(volcengine, "stream_asr", provider)
    direction = FrameDirection.DOWNSTREAM
    try:
        await stt.process_frame(RTVIClientMessageFrame("1", "push_to_talk", {"state": "start", "generation": 1}), direction)
        await stt.process_frame(InterruptionFrame(), direction)
        assert stt.recording == 1
        await stt.process_frame(InputAudioRawFrame(b"\0\1" * (volcengine.MAX_AUDIO_BYTES + 10), 16000, 1), direction)
        assert stt.audio_bytes == volcengine.MAX_AUDIO_BYTES
        assert stt.audio.qsize() == 75 and len(stt.buffer) < volcengine.PACKET_BYTES
        assert not any(isinstance(frame, TranscriptionFrame) for frame in received)
        await stt.process_frame(RTVIClientMessageFrame("2", "push_to_talk", {"state": "stop", "generation": 1}), direction)
        await asyncio.wait_for(entered.wait(), 1)
        state.begin({"generation": 2})
        finish.set()
        await stt.request
        assert not any(isinstance(frame, TranscriptionFrame) for frame in received)
        assert state.dropped == 1
    finally:
        await stt.cleanup()


async def test_new_turn_cancels_inflight_asr_websocket(state, mock_ws):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def provider(socket):
        await socket.recv()
        entered.set()
        await socket.wait_closed()
        cancelled.set()
    await mock_ws(provider)
    stt = volcengine.VolcSTT(state=state)
    await stt.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=TaskManager(loop=asyncio.get_running_loop()), pipeline_worker=None))
    try:
        direction = FrameDirection.DOWNSTREAM
        await stt.process_frame(RTVIClientMessageFrame("1", "push_to_talk", {"state": "start", "generation": 1}), direction)
        request = stt.request
        await stt.process_frame(InterruptionFrame(), direction)
        await asyncio.wait_for(entered.wait(), 2)
        assert not request.done()  # 新 PTT 自身的中断保留当前 ASR。
        state.begin({"generation": 2})
        await stt.process_frame(InterruptionFrame(), direction)
        await asyncio.wait_for(cancelled.wait(), 2)
        assert request.cancelled()
        assert stt.request is None and stt.recording is None
    finally:
        await stt.cleanup()

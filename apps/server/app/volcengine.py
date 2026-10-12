"""豆包流式 ASR 与 HTTP 分块 TTS；Pipecat 管理媒体、上下文与打断。"""
import asyncio
import base64
import gzip
import json
import os
import struct
import time
import uuid

import httpx
from pipecat.frames.frames import (CancelFrame, EndFrame, InputAudioRawFrame,
    InterruptionFrame, TranscriptionFrame, TTSAudioRawFrame, TTSStoppedFrame)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .interaction import Scoped, scope, tag
from .logs import session_logger

STT_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
INPUT_RATE = 16000
MAX_AUDIO_BYTES = INPUT_RATE * 2 * 15  # 单声道 PCM16，最多 15 秒，仅保存在内存。
PACKET_BYTES = INPUT_RATE * 2 // 5  # 官方建议双向流式每包 200ms。


def headers(resource):
    return {"X-Api-Key": os.environ["VOLCENGINE_API_KEY"],
            "X-Api-Resource-Id": resource, "X-Api-Request-Id": str(uuid.uuid4())}


def asr_packet(payload: bytes, *, audio=False, final=False) -> bytes:
    """V1 四字节头 + 大端长度 + gzip；末包用 flags=2，不携带序号。"""
    payload = gzip.compress(payload)
    header = bytes((0x11, (0x20 | (2 if final else 0)) if audio else 0x10,
                    0x01 if audio else 0x11, 0))
    return header + struct.pack(">I", len(payload)) + payload


def asr_response(wire: bytes) -> tuple[dict, bool]:
    if not isinstance(wire, bytes) or len(wire) < 8 or wire[0] >> 4 != 1:
        raise ValueError("无效的 ASR 二进制响应")
    offset = (wire[0] & 15) * 4
    kind, flags = wire[1] >> 4, wire[1] & 15
    if offset < 4 or kind not in (9, 15) or flags > 3:
        raise ValueError("无效的 ASR 响应头")
    code = None
    if kind == 15 or flags & 1:
        if len(wire) < offset + 8:
            raise ValueError("ASR 响应缺少序号或长度")
        code = struct.unpack_from(">I", wire, offset)[0] if kind == 15 else None
        offset += 4
    if len(wire) < offset + 4:
        raise ValueError("ASR 响应缺少长度")
    size = struct.unpack_from(">I", wire, offset)[0]
    payload = wire[offset + 4:]
    if len(payload) != size:
        raise ValueError("ASR 响应长度不一致")
    if kind == 15:
        if code == 45000002:  # 空音频不触发 LLM。
            return {"result": {"text": ""}}, True
        raise ValueError(f"ASR 服务错误：{code}")
    if wire[2] >> 4 != 1 or wire[2] & 15 not in (0, 1):
        raise ValueError("ASR 响应编码不受支持")
    if wire[2] & 15:
        payload = gzip.decompress(payload)
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("ASR 响应不是 JSON 对象")
    if result.get("code", 20000000) != 20000000:
        raise ValueError(f"ASR 返回非成功状态：{result.get('code')}")
    return result, bool(flags & 2)


async def stream_asr(audio, uid: str, on_text) -> str:
    """边发送 PCM 边接收全量文本；只返回末包确认的最终结果。"""
    payload = {"user": {"uid": uid},
               "audio": {"format": "pcm", "codec": "raw", "rate": INPUT_RATE, "bits": 16, "channel": 1},
               "request": {"model_name": "bigmodel", "result_type": "full", "enable_nonstream": True}}
    request_headers = headers(os.getenv("VOLCENGINE_STT_RESOURCE_ID", "volc.seedasr.sauc.duration"))
    request_headers["X-Api-Connect-Id"] = request_headers["X-Api-Request-Id"]
    async with connect(STT_URL, additional_headers=request_headers, open_timeout=5,
                       close_timeout=1, max_size=1024 * 1024) as socket:
        await socket.send(asr_packet(json.dumps(payload, ensure_ascii=False).encode()))

        async def receive():
            previous = ""
            async for wire in socket:
                result, final = asr_response(wire)
                transcript = result.get("result") or {}
                if not isinstance(transcript, dict):
                    raise ValueError("ASR 识别结果格式错误")
                text = transcript.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("ASR 未返回文本")
                text = text.strip()
                if final:
                    return text
                if text and text != previous:
                    await on_text(text)
                    previous = text
            raise ValueError("ASR 连接中断，未收到最终结果")

        try:
            # 接收失败或取消时，TaskGroup 同时停止发送和接收，随后关闭连接。
            async with asyncio.TaskGroup() as group:
                response = group.create_task(receive())
                loop = asyncio.get_running_loop()
                next_send = loop.time()
                async for chunk in audio:
                    # 握手期间积压的包也按音频时长发送，避免瞬间突发。
                    await asyncio.sleep(max(0, next_send - loop.time()))
                    await socket.send(asr_packet(chunk, audio=True))
                    next_send = loop.time() + len(chunk) / (INPUT_RATE * 2)
                await asyncio.sleep(max(0, next_send - loop.time()))
                await socket.send(asr_packet(b"", audio=True, final=True))
                async with asyncio.timeout(10):
                    text = await response
            return text
        except ExceptionGroup as error:
            raise ValueError("ASR 流中断") from error


class VolcSTT(Scoped, FrameProcessor):
    """每轮 PTT 一条可取消连接；临时转写上屏，松开后才提交用户上下文。"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.recording = None
        self.buffer = bytearray()
        self.audio = None
        self.audio_bytes = 0
        self.request = None
        self.stop_task = None

    async def cancel_request(self):
        for task in (self.stop_task, self.request):
            if task and not task.done():
                await self.cancel_task(task)
        self.request = None
        self.stop_task = None
        self.recording = None
        self.audio = None
        self.buffer.clear()

    async def cleanup(self):
        await self.cancel_request()
        await super().cleanup()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            if self.audio is not None and self.state.current(self.recording):
                chunk = frame.audio[:MAX_AUDIO_BYTES - self.audio_bytes]
                self.audio_bytes += len(chunk)
                self.buffer.extend(chunk)
                while len(self.buffer) >= PACKET_BYTES:
                    self.audio.put_nowait(bytes(self.buffer[:PACKET_BYTES]))
                    del self.buffer[:PACKET_BYTES]
            return
        if isinstance(frame, RTVIClientMessageFrame) and frame.type == "push_to_talk":
            data = frame.data or {}
            if data.get("state") == "start":
                await self.cancel_request()
                self.recording = data["generation"]
                self.audio_bytes = 0
                # 容量覆盖最长 15 秒及末包，握手失败不会无限积压麦克风数据。
                self.audio = asyncio.Queue(maxsize=80)
                self.request = self.create_task(self.recognize(self.recording, self.audio), "volc-stt")
            elif data.get("generation") == self.recording and self.state.current(self.recording):
                if self.stop_task is None:
                    self.stop_task = self.create_task(self.finish_audio(self.recording), "volc-stt-stop")
        elif isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
            # 新 PTT 自身产生的打断不能关闭已经建立的新识别连接。
            if not isinstance(frame, InterruptionFrame) or not self.state.current(self.recording):
                await self.cancel_request()
        await self.push_frame(frame, direction)

    async def finish_audio(self, generation):
        # 让松开前最后一小段 RTP 音频到达；等待期间仍可被新输入取消。
        await asyncio.sleep(.12)
        if self.state.current(generation) and self.recording == generation and self.audio is not None:
            if self.buffer:
                self.audio.put_nowait(bytes(self.buffer))
            self.audio.put_nowait(None)
            self.audio = None
            self.buffer.clear()

    async def recognize(self, generation, queue):
        started = time.perf_counter()
        async def audio():
            while (chunk := await queue.get()) is not None:
                yield chunk

        async def partial(text):
            await self.state.send("input.transcribed", generation, text=text, simulated=False, final=False)

        try:
            async with asyncio.timeout(30):
                text = await stream_asr(audio(), self.state.id, partial)
            session_logger(self.state, generation).info("[耗时/ASR] 最终转写就绪 {:.0f}ms（含按住说话时间）", (time.perf_counter() - started) * 1000)
            if not self.state.current(generation):
                self.state.dropped += 1
                return
            if not text:
                await self.state.error("没有听清，请按住说话重试，或输入文字。", generation)
                await self.state.send("response.state", generation, state="idle")
                return
            await self.state.send("input.transcribed", generation, text=text, simulated=False, final=True)
            await self.push_frame(tag(TranscriptionFrame(text, "user", time_now_iso8601()), generation))
        except (WebSocketException, OSError, TimeoutError, ValueError, KeyError, TypeError, struct.error) as error:
            session_logger(self.state, generation).opt(exception=error).error("[ASR] 语音识别失败")
            await self.state.error("语音识别暂时不可用，可以重试或输入文字。", generation)
            await self.state.send("response.state", generation, state="idle")
        finally:
            if self.recording == generation:
                self.recording = None
                self.audio = None
                self.buffer.clear()


class VolcTTS(Scoped, TTSService):
    def __init__(self, **kwargs):
        super().__init__(settings=TTSSettings(model="seed-tts-2.0",
            voice=os.getenv("VOLCENGINE_TTS_SPEAKER", "zh_female_vv_uranus_bigtts"), language="zh"),
            sample_rate=24000, push_start_frame=True, reuse_context_id_within_turn=False,
            max_consecutive_zero_audio_contexts=0, **kwargs)

    async def run_tts(self, text, context_id):
        generation = scope.get()
        started = time.perf_counter()
        session_logger(self.state, generation).info("[TTS] 开始合成，context_id={}", context_id)
        self.state.audio[context_id] = (generation, text)
        payload = {"req_params": {"text": text, "speaker": self._settings.voice,
                                  "audio_params": {"format": "pcm", "sample_rate": 24000}}}
        tail = b""
        received_audio = False
        finished = False
        try:
            async with asyncio.timeout(20), httpx.AsyncClient(timeout=10) as client:
                async with client.stream("POST", TTS_URL,
                    headers=headers(os.getenv("VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0")), json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not self.state.current(generation):
                            return
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        # V3: 0 是音频/字幕消息；20000000 才是合成完成，data 通常为 null。
                        if chunk.get("code") == 20000000:
                            finished = True
                            break
                        if chunk.get("code") != 0:
                            raise ValueError(f"火山合成失败：code={chunk.get('code')} message={chunk.get('message', '')}")
                        # 字幕和时间戳消息没有音频；不能把 null 交给 Base64 解码器。
                        data = chunk.get("data")
                        if data is None:
                            continue
                        audio = tail + base64.b64decode(data, validate=True)
                        size = len(audio) & ~1
                        tail = audio[size:]
                        if size:
                            if not received_audio:
                                session_logger(self.state, generation).info("[耗时/TTS] 首段音频 {:.0f}ms，context_id={}", (time.perf_counter() - started) * 1000, context_id)
                            received_audio = True
                            yield TTSAudioRawFrame(audio[:size], 24000, 1, context_id=context_id)
                    if not finished:
                        raise ValueError("火山合成响应中断，未收到完成状态")
                    if tail or not received_audio:
                        raise ValueError("火山合成未返回完整 PCM 音频")
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
            session_logger(self.state, generation).opt(exception=error).error("[TTS] 语音合成失败，context_id={}", context_id)
            await self.state.error("语音合成暂时不可用，当前回复保留为文字。", generation)
            await self.state.send("subtitle.update", generation, text=text)
            await self.state.send("response.state", generation, state="idle")
        yield TTSStoppedFrame(context_id=context_id)

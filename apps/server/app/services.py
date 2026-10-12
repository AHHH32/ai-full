"""Native Pipecat services with small producer-side generation guards."""
import asyncio
import os
import time
import wave
from functools import lru_cache

import numpy as np
from pipecat.frames.frames import (LLMContextFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, LLMTextFrame, TranscriptionFrame, InputAudioRawFrame,
    TTSStoppedFrame, TTSAudioRawFrame)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.services.tts_service import TTSService
from pipecat.services.settings import TTSSettings
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.time import time_now_iso8601

from .interaction import Scoped, scope, tag
from .settings import ASSETS
from .story import REPLIES, intent, sentences, audio_key
from .volcengine import VolcSTT, VolcTTS
from .logs import session_logger


@lru_cache(maxsize=32)
def pcm(text):
    chunks = []
    for sentence in sentences(text):
        with wave.open(str(ASSETS / "audio" / f"{audio_key(sentence)}.wav")) as source:
            rate = source.getframerate()
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        target = np.arange(round(len(samples) * 24000 / rate)) * rate / 24000
        chunks.append(np.interp(target, np.arange(len(samples)), samples).astype("<i2").tobytes())
    return b"".join(chunks)


class MockSTT(Scoped, FrameProcessor):
    async def process_frame(self, frame, direction):
        if isinstance(frame, InputAudioRawFrame):
            return  # Mock never pretends to recognize microphone audio.
        if isinstance(frame, RTVIClientMessageFrame) and frame.type == "push_to_talk":
            if (frame.data or {}).get("state") == "stop":
                generation = frame.data.get("generation")
                if self.state.current(generation):
                    text = self.state.mock_text
                    await self.state.send("input.transcribed", generation, text=text, simulated=True)
                    await self.push_frame(tag(TranscriptionFrame(text, "user", time_now_iso8601()), generation))
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class MockLLM(Scoped, FrameProcessor):
    def __init__(self, *, tools, **kwargs):
        super().__init__(**kwargs)
        self.tools = tools

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return
        generation = frame.metadata["generation"]
        token = scope.set(generation)
        try:
            await self.state.send("response.state", generation, state="thinking")
            fault = self.state.fault
            if fault == "old_result_late":
                # A diagnostic provider callback deliberately outlives the interrupted request.
                async def late():
                    await asyncio.sleep(2)
                    await self.push_frame(tag(LLMTextFrame("旧回答不应出现"), generation))
                self.create_task(late(), "mock-late-provider")
            await asyncio.sleep(1 if fault == "model_timeout" else .2)
            messages = frame.context.get_messages()
            text = next((m.get("content", "") for m in reversed(messages) if m["role"] == "user"), "你好")
            key = intent(text)
            if fault == "model_timeout":
                await self.state.error("模型响应超时，请重新发送。", generation)
                await self.state.send("response.state", generation, state="idle")
                return
            emotion = "guarded" if key == "waiting" else "warm" if key in ("company", "photo", "message") else "curious"
            await self.tools.perform(generation, emotion, "look_window" if key == "waiting" else "none")
            if key == "photo":
                if not await self.tools.photo(generation, fault == "asset_failure"):
                    key = "photo_error"
            if key in ("company", "message"):
                self.tools.close(generation, "stay" if key == "company" else "message")
                await self.state.send("scene.snapshot", generation, scene=self.state.scene())
            for output in (LLMFullResponseStartFrame(), LLMTextFrame(REPLIES[key]), LLMFullResponseEndFrame()):
                await self.push_frame(tag(output, generation))
        finally:
            scope.reset(token)


class MockTTS(Scoped, TTSService):
    def __init__(self, **kwargs):
        super().__init__(settings=TTSSettings(model="mock", voice="Huihui", language="zh"),
                         push_start_frame=True, max_consecutive_zero_audio_contexts=0, **kwargs)

    async def run_tts(self, text, context_id):
        generation = scope.get()
        self.state.audio[context_id] = (generation, text)
        if self.state.fault == "tts_failure":
            await self.state.error("语音合成暂时不可用，请重新发送。", generation)
            await self.state.send("subtitle.update", generation, text=text)
            await self.state.send("response.state", generation, state="idle")
            return
        audio = pcm(text)
        for offset in range(0, len(audio), 960):
            yield TTSAudioRawFrame(audio[offset:offset + 960], 24000, 1, context_id=context_id)
            await asyncio.sleep(.005)
        yield TTSStoppedFrame(context_id=context_id)


class LiveLLM(Scoped, OpenAILLMService):
    def __init__(self, **kwargs):
        self.completion_timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
        self.request = None
        super().__init__(**kwargs)
        self.add_event_handler("on_before_push_frame", self.first_text)

    def create_client(self, **kwargs):
        # Pipecat 的 retry_timeout_secs 在 retry_on_timeout=False 时不生效。
        # SDK 自身默认等待很久并重试；实时对白显式限制等待，关闭自动重试。
        return super().create_client(**kwargs).with_options(timeout=10, max_retries=0)

    async def get_chat_completions(self, context):
        started = time.perf_counter()
        stream = await super().get_chat_completions(context)
        session_logger(self.state, scope.get()).info("[耗时/LLM] 已收到响应头 {:.0f}ms", (time.perf_counter() - started) * 1000)
        return stream

    async def first_text(self, processor, frame):
        request = self.request
        if (request and isinstance(frame, LLMTextFrame) and frame.text.strip()
            and frame.metadata.get("generation") == request["generation"] and not request["has_text"]):
            request["has_text"] = True
            session_logger(self.state, request["generation"]).info("[耗时/LLM] 首段文字 {:.0f}ms", (time.perf_counter() - request["started"]) * 1000)

    async def _process_context(self, context):
        generation = scope.get()
        request = {"generation": generation, "started": time.perf_counter(), "has_text": False, "has_tools": False}
        self.request = request
        thinking = self._settings.extra.get("extra_body", {}).get("thinking", {}).get("type", "模型默认")
        session_logger(self.state, generation).info("[LLM] 开始请求 model={} thinking={} 总时限={}s", self._settings.model, thinking, self.completion_timeout)
        try:
            # 覆盖响应与整个流；工具任务另有原生时限，异常由 process_frame 发出 ErrorFrame。
            async with asyncio.timeout(self.completion_timeout):
                await super()._process_context(context)
            if not request["has_text"] and not request["has_tools"] and self.state.current(generation):
                await self.state.error("模型没有返回可朗读的回复，请重新发送。", generation)
                await self.state.send("response.state", generation, state="idle")
        except Exception:
            await self.state.send("response.state", generation, state="idle")
            raise
        finally:
            session_logger(self.state, generation).info("[耗时/LLM] 本次请求结束 {:.0f}ms", (time.perf_counter() - request["started"]) * 1000)

    async def run_function_calls(self, function_calls):
        if not function_calls:
            return
        request = self.request
        # 已有对白，且只更新表情/动作时，无需为同一回答再请求模型。
        skip_followup = bool(request and request["has_text"] and
                             all(call.function_name == "perform_character" for call in function_calls))
        if request:
            request["has_tools"] = True
        for call in function_calls:
            self.state.calls[call.tool_call_id] = (scope.get(), skip_followup)
        await super().run_function_calls(function_calls)

    async def process_frame(self, frame, direction):
        if isinstance(frame, LLMContextFrame):
            generation = frame.metadata.get("generation", self.state.generation)
            if self.state.output_generation != generation:
                await self.state.send("response.state", generation, state="thinking")
        await super().process_frame(frame, direction)


def live_services(state):
    thinking = os.getenv("LLM_THINKING", "").strip()
    extra = {"extra_body": {"thinking": {"type": thinking}}} if thinking else {}
    llm = LiveLLM(state=state, api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("LLM_BASE_URL") or None,
        retry_on_timeout=False,
        settings=OpenAILLMService.Settings(model=os.getenv("LLM_MODEL", "gpt-4.1-mini"), extra=extra))
    return VolcSTT(state=state), llm, VolcTTS(state=state)

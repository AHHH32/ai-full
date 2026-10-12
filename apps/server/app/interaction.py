"""Small session state and generation guards; Pipecat owns the running tasks."""
import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pipecat.frames.frames import (DataFrame, Frame, LLMContextFrame,
                                  LLMFullResponseStartFrame, LLMFullResponseEndFrame)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

from .story import persona
from .logs import session_logger

scope: ContextVar[int | None] = ContextVar("reply_generation", default=None)


def response_frame(frame):
    return isinstance(frame, (DataFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame))


class Input(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generation: StrictInt = Field(ge=1)
    kind: Literal["text", "voice"] = "text"
    mock_text: str = Field(default="你在等谁？", max_length=500)
    fault: Literal["none", "model_timeout", "tts_failure", "asset_failure", "old_result_late"] = "none"


@dataclass
class Session:
    id: str
    mode: str
    key: str
    generation: int = 0
    input_generation: int | None = None
    mock_text: str = "你在等谁？"
    fault: str = "none"
    photo_shown: bool = False
    ending_choice: str | None = None
    revision: int = 0
    closed: bool = False
    dropped: int = 0
    peer: object | None = None
    task: asyncio.Task | None = None
    runner: object | None = None
    rtvi: RTVIProcessor | None = None
    pending: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    calls: dict = field(default_factory=dict)
    interruptions: dict = field(default_factory=dict)
    output_generation: int = 0
    spoken_context: str | None = None
    output_context: str | None = None

    def current(self, generation: int | None) -> bool:
        return not self.closed and generation == self.generation

    def scene(self) -> dict:
        return {"photo_shown": self.photo_shown, "lighting": "warm" if self.photo_shown else "cool",
                "ending_choice": self.ending_choice, "revision": self.revision}

    def begin(self, data: dict) -> bool:
        value = Input.model_validate(data)
        if value.generation <= self.generation:
            return False
        self.generation = value.generation
        self.input_generation = value.generation if value.kind == "voice" else None
        self.mock_text = value.mock_text
        self.fault = value.fault if self.mode == "mock" else "none"
        for _, future in self.pending.values():
            if not future.done():
                future.set_result(False)
        self.pending.clear()
        return True

    async def send(self, type: str, generation: int | None = None, **payload):
        generation = self.generation if generation is None else generation
        if not self.current(generation):
            return
        if type == "input.transcribed" and payload.get("final", True):
            source = "模拟语音" if payload.get("simulated") else "语音"
            session_logger(self, generation).info("[用户/{}] {}", source, payload.get("text", ""))
        if self.rtvi:
            await self.rtvi.send_server_message({"type": type, "session_id": self.id,
                                                "generation": generation, **payload})

    async def error(self, message: str, generation: int | None = None):
        session_logger(self, generation).warning("[交互异常] {}", message)
        await self.send("error", generation, message=message)

    async def applied(self, data: dict):
        command_id = data.get("command_id")
        entry = self.pending.get(command_id)
        if not entry or not self.current(data.get("generation")):
            return
        generation, future = entry
        if generation != self.generation or future.done():
            return
        ok = data.get("ok") is True
        if ok:
            self.photo_shown = True
            self.revision += 1
        else:
            session_logger(self, generation).warning("[场景] 照片加载失败，command_id={}", command_id)
        future.set_result(ok)
        await self.send("scene.snapshot", scene=self.scene())


def tag(frame: Frame, generation: int) -> Frame:
    frame.metadata["generation"] = generation
    return frame


class Scoped:
    """Keep the originating generation across the service's own async tasks."""
    def __init__(self, *args, state: Session, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    async def process_frame(self, frame, direction):
        generation = frame.metadata.get("generation", self.state.generation)
        if response_frame(frame) and not self.state.current(generation):
            self.state.dropped += 1
            return
        token = scope.set(generation)
        try:
            if isinstance(frame, LLMContextFrame):
                frame.context.get_messages()[0]["content"] = persona(self.state.scene())
            await super().process_frame(frame, direction)
        finally:
            scope.reset(token)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        generation = frame.metadata.get("generation", scope.get())
        context = self.state.audio.get(getattr(frame, "context_id", None))
        if context:
            generation = context[0]
        if generation is not None:
            tag(frame, generation)
            if response_frame(frame) and not self.state.current(generation):
                self.state.dropped += 1
                return
        await super().push_frame(frame, direction)


class ScopedRTVI(Scoped, RTVIProcessor):
    async def _handle_send_text(self, data):
        # This pinned SDK method interrupts and flushes before appending the text.
        if len(data.content) > 500:
            await self.state.error("文字最多 500 字。")
            return
        generation = self.state.generation
        started = time.perf_counter()
        token = scope.set(generation)
        try:
            session_logger(self.state, generation).info("[用户/文字] {}", data.content)
            await super()._handle_send_text(data)
            session_logger(self.state, generation).info("[耗时/文字] 已提交原生上下文 {:.0f}ms", (time.perf_counter() - started) * 1000)
        finally:
            scope.reset(token)


class Controls(FrameProcessor):
    def __init__(self, state: Session):
        super().__init__()
        self.state = state

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, RTVIClientMessageFrame):
            data = frame.data or {}
            try:
                if frame.type == "interaction.begin" or (
                    frame.type == "push_to_talk" and data.get("state") == "start"
                ):
                    data = {**data, "kind": "voice" if frame.type == "push_to_talk" else "text"}
                    if not self.state.begin(data):
                        return
                    await self.state.send("input.accepted", scene=self.state.scene())
                elif frame.type == "scene.applied":
                    await self.state.applied(data)
                    return
                elif frame.type == "interaction.cancel":
                    if self.state.begin({**data, "kind": "text"}):
                        await self.broadcast_interruption()
                        await self.state.send("input.accepted", scene=self.state.scene())
                    return
                elif frame.type == "push_to_talk" and not self.state.current(data.get("generation")):
                    return
            except ValueError:
                await self.state.error("输入内容无效，请重新试一次。")
                return
        generation = frame.metadata.get("generation", self.state.generation)
        if response_frame(frame) and not self.state.current(generation):
            return
        tag(frame, generation)
        await self.push_frame(frame, direction)

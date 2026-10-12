import asyncio

import pytest
from pipecat.frames.frames import LLMTextFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameProcessor
from app.interaction import Session, Scoped, scope, tag
from app.services import pcm
from app.story import REPLIES
from app.tools import SceneTools, Performance


@pytest.fixture
def state():
    return Session("test", "mock", "secret")


async def test_photo_ack_and_interruption_are_atomic(state):
    state.begin({"generation": 1})
    tools = SceneTools(state)
    request = asyncio.create_task(tools.photo(1))
    await asyncio.sleep(0)
    command = next(iter(state.pending))
    state.begin({"generation": 2})
    await state.applied({"generation": 1, "command_id": command, "ok": True})
    assert await request is False
    assert state.scene()["photo_shown"] is False


async def test_confirmed_photo_survives_and_duplicate_ack_is_ignored(state):
    state.begin({"generation": 1})
    request = asyncio.create_task(SceneTools(state).photo(1))
    await asyncio.sleep(0)
    command = next(iter(state.pending))
    data = {"generation": 1, "command_id": command, "ok": True}
    await state.applied(data)
    await state.applied(data)
    assert await request is True
    state.begin({"generation": 2})
    assert state.photo_shown and state.revision == 1
    assert SceneTools(state).close(2, "stay")


class Recorder(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.received = []

    async def push_frame(self, frame, direction=None):
        self.received.append(frame)


class Guard(Scoped, Recorder):
    pass


async def test_late_text_and_tts_are_bound_to_origin(state):
    guard = Guard(state=state)
    state.begin({"generation": 1})
    token = scope.set(1)
    state.audio["old"] = (1, "旧句子")
    state.begin({"generation": 2})
    await guard.push_frame(LLMTextFrame("旧回答"))
    scope.reset(token)
    await guard.push_frame(TTSAudioRawFrame(b"\0\0" * 10, 24000, 1, context_id="old"))
    await guard.push_frame(tag(LLMTextFrame("新回答"), 2))
    assert [frame.text for frame in guard.received] == ["新回答"]
    assert state.dropped == 2


def test_audio_covers_every_mock_reply_and_tools_are_whitelisted():
    for reply in REPLIES.values():
        audio = pcm(reply)
        assert len(audio) > 24000 and len(audio) % 2 == 0
    with pytest.raises(ValueError):
        Performance(emotion="unknown", action="none")
    with pytest.raises(ValueError):
        Performance(emotion="warm", url="https://untrusted.example")

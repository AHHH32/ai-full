from pipecat.frames.frames import (InterruptionFrame, TTSAudioRawFrame, TTSStartedFrame, BotStoppedSpeakingFrame, ErrorFrame, LLMContextFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, LLMAssistantAggregator, LLMUserAggregatorParams
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy
from pipecat.turns.user_stop.external_user_turn_stop_strategy import ExternalUserTurnStopStrategy
from pipecat.turns.types import ProcessFrameResult
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from .interaction import Scoped, ScopedRTVI, Controls, scope, tag
from .services import MockSTT, MockLLM, MockTTS, live_services
from .tools import SceneTools
from .story import persona
from .logs import log_responses, session_logger


class PushStart(BaseUserTurnStartStrategy):
    async def process_frame(self, frame):
        if isinstance(frame, RTVIClientMessageFrame) and frame.type == "push_to_talk" and frame.data.get("state") == "start":
            # Native turn strategies run in their own long-lived processor task.
            token = scope.set(frame.data["generation"])
            try:
                await self.trigger_reset_aggregation()
                await self.trigger_user_turn_started(enable_user_speaking_frames=False)
            finally:
                scope.reset(token)
            return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE


class PushStop(ExternalUserTurnStopStrategy):
    async def process_frame(self, frame):
        if isinstance(frame, RTVIClientMessageFrame) and frame.type == "push_to_talk":
            if frame.data.get("state") == "start":
                await self._handle_user_started_speaking(announced_elsewhere=False)
            else:
                await self._handle_user_stopped_speaking(announced_elsewhere=False)
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)


class User(Scoped, LLMUserAggregator):
    async def push_frame(self, frame, direction=None):
        if isinstance(frame, LLMContextFrame):
            tag(frame, self.state.input_generation if self.state.input_generation is not None else self.state.generation)
        if direction is None:
            await super().push_frame(frame)
        else:
            await super().push_frame(frame, direction)


def make_bot(state, peer):
    transport = SmallWebRTCTransport(peer, TransportParams(audio_in_enabled=True, audio_out_enabled=True))
    state.rtvi = ScopedRTVI(state=state)
    tools = SceneTools(state)
    if state.mode == "mock":
        stt, llm, tts = MockSTT(state=state), MockLLM(state=state, tools=tools), MockTTS(state=state, sample_rate=24000, reuse_context_id_within_turn=False)
        schemas = None
    else:
        stt, llm, tts = live_services(state)
        schemas = tools.register(llm)
    flush_dialogue = log_responses(llm, state)
    context = LLMContext([{"role": "system", "content": persona(state.scene())}])
    if schemas is not None:
        context.set_tools(schemas)
    user = User(context, state=state, params=LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(start=[PushStart()], stop=[PushStop(timeout=.3)])))
    output = transport.output()
    pipeline = Pipeline([transport.input(), state.rtvi, Controls(state), stt, user, llm, tts, output, LLMAssistantAggregator(context)])
    worker = PipelineWorker(pipeline, app_resources=state,
        observers=[state.rtvi.create_rtvi_observer()],
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000), idle_timeout_secs=300)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    state.runner = runner

    async def interrupted(processor, frame):
        if isinstance(frame, InterruptionFrame):
            generation = frame.metadata.get("generation", state.generation)
            key = (generation, frame.id)
            completed = state.interruptions.setdefault(key, set())
            completed.add(processor.name)
            if processor is output:
                state.output_context = None
            if len(completed) == 3:
                await state.send("interaction.interrupted", generation)
                state.interruptions = {key: completed}
    for processor in (llm, tts, output):
        processor.add_event_handler("on_after_process_frame", interrupted)

    async def audio_event(processor, frame):
        if isinstance(frame, TTSStartedFrame):
            state.output_context = frame.context_id
        elif isinstance(frame, TTSAudioRawFrame):
            # Pipecat's output resampler makes new audio chunks without context_id.
            # Native ordered TTSStarted markers identify the active context at playback.
            context_id = frame.context_id or state.output_context
            binding = state.audio.get(context_id)
            if binding and state.current(binding[0]) and state.spoken_context != context_id:
                state.spoken_context = context_id
                state.output_generation = binding[0]
                session_logger(state, binding[0]).info("[输出] 首段音频已写入 WebRTC，context_id={}", context_id)
                await state.send("subtitle.update", binding[0], text=binding[1])
                await state.send("response.state", binding[0], state="speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await state.send("response.state", state.output_generation, state="idle")
        elif isinstance(frame, ErrorFrame):
            generation = frame.metadata.get("generation", state.generation)
            source = frame.processor.name if frame.processor else "管线"
            session_logger(state, generation).opt(exception=frame.exception).error("[{}] {}", source, frame.error)
            await state.error("服务暂时不可用，请重新发送或重新连接。", generation)
    # The output pushes audio downstream only after write_audio_frame succeeded.
    output.add_event_handler("on_before_push_frame", audio_event)
    state.rtvi.add_event_handler("on_after_process_frame", audio_event)

    @transport.event_handler("on_client_disconnected")
    async def disconnected(*args):
        await runner.cancel()

    async def run():
        try:
            await runner.add_workers(worker)
            await runner.run()
        except Exception:
            session_logger(state).exception("[管线] 会话运行失败")
            raise
        finally:
            flush_dialogue("会话结束")
            state.closed = True
            state.pending.clear()
            state.audio.clear()
    return run()

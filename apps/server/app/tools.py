import asyncio
import secrets
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties

from .interaction import Session
from .logs import session_logger


class Performance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emotion: Literal["guarded", "curious", "warm"]
    action: Literal["none", "look_window", "hold_photo"] = "none"


class Ending(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["stay", "message"]


class SceneTools:
    def __init__(self, state: Session):
        self.state = state

    async def perform(self, generation: int, emotion: str, action: str = "none"):
        value = Performance(emotion=emotion, action=action)
        if self.state.current(generation):
            await self.state.send("scene.command", generation,
                                  payload={"event": "character", **value.model_dump()})

    async def photo(self, generation: int, broken: bool = False) -> bool:
        if not self.state.current(generation):
            return False
        if self.state.photo_shown:
            return True
        command_id = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self.state.pending[command_id] = (generation, future)
        try:
            await self.state.send("scene.command", generation, command_id=command_id,
                                  payload={"event": "photo.reveal", "emotion": "warm",
                                           "action": "hold_photo", "asset_id": "photo_memory_01",
                                           "broken": broken})
            async with asyncio.timeout(3):
                return bool(await future) and self.state.current(generation)
        except TimeoutError as error:
            session_logger(self.state, generation).opt(exception=error).error("[场景] 等待照片展示确认超时")
            await self.state.error("照片暂时没有加载出来，可以再问一次。", generation)
            return False
        finally:
            self.state.pending.pop(command_id, None)

    def close(self, generation: int, choice: str) -> bool:
        value = Ending(choice=choice)
        if not self.state.current(generation) or not self.state.photo_shown:
            return False
        self.state.ending_choice = value.choice
        self.state.revision += 1
        return True

    def register(self, llm):
        async def handler(params):
            generation, skip_followup = self.state.calls.pop(params.tool_call_id, (None, False))
            if not self.state.current(generation):
                return
            started = time.perf_counter()
            session_logger(self.state, generation).info("[工具] 开始 {}", params.function_name)
            try:
                args = dict(params.arguments)
                if params.function_name == "perform_character":
                    value = Performance.model_validate(args)
                    await self.perform(generation, value.emotion, value.action)
                    result = {"ok": True}
                elif params.function_name == "share_photo":
                    if args:
                        session_logger(self.state, generation).warning("[场景工具] share_photo 不接受参数")
                        await params.result_callback({"ok": False, "reason": "share_photo 不接受参数"})
                        return
                    result = {"ok": await self.photo(generation)}
                else:
                    result = {"ok": self.close(generation, Ending.model_validate(args).choice)}
                    await self.state.send("scene.snapshot", generation, scene=self.state.scene())
                if self.state.current(generation):
                    properties = FunctionCallResultProperties(run_llm=False) if skip_followup else None
                    await params.result_callback(result, properties=properties)
            except Exception:
                session_logger(self.state, generation).exception("[场景工具] {} 执行失败", params.function_name)
                raise
            finally:
                session_logger(self.state, generation).info("[耗时/工具] {} 结束 {:.0f}ms，再请求模型={}",
                    params.function_name, (time.perf_counter() - started) * 1000, not skip_followup)

        schemas = []
        for name, description, model in (
            ("perform_character", "需要变化时更新舞台表情或动作；不要在对白中描写这些动作。", Performance),
            ("share_photo", "对方想看旧照片时展示照片；仅在返回 ok=true 后才能说已展示，不输出动作旁白。", None),
            ("close_scene", "已分享照片，对方选择陪伴或发消息时记录剧情选择；不实际发送消息，不输出剧情旁白。", Ending),
        ):
            schema = model.model_json_schema() if model else {}
            schemas.append(FunctionSchema(name, description, schema.get("properties", {}),
                                          schema.get("required", [])))
            llm.register_function(name, handler, cancel_on_interruption=True, timeout_secs=4)
        return ToolsSchema(standard_tools=schemas)

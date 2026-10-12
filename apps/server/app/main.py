import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Header
from loguru import logger
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler, SmallWebRTCRequest, SmallWebRTCPatchRequest, IceCandidate

from .settings import WEB, live_available
from .interaction import Session
from .bot import make_bot
from .logs import configure_logging, session_logger

configure_logging()

sessions: dict[str, Session] = {}
created: dict[str, float] = {}
webrtc = SmallWebRTCRequestHandler(ice_servers=[])


@asynccontextmanager
async def lifespan(app):
    yield
    for state in sessions.values():
        if state.runner and not state.closed:
            await state.runner.cancel()
    await webrtc.close()
    await asyncio.gather(*(s.task for s in sessions.values() if s.task), return_exceptions=True)


app = FastAPI(title="雨夜咖啡馆", lifespan=lifespan)


@app.middleware("http")
async def log_http_errors(request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("[HTTP] {} {} 请求异常", request.method, request.url.path)
        raise
    if response.status_code >= 400:
        logger.warning("[HTTP] {} {} 返回 {}", request.method, request.url.path, response.status_code)
    return response


class Start(BaseModel):
    mode: Literal["mock", "live"] = "mock"


class Offer(BaseModel):
    sdp: str = Field(max_length=65536)
    type: Literal["offer"]
    pc_id: str | None = None
    restart_pc: bool | None = None
    requestData: dict | None = None


def session(id, key):
    state = sessions.get(id)
    if not state or not secrets.compare_digest(state.key, key or ""):
        raise HTTPException(404, "会话不存在")
    if state.closed or time.monotonic() - created[id] > 1800:
        raise HTTPException(410, "会话已结束，请重新连接")
    return state


@app.get("/api/health")
async def health():
    return {"ok": True, "live_available": live_available()}


@app.post("/api/start")
async def start(value: Start):
    for id in list(sessions):
        if sessions[id].closed or (sessions[id].peer is None and time.monotonic() - created[id] > 60):
            sessions.pop(id)
            created.pop(id)
    if len(sessions) >= 8:
        raise HTTPException(429, "会话已满，请稍后再试")
    if value.mode == "live" and not live_available():
        raise HTTPException(503, "请先配置 STT、LLM 和 TTS 密钥")
    state = Session(secrets.token_hex(12), value.mode, secrets.token_urlsafe(32))
    sessions[state.id] = state
    created[state.id] = time.monotonic()
    return {"session_id": state.id, "webrtc_request_params": {
        "endpoint": f"/api/sessions/{state.id}/offer", "headers": {"X-Session-Key": state.key}},
        "ice_config": {"iceServers": []}}


@app.post("/api/sessions/{id}/offer")
async def offer(id: str, value: Offer, x_session_key: str | None = Header(default=None)):
    state = session(id, x_session_key)
    if value.pc_id and (state.peer is None or value.pc_id != state.peer.pc_id):
        raise HTTPException(400, "连接无效")
    if state.peer is not None and value.pc_id != state.peer.pc_id:
        raise HTTPException(409, "会话已连接")
    failures = []
    async def connected(peer):
        state.peer = peer
        try:
            state.task = asyncio.create_task(make_bot(state, peer), name=f"cafe-{id}")
        except Exception:
            session_logger(state).exception("[WebRTC] 会话初始化失败")
            state.closed = True
            failures.append(True)
            await peer.disconnect()
    answer = await webrtc.handle_web_request(SmallWebRTCRequest.from_dict(value.model_dump(exclude_none=True)), connected)
    if failures:
        raise HTTPException(503, "会话初始化失败，请重新连接")
    return answer


@app.patch("/api/sessions/{id}/offer")
async def patch(id: str, value: dict, x_session_key: str | None = Header(default=None)):
    state = session(id, x_session_key)
    if not state.peer or value.get("pc_id") != state.peer.pc_id:
        raise HTTPException(400, "连接无效")
    try:
        request = SmallWebRTCPatchRequest(value["pc_id"], [IceCandidate(**c) for c in value["candidates"]])
        await webrtc.handle_patch_request(request)
    except (TypeError, KeyError):
        raise HTTPException(400, "连接参数无效")
    return {"ok": True}


if (WEB / "dist").exists():
    app.mount("/", StaticFiles(directory=WEB / "dist", html=True), name="web")

"""复用 Pipecat 的 Loguru：控制台输出、密钥遮盖与完整对白日志。"""
import os
import sys

from loguru import logger
from pipecat.frames.frames import (CancelFrame, EndFrame, ErrorFrame, InterruptionFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame)


def write_console(message):
    text = str(message)
    for name in ("VOLCENGINE_API_KEY", "OPENAI_API_KEY"):
        key = os.getenv(name)
        if key:
            text = text.replace(key, "***")
    sys.stderr.write(text)
    sys.stderr.flush()


def configure_logging():
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    logger.remove()
    logger.configure(extra={"session": "-", "generation": "-"})
    # 不输出堆栈局部变量，避免请求头、密钥和原始音频进入控制台。
    logger.add(write_console, level=os.getenv("LOGURU_LEVEL", "INFO"),
        format="{time:HH:mm:ss.SSS} | {level:<7} | 会话={extra[session]} 轮次={extra[generation]} | {message}",
        backtrace=False, diagnose=False)


def session_logger(state, generation=None):
    return logger.bind(session=state.id, generation=state.generation if generation is None else generation)


def log_responses(llm, state):
    """在模型输出处合并文本，不改变帧或等待整段回复后才播放。"""
    parts = []
    generation = None
    status = "完成"

    def flush(status="完成"):
        text = "".join(parts).strip()
        if text:
            session_logger(state, generation).info("[角色回复/{}] {}", status, text)
        parts.clear()

    async def output(processor, frame):
        nonlocal generation, status
        if isinstance(frame, LLMFullResponseStartFrame):
            flush("未完成")
            generation = frame.metadata.get("generation", state.generation)
            status = "完成"
        elif isinstance(frame, LLMTextFrame):
            parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            flush(status)
        elif isinstance(frame, ErrorFrame):
            status = "异常结束"
        elif isinstance(frame, InterruptionFrame):
            flush("已打断")
        elif isinstance(frame, (CancelFrame, EndFrame)):
            flush("会话结束")

    llm.add_event_handler("on_before_push_frame", output)
    return flush

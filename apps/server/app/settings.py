"""配置入口：定位项目资源并读取服务端环境变量，不在前端暴露密钥。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 从此文件的位置定位仓库根目录，避免从不同工作目录启动时读错 .env。
ROOT = Path(__file__).resolve().parents[3]
# override 默认为 False：已有系统环境变量优先，.env 补充尚未定义的变量。
# .env 不存在时继续运行，支持无需密钥的 Mock 体验；配置变更需要重启进程。
load_dotenv(ROOT / ".env")
# WEB/dist 用于生产页面；WEB/public/assets 用于后端读取 Mock 音频源文件。
WEB = ROOT / "apps/web"
ASSETS = WEB / "public/assets"
# 火山语音 Key 用于 ASR / TTS；LLM 使用 OpenAI 兼容适配，OPENAI_API_KEY 可填方舟 Key。
# 音色和资源 ID 有默认值；仅检查密钥非空，不发起联网验证。
LIVE_KEYS = ("VOLCENGINE_API_KEY", "OPENAI_API_KEY")


def live_available() -> bool:
    """供健康接口和启动接口判断是否已填写全部 Live 必填项。"""
    return all(os.getenv(key) for key in LIVE_KEYS)

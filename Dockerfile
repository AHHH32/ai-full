# 第一阶段：使用 Node 构建前端；Node 与 node_modules 不进入最终 Python 镜像。
FROM node:24-slim AS web
WORKDIR /app
# 先复制依赖清单与项目 npm 镜像配置，源码改动可复用依赖安装缓存。
COPY package.json package-lock.json .npmrc ./
COPY apps/web/package.json apps/web/package.json
# 按锁文件安装，确保构建依赖版本与本地 npm ci 一致。
RUN npm ci
COPY apps/web apps/web
# 先进行 TypeScript 类型检查，再由 Vite 生成 apps/web/dist。
RUN npm run build

# 第二阶段：Python 3.12 运行环境，与 .python-version 和后端版本范围保持一致。
FROM python:3.12-slim
# 固定 uv 工具版本，使用根目录 uv workspace 管理依赖。
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
WORKDIR /app
# 先安装依赖再复制应用代码，减少只改业务代码时的重复下载。
COPY pyproject.toml uv.lock .python-version ./
COPY apps/server/pyproject.toml apps/server/pyproject.toml
# --locked 校验现有锁文件；--no-dev 排除 pytest 等测试依赖。
# 默认安装到 /app/.venv，下载镜像来自根目录 pyproject.toml。
RUN uv sync --all-packages --locked --no-dev
COPY apps/server/app apps/server/app
# dist 提供浏览器页面；public/assets 供后端读取 Mock WAV，两个路径均需保留。
COPY --from=web /app/apps/web/dist apps/web/dist
COPY --from=web /app/apps/web/public/assets apps/web/public/assets
# 优先使用虚拟环境 Python；UTF-8 处理中文；INFO 避免逐帧 DEBUG 日志。
ENV PATH="/app/.venv/bin:$PATH" PYTHONUTF8=1 LOGURU_LEVEL=INFO
# 声明 HTTP / 信令端口，不会自动开放 WebRTC UDP 端口或配置宿主机网络。
EXPOSE 7860
# 容器内监听所有网卡；应用由 apps/server 导入，关闭 HTTP 访问日志。
# WebRTC 网络连接由 compose.yaml 的 host 网络配置处理。
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "apps/server", "--host", "0.0.0.0", "--port", "7860", "--no-access-log"]

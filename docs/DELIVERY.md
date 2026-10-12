# 交付检查

检查日期：2026-10-12（Asia/Shanghai）。交付形式为当前源码仓库、本地启动说明，以及便于传递的源码压缩包。Git 提交与远程推送由作者后续执行。

## 交付项对应

| 要求 | 交付内容 |
| --- | --- |
| 源代码仓库 | 根目录的前后端源码、测试、素材、两个依赖锁文件和配置模板；可另交源码 ZIP |
| 本地启动说明 | README 第 1 节，前置环境、入口、排查与分步执行 |
| 一条命令启动 | 根目录 `npm start`，自动准备依赖、构建、启动并检查健康接口 |
| README | 架构与模块、语音与打断、角色和场景、模型和媒体、取舍、已知问题、约 30 小时投入、两周演进 |
| AI_USAGE.md | 工具与参与范围、人工反馈及验证边界、一个 TTS 协议错误与修正例子 |

## 本轮验证记录

执行环境：Windows、Node.js 24.15.0、npm 11.12.1、uv 0.11.8、Python 3.12.14，复用已安装 Chrome。默认演示与浏览器测试均使用 Mock，本轮没有调用真实计费模型或语音服务。

| 检查 | 结果 |
| --- | --- |
| `npm test` | 34 项通过；协议、取消隔离、工具、照片确认、API、日志与超时回归 |
| `npm run build` | TypeScript 检查和 Vite 生产构建通过 |
| `npm start` | 一键启动通过，依赖已有缓存时约 10–15 秒；健康接口和首页 HTTP 200 |
| 源码包解压后 `npm start` | 无 `.env`、`node_modules`、`.venv`、项目下载缓存；自动安装 112 个 npm 包、78 个 Python 包并构建启动成功；健康接口 `ok=true`、`live_available=false`，首页 HTTP 200 |
| 启动失败分支 | uv 缺失和 7860 占用均有明确提示，退出码为 1 |
| `Ctrl+C` | 停止启动的进程并释放 7860 端口 |
| `npm run test:e2e` | 2 项通过（23.6 秒）；实际 WebRTC 音频、连续输入、打断、模拟 PTT、照片 / 暖光、故障恢复、重连和手机尺寸布局 |
| 脚本语法 | Node 启动脚本语法与 Python 打包脚本编译检查通过 |
| Git 补丁格式 | `git diff --check` 通过 |

Python 测试有 2 条 Pipecat 关于 system prompt 配置方式的弃用提示，当前固定版本测试通过。Vite 提示主 JavaScript 包超过 500 kB（约 952 kB、gzip 约 285 kB），尚未做按需拆包。它们属于后续维护事项，不是构建失败。

Codex 的 Windows 沙箱曾导致 Vite realpath 的 EPERM，以及本机网络访问限制；允许本机执行后上述检查通过。这是本轮工具环境限制，普通终端启动不需要额外的项目授权步骤。

解压目录检查使用已安装的 Node / uv / Python 前置工具，Python 为 3.12.13；项目依赖重新下载，npm 安装约 11 秒，Python 下载准备约 17 秒、安装约 1.5 秒，前端构建约 6 秒。未单独计时工具安装或 Python 解释器下载，不将本机结果作为其他网络的 15 分钟保证。项目满足“一条命令启动”路径。Docker、真人设备、真实 ASR / TTS、公网及弱网部署未在本轮验收。人工反馈与自动测试分别记录于 AI_USAGE.md。

## 源码包与后续提交

在具备 Git 的源码工作区根目录执行 `npm run package:submission`，脚本按 Git 的已跟踪与未忽略文件列表读取**当前工作区内容**，包括本轮修改，生成：

- `output/submission/rainy-cafe-source.zip`
- `output/submission/rainy-cafe-source.sha256`

包内顶层目录为 `rainy-cafe/`；解压后进入该目录执行 `npm start`，启动不依赖 Git。打包检查必需清单、28 个 Mock WAV、路径边界与 ZIP 完整性；保留 `.env.example`、`.npmrc` 等启动所需文件。本轮另外逐文件比对 ZIP 内容与当前工作区，核对 SHA-256，并完成上述解压启动检查。实际 `.env`、其他私有环境文件、`.git`、依赖、缓存、构建和测试输出均排除。压缩包只包含源码，不包含 Git 历史；可选重新打包命令需要带 `.git` 的工作区。

建议提交前查看 `git status --short` 与 `git diff`，将交付文档、启动脚本、打包脚本和 package.json 的变更加入你自己的提交。不要用整个工作区目录直接压缩，避免夹带密钥和本机依赖；已有源码 ZIP 位于被 Git 忽略的 `output/` 中。

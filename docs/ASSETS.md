# 素材来源

| 素材 | 来源与用途 | 版权说明 |
| --- | --- | --- |
| Mira 角色精灵图集：66 帧 / 三种表情 / 四种状态 / 两种动作 | 本项目 `scripts/generate_assets.py` 生成的原创 SVG；26 岁、琥珀色外套、银色星形发夹 | 项目原创，无下载角色素材、无第三方角色 IP |
| 雨夜咖啡馆背景与海边旧照片 | 同一脚本生成的原创 SVG | 项目原创 |
| Mock 中文 WAV | 本项目原创对白；通过 Windows SAPI / Microsoft Huihui 中文系统语音离线生成，随项目提供，运行时无需调用系统语音 | 系统语音不是本项目原创声音，不能声明 CC0 或无条件商用许可；用于本地面试演示，其他分发用途应另核 Microsoft 许可或替换为授权录音 |
| UI 字体 | 操作系统已有字体：Georgia、苹方、微软雅黑等 | 未随项目分发字体文件 |

资源没有引用外部图片、音乐或视频。语音共 28 个句子文件，文件名来自规范化文本 SHA-256 的前 16 位。

重新生成图像：`uv run --all-packages python scripts/generate_assets.py`。

Windows 重新生成音频：先运行 `uv run --all-packages python scripts/prepare_audio.py`，再运行 `powershell -ExecutionPolicy Bypass -File scripts/generate_audio.ps1`。脚本只在当前进程使用系统已有的 OneCore Huihui 语音，不修改注册表，不修改全局执行策略，不通过扬声器播放。

Pipecat 与其客户端采用 BSD-2-Clause；PixiJS、React、FastAPI 等依赖按各自包许可使用。PTT 策略参考 [Pipecat 官方示例](https://github.com/pipecat-ai/pipecat/tree/main/examples/turn-management)，原有版权声明见 THIRD_PARTY_NOTICES.md。

import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';

// CHROME_PATH 优先；Windows 默认复用已安装的 Chrome，避免额外下载浏览器。
// 路径不存在时使用 Playwright 管理的浏览器，需要先运行 npx playwright install chromium。
const chrome = process.env.CHROME_PATH ?? (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
export default defineConfig({
  // 收集仓库根目录的端到端测试；Python 测试由 pytest 收集。
  testDir: './tests',
  // 单个测试总时限 45 秒，包括建立 WebRTC、播放和恢复流程。
  timeout: 45000,
  // 串行运行，降低音视频处理和浏览器渲染的资源竞争。
  workers: 1,
  // 单条 expect 最多等待 15 秒，覆盖异步语音状态和资源加载。
  expect: { timeout: 15000 },
  use: {
    // 测试前先运行 npm start；配置不会自动启动服务。改变服务端口时同步修改。
    baseURL: 'http://127.0.0.1:7860',
    // 默认桌面视口；移动布局测试在用例中另行设置 390 × 844。
    viewport: { width: 1280, height: 900 },
    launchOptions: {
      // 只有存在的自定义 / 系统 Chrome 路径才覆盖 Playwright 的默认浏览器。
      ...(chrome && existsSync(chrome) ? { executablePath: chrome } : {}),
      args: [
        // 自动批准测试媒体权限，避免无头浏览器等待交互式权限弹窗。
        '--use-fake-ui-for-media-stream',
        // 使用虚拟媒体设备，测试不读取真实麦克风或摄像头。
        '--use-fake-device-for-media-stream',
        // 允许无头环境用软件 WebGL 渲染 PixiJS 舞台；仅用于自动测试进程。
        '--enable-unsafe-swiftshader',
      ],
    },
  },
});

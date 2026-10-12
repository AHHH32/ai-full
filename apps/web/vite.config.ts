import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // 提供 React JSX 编译与开发时的组件热更新。
  plugins: [react()],
  server: {
    proxy: {
      // 仅用于 npm run dev：将 API、WebRTC offer 和 ICE PATCH 请求转发给 Python。
      // 保留 /api 前缀，后端需另行启动；改变后端端口时同步修改这里和测试 baseURL。
      // 此代理处理 HTTP 信令，WebRTC 音频通过浏览器与后端的 peer connection 传输。
      '/api': 'http://127.0.0.1:7860',
    },
  },
  // 生产构建默认写入 apps/web/dist，再由 FastAPI 提供页面，不使用开发代理。
});

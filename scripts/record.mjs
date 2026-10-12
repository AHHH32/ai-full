import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

process.env.PLAYWRIGHT_BROWSERS_PATH ??= resolve('.cache/ms-playwright');
const { chromium } = await import('@playwright/test');
const chrome = process.env.CHROME_PATH ?? (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
mkdirSync('output/raw', { recursive: true });
const browser = await chromium.launch({
  ...(chrome && existsSync(chrome) ? { executablePath: chrome } : {}),
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--enable-unsafe-swiftshader'],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 900 },
  recordVideo: { dir: 'output/raw', size: { width: 1280, height: 900 } } });
const page = await context.newPage();
const started = Date.now();
const actions = [];
async function at(seconds, description, action) {
  await page.waitForTimeout(Math.max(0, seconds * 1000 - (Date.now() - started)));
  console.log(`${Math.round((Date.now() - started) / 1000)}s ${description}`);
  actions.push(description);
  await action();
}
async function send(text) {
  await page.getByRole('textbox', { name: '说点什么' }).fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
}
try {
  await page.goto('http://127.0.0.1:7860');
  await at(3, '进入 Mock（真实 WebRTC 播放，转写为模拟）', async () => {
    await page.getByRole('button', { name: '坐下来聊聊' }).click();
    await page.getByText('已连接', { exact: true }).waitFor();
  });
  await page.waitForFunction(() => document.querySelector('audio')?.srcObject instanceof MediaStream);
  const audioOffset = (Date.now() - started) / 1000;
  await page.evaluate(() => {
    const stream = document.querySelector('audio').srcObject;
    const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
    const chunks = [];
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    window.demoRecorder = { recorder, chunks };
    recorder.start(1000);
  });
  await at(10, '文字问候，观察好奇表情和说话动画', () => send('你好，米拉。'));
  await at(31, '询问等待的朋友，观察看窗动作', () => send('你在等谁？'));
  await at(52, '重新询问，在语音播放中准备打断', () => send('你在等谁？'));
  await at(55, '按住说话打断，明确显示模拟转写', async () => {
    const ptt = page.getByRole('button', { name: '按住说话', exact: true });
    await ptt.hover();
    await page.mouse.down();
    await page.waitForTimeout(1500);
    await page.mouse.up();
  });
  await at(77, '询问照片，触发举照片与暖光', () => send('可以看看那张照片吗？'));
  await at(101, '陪伴收束，确认照片在后续对话中保留', () => send('我陪你再等一会儿。'));
  await at(121, '注入下一轮 TTS 失败，展示错误和文字降级', async () => {
    await page.getByRole('button', { name: '调试', exact: true }).click();
    await page.locator('.debug select').selectOption('tts_failure');
    await send('这里的咖啡怎么样？');
  });
  await at(130, '重新发送，恢复语音', () => send('这里的咖啡怎么样？'));
  await at(151, '模拟旧结果延迟返回，连续输入打断', async () => {
    await page.locator('.debug select').selectOption('old_result_late');
    await send('你在等谁？');
    await page.waitForTimeout(400);
    await send('你可以给朋友发条消息。');
  });
  await at(172, '展示移动布局与另一种剧情收束', async () => {
    await page.getByRole('button', { name: '调试', exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'output/demo-mobile.png', fullPage: true });
  });
  await at(185, '完成演示', async () => {});
  const audio = await page.evaluate(async () => {
    const { recorder, chunks } = window.demoRecorder;
    await new Promise(resolve => { recorder.onstop = resolve; recorder.stop(); });
    const data = new Uint8Array(await new Blob(chunks, { type: 'audio/webm' }).arrayBuffer());
    let binary = '';
    for (const byte of data) binary += String.fromCharCode(byte);
    return btoa(binary);
  });
  writeFileSync('output/raw/audio.webm', Buffer.from(audio, 'base64'));
  await context.close();
  const video = await page.video().path();
  writeFileSync('output/demo.json', JSON.stringify({ mode: 'mock', duration_seconds: (Date.now() - started) / 1000, audio_offset_seconds: audioOffset, actions }, null, 2));
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  const result = spawnSync(python, ['scripts/mux_demo.py', video, 'output/raw/audio.webm', String(audioOffset), 'output/demo.webm'], { stdio: 'inherit' });
  if (result.status !== 0) throw new Error('录屏音视频合并失败');
  console.log('演示录屏：output/demo.webm');
} finally { await browser.close(); }

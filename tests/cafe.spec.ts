import { test, expect } from '@playwright/test';

test('native WebRTC: text, interruption, scene confirmation, PTT and recovery', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button', { name: '坐下来聊聊' }).click();
  await expect(page.getByText('已连接', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '你在等谁？', exact: true }).click();
  await expect(page.getByTestId('subtitle')).toContainText('等一个很久没见的朋友');
  await expect(page.locator('.stage')).toHaveAttribute('data-phase', 'speaking');
  await page.getByRole('button', { name: '可以看看那张照片吗？', exact: true }).click();
  await expect(page.getByAltText('米拉和朋友在海边的旧照片')).toBeVisible();
  await expect(page.getByTestId('subtitle')).toContainText('第一次去海边');
  await expect(page.locator('.stage')).toHaveClass(/warm/);
  await page.getByRole('button', { name: '我陪你再等一会儿。', exact: true }).click();
  await expect(page.locator('.ending')).toContainText('等待有了陪伴');
  await expect(page.getByTestId('subtitle')).toContainText('再坐一会儿');
  // Local audio consumes packets throughout interruption and is unmuted for the new turn.
  await expect(page.getByTestId('bot-audio')).toHaveJSProperty('muted', false);
  await expect(page.getByTestId('bot-audio')).toHaveJSProperty('paused', false);
  expect(await page.evaluate(async () => {
    const element = document.querySelector('audio')!;
    const context = new AudioContext();
    await context.resume();
    const analyser = context.createAnalyser();
    context.createMediaStreamSource(element.srcObject as MediaStream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    let peak = 0;
    for (let i = 0; i < 20; i++) {
      analyser.getFloatTimeDomainData(samples);
      peak = Math.max(peak, ...samples.map(Math.abs));
      await new Promise(r => setTimeout(r, 50));
    }
    await context.close();
    return peak;
  })).toBeGreaterThan(.005);
  await page.getByRole('button', { name: '调试', exact: true }).click();
  await page.locator('.debug select').selectOption('old_result_late');
  await page.getByRole('button', { name: '你在等谁？', exact: true }).click();
  await page.waitForTimeout(350);
  await page.getByRole('button', { name: '我陪你再等一会儿。', exact: true }).click();
  await page.waitForTimeout(2500);
  await expect(page.getByTestId('subtitle')).not.toContainText('旧回答');
  await page.locator('.mock-select select').selectOption('你可以给朋友发条消息。');
  const ptt = page.getByRole('button', { name: '按住说话', exact: true });
  await ptt.dispatchEvent('pointerdown', { pointerId: 1 });
  await expect(page.locator('.stage')).toHaveAttribute('data-phase', 'listening');
  await ptt.dispatchEvent('pointerup', { pointerId: 1 });
  await expect(page.locator('.heard')).toContainText('你可以给朋友发条消息');
  await expect(page.getByTestId('subtitle')).toContainText('先发一条消息');
  await expect(page.getByTestId('bot-audio')).toHaveJSProperty('muted', false);
  await page.waitForTimeout(5500);
  await expect(page.getByText('已连接', { exact: true })).toBeVisible();
  await page.locator('.debug select').selectOption('model_timeout');
  await page.getByRole('button', { name: '你在等谁？', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('模型响应超时');
  await page.getByRole('button', { name: '你在等谁？', exact: true }).click();
  await expect(page.getByTestId('subtitle')).toContainText('等一个很久没见的朋友');
  await page.locator('.debug select').selectOption('tts_failure');
  await page.getByRole('button', { name: '我陪你再等一会儿。', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('语音合成');
  await page.getByRole('button', { name: '我陪你再等一会儿。', exact: true }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-phase', 'speaking');
  await expect(page.getByTestId('bot-audio')).toHaveJSProperty('muted', false);
  await page.screenshot({ path: 'test-results/cafe-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '离开座位', exact: true }).click();
  await page.getByRole('button', { name: '坐下来聊聊' }).click();
  await expect(page.getByText('已连接', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('mobile and photo loading failure recover without confirming a false scene', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '坐下来聊聊' }).click();
  await expect(page.getByText('已连接', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '调试', exact: true }).click();
  await page.locator('.debug select').selectOption('asset_failure');
  await page.getByRole('button', { name: '可以看看那张照片吗？', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('照片暂时');
  await expect(page.getByAltText('米拉和朋友在海边的旧照片')).toHaveCount(0);
  await page.getByRole('button', { name: '可以看看那张照片吗？', exact: true }).click();
  await expect(page.getByAltText('米拉和朋友在海边的旧照片')).toBeVisible();
  await page.getByRole('button', { name: '调试', exact: true }).click();
  await page.screenshot({ path: 'test-results/cafe-mobile.png', fullPage: true });
});

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const windows = process.platform === 'win32';
const python = resolve('.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const env = { ...process.env, UV_CACHE_DIR: resolve('.cache/uv'), npm_config_cache: resolve('.cache/npm'), PYTHONUTF8: '1', LOGURU_LEVEL: process.env.LOGURU_LEVEL ?? 'INFO' };
const address = 'http://127.0.0.1:7860';
let activeChild;
let interrupted = false;

// npm's hidden lockfile describes installed packages. Check it against the
// committed lockfile so a partial install or dependency change gets repaired.
function nodeDependenciesReady() {
  try {
    const wanted = JSON.parse(readFileSync('package-lock.json', 'utf8')).packages;
    const installed = JSON.parse(readFileSync('node_modules/.package-lock.json', 'utf8')).packages;
    for (const [name, dependency] of Object.entries(wanted)) {
      if (!name.startsWith('node_modules/')) continue;
      const actual = installed[name];
      // npm omits optional dependencies that do not support this OS / CPU.
      if (!actual && dependency.optional) continue;
      if (!actual || !existsSync(name) || ['version', 'resolved', 'integrity', 'link'].some(key => dependency[key] !== actual[key])) return false;
    }
    return ['vite', 'tsc'].every(name => existsSync(resolve('node_modules/.bin', `${name}${windows ? '.cmd' : ''}`)));
  } catch {
    return false;
  }
}

function run(command, args, { quiet = false, server = false } = {}) {
  return new Promise((ok, fail) => {
    if (interrupted) return fail(new Error('启动已取消'));
    // npm start provides its CLI path. Running that CLI directly also handles
    // Windows paths containing spaces without the deprecated shell:true mode.
    const npmCli = command === 'npm' && process.env.npm_execpath;
    const executable = npmCli ? process.execPath : windows && command === 'npm' ? process.env.ComSpec ?? 'cmd.exe' : command;
    const commandArgs = npmCli ? [npmCli, ...args] : windows && command === 'npm' ? ['/d', '/s', '/c', 'npm', ...args] : args;
    const child = spawn(executable, commandArgs, { cwd: root, env, stdio: quiet ? 'ignore' : 'inherit' });
    activeChild = child;
    child.once('error', error => {
      if (activeChild === child) activeChild = undefined;
      if (error.code === 'ENOENT' && command === 'uv') {
        fail(new Error('未找到 uv。请先按 README 安装 uv，重新打开终端，然后运行 npm start。'));
      } else {
        fail(error);
      }
    });
    child.once('exit', (code, signal) => {
      if (activeChild === child) activeChild = undefined;
      code === 0 ? ok() : fail(new Error(`${command} 退出：${signal ?? code}`));
    });
    if (server) void announceReady(child);
  });
}

function checkPort() {
  return new Promise((ok, fail) => {
    const probe = createServer();
    probe.once('error', error => fail(new Error(error.code === 'EADDRINUSE'
      ? '7860 端口已被占用。请停止之前启动的服务，再运行 npm start。'
      : `无法监听本地 7860 端口：${error.message}`)));
    probe.listen(7860, '127.0.0.1', () => probe.close(ok));
  });
}

async function announceReady(child) {
  while (activeChild === child && !interrupted) {
    try {
      const response = await fetch(`${address}/api/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok && activeChild === child && !interrupted) {
        console.log(`\n雨夜咖啡馆已启动：${address}\n选择 Mock 即可体验，无需配置密钥。按 Ctrl+C 停止。\n`);
        return;
      }
    } catch {
      // Importing media dependencies can take a few seconds on first launch.
    }
    if (activeChild !== child || interrupted) return;
    await new Promise(ok => setTimeout(ok, 500));
  }
}

function stop(signal) {
  interrupted = true;
  process.exitCode = signal === 'SIGINT' ? 130 : 143;
  if (!activeChild?.pid) return;
  if (windows) {
    // npm runs through cmd.exe on Windows; also stop its Node child process.
    const killer = spawn('taskkill', ['/PID', String(activeChild.pid), '/T', '/F'], { stdio: 'ignore' });
    killer.once('error', () => activeChild?.kill());
  } else {
    activeChild.kill(signal);
  }
}
process.once('SIGINT', () => stop('SIGINT'));
process.once('SIGTERM', () => stop('SIGTERM'));

try {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 12)) throw new Error('需要 Node.js 22.12 或更新版本。安装后重新打开终端，再运行 npm start。');
  await run('uv', ['--version'], { quiet: true });
  await checkPort();
  mkdirSync('.cache', { recursive: true });
  console.log('[1/4] 检查前端依赖（首次启动自动执行 npm ci）');
  if (!nodeDependenciesReady()) await run('npm', ['ci']);
  console.log('[2/4] 安装 Python 3.12 运行环境与锁定依赖');
  await run('uv', ['sync', '--all-packages', '--locked']);
  console.log('[3/4] 检查 TypeScript 并构建前端');
  await run('npm', ['run', 'build']);
  console.log('[4/4] 启动本地服务，等待健康检查通过');
  await run(python, ['-m', 'uvicorn', 'app.main:app', '--app-dir', 'apps/server', '--host', '127.0.0.1', '--port', '7860', '--no-access-log'], { server: true });
} catch (error) {
  if (!interrupted) {
    console.error(`\n启动失败：${error.message}\n请根据上方日志处理后重新运行 npm start。首次启动需要网络下载依赖。`);
    process.exitCode = 1;
  }
}

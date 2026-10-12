import { spawnSync } from 'node:child_process';
const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
const result = spawnSync(python, ['-m', 'pytest', '-q'], { stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1' } });
process.exit(result.status ?? 1);

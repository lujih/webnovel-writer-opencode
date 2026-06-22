/**
 * Python 检测与依赖安装模块
 *
 * 零依赖，纯 Node 22 标准库
 */

import { spawn } from 'node:child_process';

/**
 * 检测可用的 Python 3.10+
 * @returns {Promise<string|null>} 可用的 python 命令，或 null
 */
export async function detectPython() {
  const candidates = process.platform === 'win32'
    ? ['py', 'python3', 'python']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    try {
      await checkPython(cmd);
      return cmd;
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * 检查某个 python 命令是否可用且版本 ≥ 3.10
 */
function checkPython(cmd) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, ['--version'], {
      timeout: 5000,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let output = '';
    child.stdout?.on('data', (d) => { output += d.toString(); });
    child.stderr?.on('data', (d) => { output += d.toString(); });

    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`exit ${code}`));
        return;
      }
      const match = output.match(/Python\s*(\d+)\.(\d+)/i);
      if (match) {
        const major = parseInt(match[1], 10);
        const minor = parseInt(match[2], 10);
        if (major >= 3 && minor >= 10) {
          resolve({ major, minor, full: match[0] });
        } else {
          reject(new Error(`Python ${major}.${minor} < 3.10`));
        }
      } else {
        reject(new Error('cannot parse version'));
      }
    });

    child.on('error', reject);
  });
}

/**
 * pip install -r requirements.txt
 * @param {string} python - python 命令
 * @param {string} reqFile - requirements.txt 路径
 * @param {object} options
 * @returns {Promise<boolean>}
 */
export async function pipInstall(python, reqFile, options = {}) {
  if (!existsSync(reqFile)) {
    warn(`依赖文件不存在: ${reqFile}`);
    return false;
  }

  const args = ['-m', 'pip', 'install', '-r', reqFile];
  if (options.quiet) args.push('--quiet');

  return new Promise((resolve) => {
    const spinner = options.silent ? null : createSpinner('安装 Python 依赖...');
    spinner?.start();

    const child = spawn(python, args, {
      stdio: options.verbose ? 'inherit' : 'pipe',
    });

    let stderr = '';

    child.stderr?.on('data', (d) => {
      stderr += d.toString();
    });

    child.on('close', (code) => {
      if (code === 0) {
        spinner?.stop('Python 依赖安装完成');
        resolve(true);
      } else {
        spinner?.fail('pip install 失败');
        if (!options.verbose && stderr) {
          const lines = stderr.split('\n').filter(l => l.trim());
          // 只显示最后几行错误
          const tail = lines.slice(-5);
          for (const line of tail) {
            info(`  ${line.trim()}`);
          }
        }
        resolve(false);
      }
    });

    child.on('error', (e) => {
      spinner?.fail(`无法启动 pip: ${e.message}`);
      resolve(false);
    });
  });
}


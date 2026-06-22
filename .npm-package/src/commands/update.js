/**
 * update 命令 — 更新 .opencode/ 到最新版本
 */

import { existsSync, unlinkSync, createWriteStream, mkdirSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { get as httpsGet } from 'node:https';
import { get as httpGet } from 'node:http';
import { spawn } from 'node:child_process';

import { step, stepOk, info, warn, createSpinner, confirm, success } from '../core/ui.js';
import { extractTarGz } from '../core/extract.js';
import { detectPython } from '../core/python.js';

const __pkgRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))));

function getInstallerVersion() {
  try {
    const mf = join(__pkgRoot, 'package.json');
    if (existsSync(mf)) {
      const { version } = JSON.parse(readFileSync(mf, 'utf-8'));
      if (version) return version;
    }
  } catch {}
  return '0.0.0';
}

const REPO = 'lujih/webnovel-writer-opencode';
const BRANCH = 'master';
const GITHUB_TARBALL = `https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz`;
const MIRRORS = ['https://ghproxy.com/', 'https://mirror.ghproxy.com/'];
const PREFIX = `${REPO.split('/')[1]}-${BRANCH}/.opencode`;

// 可信域名白名单
const TRUSTED_HOSTS = [
  'github.com', 'objects.githubusercontent.com', 'codeload.github.com',
  'ghproxy.com', 'mirror.ghproxy.com',
];

function isTrustedHost(url) {
  try {
    const host = new URL(url).hostname;
    return TRUSTED_HOSTS.some(h => host === h || host.endsWith('.' + h));
  } catch { return false; }
}

async function downloadFile(url, destPath, redirectCount = 0) {
  if (redirectCount > 5) throw new Error('重定向次数过多');
  if (!isTrustedHost(url)) throw new Error(`不受信任的下载地址: ${new URL(url).hostname}`);
  mkdirSync(dirname(destPath), { recursive: true });

  return new Promise((resolve, reject) => {
    const file = createWriteStream(destPath);
    file.on('error', (err) => { file.close(); try { unlinkSync(destPath); } catch {} reject(err); });

    const getFn = url.startsWith('https') ? httpsGet : httpGet;
    getFn(url, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        file.close();
        try { unlinkSync(destPath); } catch {}
        return downloadFile(response.headers.location, destPath, redirectCount + 1).then(resolve).catch(reject);
      }
      if (response.statusCode !== 200) {
        file.close();
        try { unlinkSync(destPath); } catch {}
        return reject(new Error(`HTTP ${response.statusCode}`));
      }
      const totalSize = parseInt(response.headers['content-length'], 10) || 0;
      let downloaded = 0;
      response.on('data', (chunk) => { downloaded += chunk.length; file.write(chunk); });
      response.on('end', () => {
        file.end();
        if (totalSize > 0 && downloaded < totalSize) {
          try { unlinkSync(destPath); } catch {}
          reject(new Error(`下载不完整 (${downloaded}/${totalSize})，请重试或使用 init 离线安装`));
        } else {
          resolve();
        }
      });
      response.on('error', (err) => { file.close(); reject(err); });
    }).on('error', reject);
  });
}

async function downloadWithFallback(urls, destPath) {
  for (const url of urls) {
    try { await downloadFile(url, destPath); return true; } catch {}
  }
  return false;
}

async function installPythonDeps(cwd) {
  const reqFile = join(cwd, '.opencode', 'scripts', 'requirements.txt');
  if (!existsSync(reqFile)) return 'skipped';

  // 验证 requirements.txt 来自可信源（仅允许 GitHub + 白名单镜像）
  try {
    const content = readFileSync(reqFile, 'utf-8');
    const lines = content.split('\n').filter(l => l.trim() && !l.trim().startsWith('#'));
    // 每行必须是合法的 pip requirement 格式（package==version 或 package>=version）
    const valid = lines.every(l => /^[a-zA-Z0-9._-]+([><=!~]+[a-zA-Z0-9._*-]+)?(\s*;\s*[a-zA-Z_]+.*)?$/.test(l.trim().split(';')[0].trim().split(/[><=!~]+/)[0]));
    if (!valid || lines.length === 0) {
      warn('依赖文件格式异常，跳过 pip 安装以确保安全');
      return 'skipped';
    }
  } catch { return 'skipped'; }

  const python = await detectPython();
  if (!python) { info('未检测到 Python，跳过依赖更新'); return 'skipped'; }

  const s = createSpinner('更新 Python 依赖');
  s.start();
  return new Promise((resolve) => {
    const child = spawn(python, ['-m', 'pip', 'install', '-r', reqFile, '--upgrade'], { stdio: 'pipe' });
    child.on('close', (code) => {
      if (code === 0) { s.stop('Python 依赖已更新'); resolve('updated'); }
      else { s.fail('pip 更新失败'); resolve('failed'); }
    });
    child.on('error', () => { s.fail('pip 启动失败'); resolve('failed'); });
  });
}

export async function update(options = {}) {
  const cwd = process.cwd();
  const dest = join(cwd, '.opencode');

  if (!existsSync(dest)) {
    process.stderr.write('未找到 .opencode/，请先运行 init 安装。\n');
    process.exit(1);
  }

  try {
    const doit = await confirm('将下载最新版本覆盖现有 .opencode/，继续？', true);
    if (!doit) { info('已取消'); return; }

    // 优先用 npx 已下载的内置离线包
    const offline = join(__pkgRoot, 'offline', 'opencode-bundle.tar.gz');
    if (existsSync(offline)) {
      step(1, 2, '从内置离线包更新');
      const s = createSpinner('解压中');
      s.start();
      try {
        const count = await extractTarGz(offline, dest);
        s.stop(`已更新 ${count} 个文件`);
        stepOk(1, 2, `已更新（离线包，${count} 个文件）`);
      } catch (e) {
        s.fail(`解压失败: ${e.message}`);
        process.exit(1);
      }
    } else {
      // 离线包不存在，回退网络下载
      step(1, 3, '下载最新版本');
      const urls = options.mirror
        ? [`${options.mirror}${GITHUB_TARBALL}`]
        : [GITHUB_TARBALL];
      for (const m of MIRRORS) urls.push(`${m}${GITHUB_TARBALL}`);

      const tmp = join(cwd, '_opencode_update.tar.gz');
      const s = createSpinner('下载中');
      s.start();
      if (!(await downloadWithFallback(urls, tmp))) {
        s.fail('下载失败');
        process.stderr.write('\n  所有下载地址均不可用。');
        process.stderr.write('\n  请使用 init 离线安装:\n');
        process.stderr.write('    npx @cszx/webnovel-writer-opencode init\n');
        process.exit(1);
      }
      s.stop('下载完成');
      stepOk(1, 3, '下载完成');

      step(2, 3, '解压更新');
      const es = createSpinner('解压中');
      es.start();
      try {
        const count = await extractTarGz(tmp, dest, PREFIX);
        if (count === 0) throw new Error('压缩包为空');
        es.stop(`已更新 ${count} 个文件`);
        stepOk(2, 3, `已更新 ${count} 个文件`);
        try { unlinkSync(tmp); } catch {}
      } catch (e) {
        es.fail('解压失败');
        try { unlinkSync(tmp); } catch {}
        process.stderr.write(`\n  解压失败: ${e.message}\n`);
        process.stderr.write('  请使用 init 离线安装:\n');
        process.stderr.write('    npx @cszx/webnovel-writer-opencode init\n');
        process.exit(1);
      }
    }

    const stepNum = existsSync(join(__pkgRoot, 'offline', 'opencode-bundle.tar.gz')) ? 2 : 3;
    const total = stepNum;
    step(stepNum, total, '更新 Python 依赖');
    const pr = await installPythonDeps(cwd);
    stepOk(stepNum, total, pr === 'updated' ? 'Python 依赖已更新' : '已跳过');

    const info = {
      installer: getInstallerVersion(),
      source: 'network',
      timestamp: new Date().toISOString(),
    };
    try {
      const mf = join(dest, 'manifest.json');
      if (existsSync(mf)) {
        const { version, tag } = JSON.parse(readFileSync(mf, 'utf-8'));
        if (version) info.opencode_version = version;
        if (tag) info.opencode_tag = tag;
      }
      writeFileSync(join(dest, 'version.json'), JSON.stringify(info, null, 2), 'utf-8');
    } catch {}

    success('更新完成！', ['已更新到最新版本']);
  } catch (e) {
    process.stderr.write(`\n更新失败: ${e.message}\n`);
    process.exit(1);
  }
}

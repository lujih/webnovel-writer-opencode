/**
 * init 命令 — 部署 .opencode/ 写作工具链
 *
 * npx @cszx/webnovel-writer-opencode init [--offline] [--mirror URL] [--no-pip] [--quiet]
 */

import { existsSync, mkdirSync, unlinkSync, createWriteStream, readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { get as httpsGet } from 'node:https';
import { get as httpGet } from 'node:http';
import { spawn } from 'node:child_process';

import {
  boxOpen, boxClose, boxRow,
  step, stepOk,
  info, warn, error, success,
  prompt, confirm,
  createSpinner,
} from '../core/ui.js';
import { extractTarGz } from '../core/extract.js';
import { detectPython } from '../core/python.js';

const __filename = fileURLToPath(import.meta.url);
const __pkgRoot = dirname(dirname(dirname(__filename))); // .npm-package/

// ── 常量 ──────────────────────────────────────────────────
const REPO = 'lujih/webnovel-writer-opencode';
const BRANCH = 'master';
const GITHUB_TARBALL = `https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz`;
const MIRRORS = ['https://ghproxy.com/', 'https://mirror.ghproxy.com/'];
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
const PREFIX = `${REPO.split('/')[1]}-${BRANCH}/.opencode`;

// ── 步骤 0: Node 版本检查 ────────────────────────────────

function checkNodeVersion() {
  const v = process.versions.node.split('.').map(Number);
  if (v[0] < 22) {
    process.stderr.write(`
当前 Node ${process.version}，需要 ≥ 22。升级: https://nodejs.org/
`);
    process.exit(1);
  }
}

// ── 步骤 1: 欢迎 ─────────────────────────────────────────

async function showWelcome(cwd) {
  const w = 60;
  process.stdout.write('\n');
  process.stdout.write(boxOpen('Webnovel Writer OpenCode Edition — 安装', w) + '\n');
  process.stdout.write(boxRow(`目录：${cwd}`, w) + '\n');
  process.stdout.write(boxRow('', w) + '\n');
  process.stdout.write(boxRow('将在此目录部署 .opencode/ 写作工具链', w) + '\n');
  process.stdout.write(boxRow('按 Enter 继续，输入 q 退出', w) + '\n');
  process.stdout.write(boxClose(w) + '\n\n');

  const answer = await prompt('确认安装？');
  if (answer.toLowerCase() === 'q') {
    process.stdout.write('已取消。\n');
    process.exit(0);
  }
}

// ── 步骤 2: 下载 ─────────────────────────────────────────

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

      response.on('data', (chunk) => { file.write(chunk); });
      response.on('end', () => { file.end(); resolve(); });
      response.on('error', (err) => { file.close(); reject(err); });
    }).on('error', reject);
  });
}

async function downloadWithFallback(urls, destPath) {
  let lastErr = '';
  for (const url of urls) {
    try {
      await downloadFile(url, destPath);
      return true;
    } catch (e) {
      lastErr = e.message;
    }
  }
  if (lastErr) info(`最后错误: ${lastErr}`);
  return false;
}

// ── 步骤 3: 部署 .opencode/ ──────────────────────────────

async function deployOpencode(cwd, options) {
  const dest = join(cwd, '.opencode');

  if (existsSync(dest)) {
    const overwrite = await confirm('.opencode/ 已存在，是否覆盖？', false);
    if (!overwrite) { info('保留现有 .opencode/'); return 'skipped'; }
  }

  // 1) 离线包（tar 内已含 .opencode/ 目录，解压到 cwd 即得 cwd/.opencode/）
  const offlinePath = join(__pkgRoot, 'offline', 'opencode-bundle.tar.gz');
  if (!options.mirror && existsSync(offlinePath)) {
    const s = createSpinner('从离线包解压 .opencode/');
    s.start();
    try {
      const count = await extractTarGz(offlinePath, cwd);
      s.stop(`已解压 ${count} 个文件`);
      return 'offline';
    } catch (e) {
      s.fail(`离线包解压失败: ${e.message}`);
      info('切换为网络下载...');
    }
  }

  if (options.offline) error('离线包不存在且指定了 --offline。');

  // 2) 网络下载
  const urls = options.mirror
    ? [`${options.mirror}${GITHUB_TARBALL}`]
    : [GITHUB_TARBALL];
  for (const m of MIRRORS) urls.push(`${m}${GITHUB_TARBALL}`);

  const tmp = join(cwd, '_opencode_dl.tar.gz');
  const s = createSpinner('下载 .opencode/');
  s.start();
  if (!(await downloadWithFallback(urls, tmp))) {
    s.fail('所有下载地址均不可用');
    error('请检查网络连接，或通过 --mirror 指定镜像地址。');
  }
  s.stop('下载完成');

  const es = createSpinner('解压 .opencode/');
  es.start();
  try {
    const netCount = await extractTarGz(tmp, dest, PREFIX);
    if (netCount === 0) {
      es.fail('未提取到文件，可能 PREFIX 不匹配');
      error('请尝试离线安装：npx @cszx/webnovel-writer-opencode init --offline');
    }
    es.stop(`解压完成 (${netCount} 个文件)`);
    try { unlinkSync(tmp); } catch {}
  } catch (e) {
    es.fail(`解压失败: ${e.message}`);
    try { unlinkSync(tmp); } catch {}
    error('请手动下载并解压，或尝试离线安装：--offline');
  }

  return 'downloaded';
}

// ── 步骤 4: Python 依赖 ──────────────────────────────────

async function installPythonDeps(cwd, options) {
  if (options.skipPip) { info('跳过 Python 依赖（--no-pip）'); return 'skipped'; }

  const python = await detectPython();
  if (!python) {
    info('未检测到 Python 3.10+，请手动安装依赖:');
    info('  pip install -r .opencode/scripts/requirements.txt');
    return 'skipped';
  }

  const reqFile = join(cwd, '.opencode', 'scripts', 'requirements.txt');
  if (!existsSync(reqFile)) { warn('requirements.txt 不存在'); return 'skipped'; }

  try {
    const content = readFileSync(reqFile, 'utf-8');
    const lines = content.split('\n').filter(l => l.trim() && !l.trim().startsWith('#'));
    const valid = lines.every(l => /^[a-zA-Z0-9._-]+/.test(l.trim().split(/[><=!~]+/)[0]));
    if (!valid || lines.length === 0) { warn('依赖文件格式异常，跳过 pip 安装'); return 'skipped'; }
  } catch { return 'skipped'; }

  const doit = await confirm('安装 Python 依赖？', true);
  if (!doit) { info(`手动运行: ${python} -m pip install -r .opencode/scripts/requirements.txt`); return 'skipped'; }

  const s = createSpinner('pip install');
  s.start();
  return new Promise((resolve) => {
    const child = spawn(python, ['-m', 'pip', 'install', '-r', reqFile], { stdio: 'pipe' });
    child.on('close', (code) => {
      if (code === 0) { s.stop('Python 依赖安装完成'); resolve('installed'); }
      else { s.fail(`退出码 ${code}`); info('手动: pip install -r .opencode/scripts/requirements.txt'); resolve('failed'); }
    });
    child.on('error', () => { s.fail('pip 启动失败'); resolve('failed'); });
  });
}

// ── 步骤 5: 完成 ─────────────────────────────────────────

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

function writeVersion(cwd, source) {
  const info = {
    installer: getInstallerVersion(),
    source,
    timestamp: new Date().toISOString(),
  };
  try {
    const mf = join(cwd, '.opencode', 'manifest.json');
    if (existsSync(mf)) {
      const { version, tag } = JSON.parse(readFileSync(mf, 'utf-8'));
      if (version) info.opencode_version = version;
      if (tag) info.opencode_tag = tag;
    }
  } catch {}
  try {
    writeFileSync(join(cwd, '.opencode', 'version.json'), JSON.stringify(info, null, 2), 'utf-8');
  } catch {}
}

function showComplete(cwd) {
  success('安装完成！', [
    `目录：${cwd}`,
    '',
    '下一步：',
    '  1. 在此目录打开 OpenCode',
    '  2. 说"开始写书"或使用 /webnovel-init',
    '  3. 配置 .env API Key',
  ]);
}

// ── 入口 ──────────────────────────────────────────────────

export async function init(options = {}) {
  const cwd = process.cwd();

  try {
    checkNodeVersion();                                          // 0
    if (!options.quiet) await showWelcome(cwd);                  // 1

    step(1, 2, '部署 .opencode/ 写作工具链');                    // 2-3
    const r = await deployOpencode(cwd, options);
    stepOk(1, 2, r === 'offline' ? '已部署（离线包）' : r === 'downloaded' ? '已部署（网络下载）' : '已跳过');
    if (r !== 'skipped') writeVersion(cwd, r);

    step(2, 2, '安装 Python 依赖');                              // 4
    const pr = await installPythonDeps(cwd, options);
    stepOk(2, 2, pr === 'installed' ? 'Python 依赖安装完成' : '已跳过');

    showComplete(cwd);                                           // 5
  } catch (e) {
    process.stderr.write(`\n安装失败: ${e.message}\n`);
    if (e.stack && process.env.DEBUG) process.stderr.write(`${e.stack}\n`);
    process.exit(1);
  }
}

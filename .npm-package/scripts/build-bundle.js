#!/usr/bin/env node

/**
 * 离线包构建脚本 — 纯 Node.js，零系统依赖
 *
 * 从本地 .opencode/ 目录打包为 offline/opencode-bundle.tar.gz
 * 排除 __pycache__、*.pyc、node_modules
 *
 * 用法: node scripts/build-bundle.js
 */

import { readFileSync, statSync, readdirSync, createWriteStream } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createGzip } from 'node:zlib';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

const __filename = fileURLToPath(import.meta.url);
const __pkgRoot = dirname(dirname(__filename));            // .npm-package/
const OPC_DIR = join(__pkgRoot, '..', '.opencode');        // 仓库根 .opencode/
const OUT_DIR = join(__pkgRoot, 'offline');
const OUT_PATH = join(OUT_DIR, 'opencode-bundle.tar.gz');

const BLOCK_SIZE = 512;

const SKIP_DIRS = new Set(['__pycache__', 'node_modules', '.git']);
const SKIP_EXTS = new Set(['.pyc', '.pyo']);
const SKIP_FILES = new Set(['.DS_Store']);

// ── UStar header ──────────────────────────────────────────

function padOctal(n, len) {
  const s = n.toString(8);
  if (s.length >= len) return s.slice(0, len);
  return s.padStart(len - 1, '0') + ' ';
}

function makeHeader(name, size, type = '0', mtime = 0) {
  const h = Buffer.alloc(BLOCK_SIZE);

  // name (100) + prefix (155)
  const nameBytes = Buffer.from(name, 'utf-8');
  if (nameBytes.length <= 100) {
    nameBytes.copy(h, 0);
  } else {
    // 寻找最长的 '/' 分割点使 name ≤ 100 且 prefix ≤ 155
    const parts = name.split('/');
    let best = 0;
    for (let i = parts.length - 1; i >= 0; i--) {
      const pre = parts.slice(0, i).join('/');
      const suf = parts.slice(i).join('/');
      if (Buffer.byteLength(pre, 'utf-8') <= 155 && Buffer.byteLength(suf, 'utf-8') <= 100) {
        Buffer.from(suf, 'utf-8').copy(h, 0);
        Buffer.from(pre, 'utf-8').copy(h, 345);
        best = 1;
        break;
      }
    }
    if (!best) {
      // 无法拆分，截断 name
      nameBytes.copy(h, 0, 0, Math.min(nameBytes.length, 100));
    }
  }

  h.write(padOctal(0o644, 8), 100, 'ascii');   // mode
  h.write(padOctal(0, 8), 108, 'ascii');        // uid
  h.write(padOctal(0, 8), 116, 'ascii');        // gid
  h.write(padOctal(size, 12), 124, 'ascii');    // size
  h.write(padOctal(mtime, 12), 136, 'ascii');   // mtime
  h.write('        ', 148, 'ascii');            // chksum placeholder
  h[156] = type.charCodeAt(0);                  // type flag
  h.write('ustar\x0000', 257, 'ascii');          // magic
  h.write('00', 263, 'ascii');                   // version

  // checksum
  let sum = 0;
  for (let i = 0; i < BLOCK_SIZE; i++) sum += h[i];
  h.write(padOctal(sum, 7).slice(0, 7), 148, 'ascii');

  return h;
}

function padBlock(buf) {
  const rem = buf.length % BLOCK_SIZE;
  if (rem === 0) return buf;
  const pad = Buffer.alloc(BLOCK_SIZE - rem);
  return Buffer.concat([buf, pad]);
}

// ── 遍历 ──────────────────────────────────────────────────

function collectFiles(dir, base) {
  const results = [];
  const entries = readdirSync(dir, { withFileTypes: true });

  for (const e of entries) {
    if (SKIP_DIRS.has(e.name)) continue;
    if ([...SKIP_EXTS].some(ext => e.name.endsWith(ext))) continue;
    if (SKIP_FILES.has(e.name)) continue;

    const full = join(dir, e.name);
    const rel = relative(base, full).replace(/\\/g, '/');

    if (e.isDirectory()) {
      results.push({ name: rel + '/', size: 0, type: '5', mtime: 0 });
      results.push(...collectFiles(full, base));
    } else if (e.isFile()) {
      const st = statSync(full);
      results.push({ name: rel, size: st.size, type: '0', mtime: Math.floor(st.mtimeMs / 1000), path: full });
    }
  }

  return results;
}

// ── 构建 tar.gz 流 ──────────────────────────────────────

async function buildBundle() {
  const { mkdirSync, copyFileSync } = await import('node:fs');
  mkdirSync(OUT_DIR, { recursive: true });

  if (!statSync(OPC_DIR).isDirectory()) {
    throw new Error(`.opencode/ 不存在: ${OPC_DIR}`);
  }

  // GPL §4(a)：分发时必须附上许可证副本。package.json 的 "files" 以
  // .npm-package/ 为基准，而正本 LICENSE 在仓库根，故构建时复制一份过去。
  // 在这里复制而不是把两份文件都提交进 git——后者必然漂移。
  const REPO_LICENSE = join(__pkgRoot, '..', 'LICENSE');
  const PKG_LICENSE = join(__pkgRoot, 'LICENSE');
  if (statSync(REPO_LICENSE).isFile()) {
    copyFileSync(REPO_LICENSE, PKG_LICENSE);
    process.stdout.write(`  ✅ 许可证已附: ${PKG_LICENSE}\n`);
  } else {
    throw new Error(`LICENSE 不存在: ${REPO_LICENSE}（GPL 分发必须附许可证）`);
  }

  process.stdout.write('\n=== 构建离线包 ===\n');
  process.stdout.write(`  源: ${OPC_DIR}\n`);
  process.stdout.write(`  输出: ${OUT_PATH}\n\n`);

  // 项目说明：OpenCode 2.x 只发现环境里的 AGENTS.md（不在 .opencode/ 里），
  // 缺了它整套项目规则对模型不可见。这两份在仓库根、不在 .opencode/ 下，
  // 必须显式追加为 tar 根条目——解压到 cwd 后与 .opencode/ 平级，
  // 正是 AGENTS.md 发现路径能看到的位置。
  // CLAUDE.md 是正本（sync-agents-md 从它镜像），随包提供以便日后重生成。
  //
  // 顺序说明：说明文件的门禁**排在**前端产物门禁之前。两者都会拒绝出包，
  // 但缺说明文件时诊断优先级更高（整套规则对模型不可见），且既有测试
  // test_missing_agents_md_exits_nonzero 依赖这条顺序。
  const docEntries = [];
  for (const doc of ['AGENTS.md', 'CLAUDE.md']) {
    const p = join(__pkgRoot, '..', doc);
    let st;
    try {
      st = statSync(p);
    } catch {
      throw new Error(`${doc} 不存在: ${p}（V2 用户需要它，缺失时拒绝出包）`);
    }
    if (!st.isFile()) throw new Error(`${doc} 不是普通文件: ${p}`);
    docEntries.push({ name: doc, size: st.size, type: '0', mtime: Math.floor(st.mtimeMs / 1000), path: p });
  }

  // dashboard 前端构建产物：`webnovel-dashboard` skill 的**硬门禁**是
  //   test -f frontend/dist/index.html || exit 1
  // 所以 dist 缺失时打出的包，安装后那个 skill 第一步就退出——而且全程静默：
  // 构建成功、verify 通过、npm 发布成功，用户直到打开面板才发现。
  // 与 AGENTS.md 同样处理：缺失即拒绝出包，绝不允许产出功能残缺的包。
  const FRONTEND_DIST = join(OPC_DIR, 'dashboard', 'frontend', 'dist');
  const FRONTEND_INDEX = join(FRONTEND_DIST, 'index.html');
  let distStat;
  try {
    distStat = statSync(FRONTEND_INDEX);
  } catch {
    throw new Error(
      `前端构建产物不存在: ${FRONTEND_INDEX}\n` +
        `  该文件是 webnovel-dashboard skill 的硬性前提，缺失时包不可用。\n` +
        `  请先构建：cd .opencode/dashboard/frontend && npm install && npm run build\n` +
        `  并确认它已提交进 git（见 .gitignore 里 !.opencode/dashboard/frontend/dist/ 的说明）。`
    );
  }
  if (!distStat.isFile()) throw new Error(`前端产物不是普通文件: ${FRONTEND_INDEX}`);

  process.stdout.write('收集文件... ');
  const entries = collectFiles(OPC_DIR, dirname(OPC_DIR));
  process.stdout.write(`${entries.length} 个条目\n`);

  // 兜底：即使 index.html 在，也要确认 dist 整体真的被收进了 entries。
  // collectFiles 会跳过 SKIP_DIRS，未来若有人把 dist 加进跳过表，这条会响。
  const distEntries = entries.filter(e => e.name.includes('dashboard/frontend/dist/'));
  if (distEntries.length === 0) {
    throw new Error(
      `dist/index.html 存在，但没有任何 dist 文件被收进打包列表——` +
        `检查 SKIP_DIRS/SKIP_FILES 是否误排除了它。`
    );
  }
  process.stdout.write(`  ✅ 前端产物已入包: ${distEntries.length} 个文件\n`);

  for (const e of docEntries) {
    entries.push(e);
    process.stdout.write(`  ✅ 说明文件已入包: ${e.name}\n`);
  }

  // 构建 tar buffer
  process.stdout.write('打包 tar... ');
  const chunks = [];
  for (const e of entries) {
    chunks.push(makeHeader(e.name, e.size, e.type, e.mtime));
    if (e.size > 0 && e.path) {
      const data = readFileSync(e.path);
      chunks.push(padBlock(data));
    }
  }
  // tar 结束标记（两个全零块）
  chunks.push(Buffer.alloc(BLOCK_SIZE));
  chunks.push(Buffer.alloc(BLOCK_SIZE));

  const tarBuf = Buffer.concat(chunks);
  process.stdout.write(`${(tarBuf.length / 1024 / 1024).toFixed(1)} MB\n`);

  // gzip + 写入
  process.stdout.write('gzip 压缩... ');
  const gzip = createGzip({ level: 9 });
  const out = createWriteStream(OUT_PATH);

  await pipeline(Readable.from(tarBuf), gzip, out);

  const finalSize = (statSync(OUT_PATH).size / 1024 / 1024).toFixed(1);
  process.stdout.write(`${finalSize} MB\n`);

  process.stdout.write(`\n  ✅ 离线包已构建: ${OUT_PATH} (${finalSize} MB)\n\n`);
}

buildBundle().catch((e) => {
  process.stderr.write(`\n构建失败: ${e.message}\n`);
  if (process.env.DEBUG) console.error(e);
  process.exit(1);
});

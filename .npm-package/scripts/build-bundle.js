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
  const { mkdirSync } = await import('node:fs');
  mkdirSync(OUT_DIR, { recursive: true });

  if (!statSync(OPC_DIR).isDirectory()) {
    throw new Error(`.opencode/ 不存在: ${OPC_DIR}`);
  }

  process.stdout.write('\n=== 构建离线包 ===\n');
  process.stdout.write(`  源: ${OPC_DIR}\n`);
  process.stdout.write(`  输出: ${OUT_PATH}\n\n`);

  process.stdout.write('收集文件... ');
  const entries = collectFiles(OPC_DIR, dirname(OPC_DIR));
  process.stdout.write(`${entries.length} 个条目\n`);

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

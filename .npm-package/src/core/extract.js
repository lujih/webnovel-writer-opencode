/**
 * tar.gz 解压模块 — 零依赖，纯 Node 22 标准库
 *
 * 使用 node:zlib (gzip) + 手动 UStar/Pax tar 解析
 * UStar 格式：512 字节 header 块 + 数据块（512 对齐）
 */

import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { join, dirname, normalize, relative } from 'node:path';
import { gunzipSync } from 'node:zlib';

const BLOCK_SIZE = 512;
const NAME_LEN = 100;
const SIZE_OFFSET = 124;
const SIZE_LEN = 12;
const TYPE_OFFSET = 156;
const PREFIX_OFFSET = 345;
const PREFIX_LEN = 155;

const decoder = new TextDecoder('utf-8');

function readCString(buf, offset, maxLen) {
  const end = buf.indexOf(0, offset);
  const len = end === -1 || end - offset > maxLen ? maxLen : end - offset;
  if (len === 0) return '';
  return decoder.decode(buf.subarray(offset, offset + len));
}

function readOctal(buf, offset, len) {
  let s = '';
  for (let i = 0; i < len; i++) {
    const b = buf[offset + i];
    if (b === 0 || b === 32) break;
    s += String.fromCharCode(b);
  }
  return parseInt(s.trim(), 8) || 0;
}

/**
 * @param {Buffer} tarBuffer
 * @param {string} destDir
 * @param {string} prefix - 路径前缀（如 'repo-master/.opencode/'）
 * @param {string[]|null} only - 非空时只落盘这些剥离前缀后的相对路径
 *   （用于从源码 tarball 根只抽 AGENTS.md/CLAUDE.md，不碰其余仓库文件）
 * @returns {number} 提取的文件数
 */
function extractFromTar(tarBuffer, destDir, prefix = '', only = null) {
  let offset = 0;
  let fileCount = 0;
  const cleanPrefix = prefix ? prefix.replace(/^\.?\//, '').replace(/\/$/, '') : '';

  while (offset + BLOCK_SIZE <= tarBuffer.length) {
    const header = tarBuffer.subarray(offset, offset + BLOCK_SIZE);

    // 全零块 = tar 结束
    if (header.every(b => b === 0)) {
      offset += BLOCK_SIZE;
      if (offset + BLOCK_SIZE <= tarBuffer.length) {
        const next = tarBuffer.subarray(offset, offset + BLOCK_SIZE);
        if (next.every(b => b === 0)) break;
      }
      continue;
    }

    // 文件名（UStar: name + prefix 拼接）
    const rawName = readCString(header, 0, NAME_LEN);
    const rawPrefix = readCString(header, PREFIX_OFFSET, PREFIX_LEN);
    const name = rawPrefix ? rawPrefix + '/' + rawName : rawName;

    const size = readOctal(header, SIZE_OFFSET, SIZE_LEN);
    const typeFlag = String.fromCharCode(header[TYPE_OFFSET]);
    offset += BLOCK_SIZE;

    // 前缀过滤：保留 cleanPrefix/ 下的条目 + cleanPrefix 目录自身
    if (cleanPrefix) {
      const cleanName = name.replace(/^\.?\//, '');
      const isUnder = cleanName.startsWith(cleanPrefix + '/');
      const isSelf = cleanName === cleanPrefix;
      if (!isUnder && !isSelf) {
        offset += Math.ceil(size / BLOCK_SIZE) * BLOCK_SIZE;
        continue;
      }
    }

    // 计算前缀剥离后的目标名
    let targetName = name.replace(/^\.?\//, '');
    if (cleanPrefix) {
      if (targetName.startsWith(cleanPrefix + '/')) {
        targetName = targetName.slice(cleanPrefix.length + 1);
      } else if (targetName === cleanPrefix) {
        targetName = '';
      }
    }

    // only 过滤：只允许白名单路径落盘（含目录条目一并跳过——写文件时会自建父目录）
    if (only) {
      const t = targetName.replace(/\\/g, '/').replace(/\/$/, '');
      if (!t || !only.includes(t)) {
        offset += Math.ceil(size / BLOCK_SIZE) * BLOCK_SIZE;
        continue;
      }
    }

    // 路径穿越防护
    if (targetName) {
      const clean = targetName.replace(/\\/g, '/').replace(/\/$/, '');
      const resolved = normalize(clean).replace(/\\/g, '/');
      if (resolved.startsWith('..') || resolved !== clean) {
        offset += Math.ceil(size / BLOCK_SIZE) * BLOCK_SIZE;
        continue;
      }
    }

    if (typeFlag === '5') {
      if (targetName) mkdirSync(join(destDir, targetName), { recursive: true });
    } else if (typeFlag === '0' || typeFlag === '\x00') {
      if (targetName && offset + size <= tarBuffer.length) {
        const destPath = join(destDir, targetName);
        mkdirSync(dirname(destPath), { recursive: true });
        if (size > 0) {
          writeFileSync(destPath, tarBuffer.subarray(offset, offset + size));
        } else {
          writeFileSync(destPath, Buffer.alloc(0));
        }
        fileCount++;
      }
    }

    offset += Math.ceil(size / BLOCK_SIZE) * BLOCK_SIZE;
  }

  return fileCount;
}

/**
 * 解压 tar.gz 文件
 * @param {string} tarGzPath
 * @param {string} destDir
 * @param {string} prefix
 * @param {string[]|null} only - 白名单过滤，见 extractFromTar
 * @returns {Promise<number>}
 */
export async function extractTarGz(tarGzPath, destDir, prefix = '', only = null) {
  mkdirSync(destDir, { recursive: true });
  const compressed = readFileSync(tarGzPath);
  const decompressed = gunzipSync(compressed);
  return extractFromTar(decompressed, destDir, prefix, only);
}

/** 从源码 tarball 根目录只抽项目说明文件（AGENTS.md / CLAUDE.md）。
 *
 * 必要性：init/update 的网络路径用 PREFIX 把解压限定在 `.opencode/` 内
 * （dest 也是 `.opencode/`），仓库根的指令文件被前缀过滤跳过——于是
 * OpenCode 2.x 的用户拿不到任何项目说明（V2 只发现 AGENTS.md）。
 * 网络源码 tarball 里本来就有这两个文件，单抽一遍即可，无额外下载。
 *
 * @param {string} tarGzPath 源码 tarball（refs/heads 分支归档）
 * @param {string} destDir 落盘目录（工作区根，与 .opencode/ 平级）
 * @param {string} repoBase 归档顶层目录名，如 `webnovel-writer-opencode-master`
 * @returns {Promise<{extracted: string[], missing: string[]}>}
 */
export async function extractRootDocs(tarGzPath, destDir, repoBase) {
  const wanted = ['AGENTS.md', 'CLAUDE.md'];
  const extracted = [];
  for (const doc of wanted) {
    // 逐文件抽：计数能精确说明 tarball 里到底有没有它
    const n = await extractTarGz(tarGzPath, destDir, `${repoBase}/`, [doc]);
    if (n > 0 && existsSync(join(destDir, doc))) extracted.push(doc);
  }
  return { extracted, missing: wanted.filter((d) => !extracted.includes(d)) };
}

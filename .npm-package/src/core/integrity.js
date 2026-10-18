/**
 * 完整性校验 — sha256，零依赖（node:crypto）
 *
 * 为什么要它：安装器此前的唯一校验是 `content-length`。镜像站（含自动追加的
 * 第三方 ghproxy）返回一个**长度正确**的恶意 tarball 就能一路过关，而
 * extractRootDocs 会把其中的 AGENTS.md / CLAUDE.md 写到工作区根——OpenCode
 * 会自动加载它们，等于安装器级别的提示词注入，且没有任何完整性门禁。
 *
 * 覆盖范围（有意分两级）：
 *  - 离线包（随 npm 包发布，内容固定）：**强制**校验 .sha256 sidecar，不匹配即
 *    拒绝解压。这是唯一一条能把"发布者签名"落到用户机器上的路径。
 *  - 网络下载（分支 tarball，内容随时变）：无法预置哈希，只计算并打印供审计；
 *    用户可用 --sha256 自己钉死。
 *
 * 为什么不给网络路径也做强制校验：`refs/heads/master` 是可变的，任何写死的
 * 哈希第二天就失效；而 GitHub 不提供 tarball 官方校验和。钉死到不可变 tag 会
 * 改变 tarball 顶层目录名（PREFIX/REPO_BASE），对这一轮属于高风险改动。
 */

import { createHash } from 'node:crypto';
import { createReadStream, existsSync, readFileSync, writeFileSync } from 'node:fs';

/** @param {string} filePath @returns {Promise<string>} 64 位小写 hex */
export function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    const stream = createReadStream(filePath);
    stream.on('error', reject);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

/**
 * 校验文件的 sha256。
 *
 * @param {string} filePath
 * @param {string} expectedHex 64 位 hex，比较前统一小写、去空白
 * @returns {Promise<{ok: boolean, actual: string, expected: string}>}
 */
export async function verifyFileSha256(filePath, expectedHex) {
  const actual = await sha256File(filePath);
  const expected = String(expectedHex || '').trim().toLowerCase();
  return { ok: expected.length === 64 && actual === expected, actual, expected };
}

/**
 * 生成 `<file>.sha256` sidecar，内容为 `<hex>  <basename>\n`。
 *
 * 用 `sha256sum` 的经典两空格格式，这样 `sha256sum -c` 也能直接验；
 * 两个空格与一个空格的区别在二进制模式标记（`*`），这里不需要。
 *
 * @param {string} filePath
 * @returns {Promise<string>} 写入的 hex
 */
export async function writeSha256Sidecar(filePath) {
  const hex = await sha256File(filePath);
  const sidecar = `${filePath}.sha256`;
  const base = filePath.split(/[\\/]/).pop();
  writeFileSync(sidecar, `${hex}  ${base}\n`, 'utf8');
  return hex;
}

/**
 * 读取 sidecar 里的 hex。只取第一段，忽略文件名栏——文件名在 npm 包里可能
 * 被重命名，但哈希本身是内容的指纹。
 *
 * @param {string} sidecarPath
 * @returns {string|null} 无效或不存在时返回 null
 */
export function readSha256Sidecar(sidecarPath) {
  if (!existsSync(sidecarPath)) return null;
  try {
    const first = readFileSync(sidecarPath, 'utf8').trim().split(/\s+/)[0] || '';
    return /^[0-9a-fA-F]{64}$/.test(first) ? first.toLowerCase() : null;
  } catch {
    return null;
  }
}

/** 形如 sha256 的形状检查，供 --sha256 参数用。 */
export function looksLikeSha256(value) {
  return /^[0-9a-fA-F]{64}$/.test(String(value || '').trim());
}

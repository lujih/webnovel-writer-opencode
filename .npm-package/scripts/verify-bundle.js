#!/usr/bin/env node
/**
 * 验证离线包完整性
 * 用法: node scripts/verify-bundle.js
 */

import { mkdtempSync, existsSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { extractTarGz } from '../src/core/extract.js';

const BUNDLE = join(import.meta.dirname, '..', 'offline', 'opencode-bundle.tar.gz');
const testDir = mkdtempSync(join(tmpdir(), 'ci-verify-'));

try {
  const count = await extractTarGz(BUNDLE, testDir);
  console.log(`提取: ${count} 文件`);

  const checks = [
    '.opencode/package.json',
    '.opencode/scripts/webnovel.py',
    '.opencode/scripts/requirements.txt',
    '.opencode/skills/webnovel-write/SKILL.md',
    '.opencode/agents/context-agent.md',
    // dashboard 前端产物：`webnovel-dashboard` skill 以它为硬门禁，缺失时
    // 安装后 skill 第一步就 exit 1。verify 必须与 build 的断言对齐——
    // 早先两边都没有这条，于是"没有前端的包"一路绿灯发布了出去。
    '.opencode/dashboard/frontend/dist/index.html',
    // 项目说明必须在 tar 根（与 .opencode/ 平级），OpenCode V2 才发现得到
    'AGENTS.md',
    'CLAUDE.md',
    // ── 一致性 / 审计链的关键模块 ──────────────────────────────
    // 这些是"能力存在但进不了包"的高危项：模块本身没有运行时依赖清单撑着，
    // 少一个文件，安装后的 canon/audit/重跑全部失效，而构建仍然成功。
    '.opencode/scripts/data_modules/canon_locks.py',
    '.opencode/scripts/data_modules/state_change_log.py',
    '.opencode/scripts/data_modules/projection_log.py',
    // 变更审计页是动态 import 的 chunk（文件名带 hash），单独做 glob 检查
  ];

  let failed = 0;
  for (const f of checks) {
    const ok = existsSync(join(testDir, f));
    console.log(ok ? `  ✓ ${f}` : `  ✗ ${f}`);
    if (!ok) failed++;
  }

  // 懒加载页面是 content-hash 命名的 chunk（`ChangesPage-<hash>.js`），
  // 路径无法硬编码——只能按前缀 glob。缺了它，路由 `/changes` 会 404，
  // 而 index.html 仍在，前面那些逐路径检查一条都不会响。
  let changesChunk = false;
  try {
    const assets = join(testDir, '.opencode/dashboard/frontend/dist/assets');
    changesChunk = readdirSync(assets).some(
      name => name.startsWith('ChangesPage-') && name.endsWith('.js')
    );
  } catch {
    changesChunk = false;
  }
  console.log(changesChunk
    ? '  ✓ dist/assets/ChangesPage-*.js（懒加载 chunk）'
    : '  ✗ dist/assets/ChangesPage-*.js（懒加载 chunk）');
  if (!changesChunk) failed++;

  if (failed > 0) {
    console.error(`\n${failed} 个文件缺失`);
    process.exit(1);
  }

  console.log('\n✓ 离线包验证通过');
} catch (e) {
  console.error('验证失败:', e.message);
  process.exit(1);
} finally {
  rmSync(testDir, { recursive: true, force: true });
}

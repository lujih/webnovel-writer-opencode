#!/usr/bin/env node
/**
 * 验证离线包完整性
 * 用法: node scripts/verify-bundle.js
 */

import { mkdtempSync, existsSync, rmSync } from 'node:fs';
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
  ];

  let failed = 0;
  for (const f of checks) {
    const ok = existsSync(join(testDir, f));
    console.log(ok ? `  ✓ ${f}` : `  ✗ ${f}`);
    if (!ok) failed++;
  }

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

/**
 * uninstall 命令 — 卸载 .opencode/
 */

import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';

import { step, stepOk, info, success, confirm } from '../core/ui.js';

export async function uninstall() {
  const cwd = process.cwd();
  const dest = join(cwd, '.opencode');

  if (!existsSync(dest)) {
    info('未找到 .opencode/，无需卸载。');
    return;
  }

  try {
    const doit = await confirm('确认卸载 .opencode/？这将删除所有写作工具链文件', false);
    if (!doit) { info('已取消'); return; }

    step(1, 1, '删除 .opencode/');
    rmSync(dest, { recursive: true, force: true });
    stepOk(1, 1, '.opencode/ 已删除');

    // 清理可能残留的临时文件
    for (const f of ['_opencode_dl.tar.gz', '_opencode_update.tar.gz']) {
      const p = join(cwd, f);
      try { if (existsSync(p)) rmSync(p); } catch {}
    }

    success('卸载完成', ['书项目文件（大纲/正文/设定集等）未被删除']);
  } catch (e) {
    process.stderr.write(`\n卸载失败: ${e.message}\n`);
    process.exit(1);
  }
}

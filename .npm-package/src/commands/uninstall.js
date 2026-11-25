/**
 * uninstall 命令 — 卸载 .opencode/
 */

import { existsSync, rmSync, readFileSync } from 'node:fs';
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

    // 项目说明是工具链文件（安装时部署在工作区根，与 .opencode/ 平级），
    // 卸载时一并清理——但只删"未被用户改过"的镜像：AGENTS.md 逐字节等于
    // CLAUDE.md 即说明没被手工改过；两者不等则保留 AGENTS.md 并告知，
    // 用户的自定义指令不因卸载而丢失。
    const agents = join(cwd, 'AGENTS.md');
    const claude = join(cwd, 'CLAUDE.md');
    if (existsSync(agents) && existsSync(claude)) {
      const mirror = readFileSync(agents).equals(readFileSync(claude));
      if (mirror) {
        rmSync(agents); rmSync(claude);
        info('已删除未修改的项目说明（AGENTS.md / CLAUDE.md）');
      } else {
        info('AGENTS.md 与 CLAUDE.md 不一致（你改过说明文件），已保留两者');
      }
    } else {
      // 只有一个文件存在时**无法**判定它是不是「未修改的安装器镜像」。
      // 修复前这里无条件 rmSync：用户删掉 CLAUDE.md、只留自己写/改过的
      // AGENTS.md 时，卸载会把用户文件一起删掉——卸载不该碰用户内容。
      for (const f of [agents, claude]) {
        if (existsSync(f)) {
          const name = f.split(/[\\/]/).pop();
          info(`${name} 无法确认为安装器部署的镜像（另一份说明文件不存在），已保留`);
        }
      }
    }

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

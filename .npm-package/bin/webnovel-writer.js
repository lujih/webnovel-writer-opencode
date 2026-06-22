#!/usr/bin/env node

/**
 * webnovel-writer-opencode CLI 入口
 *
 * 用法：
 *   npx @cszx/webnovel-writer-opencode init [options]
 */

import { init } from '../src/commands/init.js';
import { update } from '../src/commands/update.js';
import { uninstall } from '../src/commands/uninstall.js';

const command = process.argv[2] || 'init';
const args = process.argv.slice(3);

// 顶层 --help/-h 直接显示帮助
if (command === '--help' || command === '-h') {
  showHelp();
  process.exit(0);
}

function parseArgs(args) {
  const options = {
    offline: false,
    mirror: null,
    skipPip: false,
    quiet: false,
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--offline':
        options.offline = true;
        break;
      case '--mirror':
        if (args[i + 1] && !args[i + 1].startsWith('--')) {
          options.mirror = args[++i].replace(/\/?$/, '/');
        } else {
          process.stderr.write('错误: --mirror 需要 URL 参数\n');
          process.exit(1);
        }
        break;
      case '--no-pip':
        options.skipPip = true;
        break;
      case '--quiet':
      case '-q':
        options.quiet = true;
        break;
      case '--help':
      case '-h':
        showHelp();
        process.exit(0);
      default:
        if (args[i].startsWith('-')) {
          process.stderr.write(`警告: 未知选项 ${args[i]} 已忽略\n`);
        }
        break;
    }
  }

  return options;
}

function showHelp() {
  process.stdout.write(`
Webnovel Writer OpenCode Edition — 网文 AI 写作工具链

用法:
  npx @cszx/webnovel-writer-opencode init     安装工作目录（默认命令）
  npx @cszx/webnovel-writer-opencode update   更新写作工具链
  npx @cszx/webnovel-writer-opencode uninstall 卸载

init 选项:
  --offline       仅使用离线包，不联网
  --mirror URL    指定下载镜像地址
  --no-pip        跳过 Python 依赖安装
  --quiet, -q     跳过欢迎界面

示例:
  npx @cszx/webnovel-writer-opencode init
  npx @cszx/webnovel-writer-opencode init --no-pip
  npx @cszx/webnovel-writer-opencode init --mirror https://ghproxy.com/
`);
}

async function main() {
  switch (command) {
    case 'init': {
      const options = parseArgs(args);
      await init(options);
      break;
    }
    case 'update': {
      const uOpts = { quiet: args.includes('-q') || args.includes('--quiet') };
      const mIdx = args.indexOf('--mirror');
      if (mIdx !== -1 && args[mIdx + 1]) uOpts.mirror = args[mIdx + 1].replace(/\/?$/, '/');
      await update(uOpts);
      break;
    }
    case 'uninstall': {
      await uninstall();
      break;
    }
    default:
      process.stderr.write(`未知命令: ${command}\n`);
      showHelp();
      process.exit(1);
  }
}

main().catch((e) => {
  process.stderr.write(`错误: ${e.message}\n`);
  process.exit(1);
});

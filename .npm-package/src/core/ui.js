/**
 * 终端 UI 模块 — ANSI 颜色 + Unicode 框 + spinner + 步骤指示
 * 零依赖，纯 Node 22 标准库
 */

import { createInterface } from 'node:readline';
import { stdout } from 'node:process';

// ── ANSI 控制码 ────────────────────────────────────────────
const CSI = '\x1b[';
const RESET = `${CSI}0m`;
const BOLD = `${CSI}1m`;
const DIM = `${CSI}2m`;
const GREEN = `${CSI}32m`;
const YELLOW = `${CSI}33m`;
const RED = `${CSI}31m`;
const CYAN = `${CSI}36m`;
const WHITE = `${CSI}37m`;
const CLREOL = `${CSI}K`; // 清除到行尾

// ── ANSI 剥离 ──────────────────────────────────────────────
const ANSI_RE = /\x1b\[[0-9;]*m/g;

function stripAnsi(s) {
  return String(s).replace(ANSI_RE, '');
}

// ── 字符串宽度估算（CJK 字符 = 2 宽度） ────────────────────
function strWidth(s) {
  let w = 0;
  for (const ch of stripAnsi(s)) {
    const cp = ch.codePointAt(0);
    // CJK + 全角符号
    if ((cp >= 0x1100 && cp <= 0x115F) ||
        (cp >= 0x2E80 && cp <= 0xA4CF) ||
        (cp >= 0xAC00 && cp <= 0xD7A3) ||
        (cp >= 0xF900 && cp <= 0xFAFF) ||
        (cp >= 0xFE10 && cp <= 0xFE19) ||
        (cp >= 0xFE30 && cp <= 0xFE6F) ||
        (cp >= 0xFF00 && cp <= 0xFF60) ||
        (cp >= 0xFFE0 && cp <= 0xFFE6) ||
        (cp >= 0x1F300 && cp <= 0x1F64F) ||
        (cp >= 0x1F900 && cp <= 0x1F9FF) ||
        (cp >= 0x20000 && cp <= 0x2FFFD) ||
        (cp >= 0x30000 && cp <= 0x3FFFD)) {
      w += 2;
    } else {
      w += 1;
    }
  }
  return w;
}

function padRight(s, width) {
  const sw = strWidth(s);
  const pad = Math.max(0, width - sw);
  return s + ' '.repeat(pad);
}

// ── 框 ─────────────────────────────────────────────────────
const BOX_TOP_LEFT = '┌'; const BOX_TOP_RIGHT = '┐';
const BOX_BOTTOM_LEFT = '└'; const BOX_BOTTOM_RIGHT = '┘';
const BOX_HORIZ = '─'; const BOX_VERT = '│';
const BOX_T_LEFT = '├'; const BOX_T_RIGHT = '┤';

export function boxLine(text, width) {
  return `${CYAN}${BOX_VERT}${RESET} ${padRight(text, width - 4)} ${CYAN}${BOX_VERT}${RESET}`;
}

export function boxOpen(title, width = 60) {
  const lines = [];
  lines.push(`${CYAN}${BOX_TOP_LEFT}${BOX_HORIZ.repeat(width - 2)}${BOX_TOP_RIGHT}${RESET}`);
  lines.push(boxLine(`${BOLD}${title}${RESET}`, width));
  lines.push(`${CYAN}${BOX_T_LEFT}${BOX_HORIZ.repeat(width - 2)}${BOX_T_RIGHT}${RESET}`);
  return lines.join('\n');
}

export function boxClose(width = 60) {
  return `${CYAN}${BOX_BOTTOM_LEFT}${BOX_HORIZ.repeat(width - 2)}${BOX_BOTTOM_RIGHT}${RESET}`;
}

export function boxRow(text, width = 60) {
  return boxLine(text, width);
}

// ── 输出函数 ──────────────────────────────────────────────
export function step(n, total, msg) {
  process.stdout.write(`  ${CYAN}[${n}/${total}]${RESET} ${msg}\n`);
}

export function stepOk(n, total, msg) {
  process.stdout.write(`\r  ${GREEN}✓${RESET} [${n}/${total}] ${msg}${CLREOL}\n`);
}

export function stepWarn(msg) {
  process.stdout.write(`  ${YELLOW}⚠${RESET} ${msg}\n`);
}

export function info(msg) {
  process.stdout.write(`  ${DIM}${msg}${RESET}\n`);
}

export function warn(msg) {
  process.stdout.write(`  ${YELLOW}⚠${RESET} ${msg}\n`);
}

export function success(title, lines = []) {
  const width = 60;
  process.stdout.write('\n');
  process.stdout.write(`${GREEN}${BOX_TOP_LEFT}${BOX_HORIZ.repeat(width - 2)}${BOX_TOP_RIGHT}${RESET}\n`);
  process.stdout.write(boxLine(`${BOLD}${title}${RESET}`, width) + '\n');
  for (const line of lines) {
    process.stdout.write(boxLine(line, width) + '\n');
  }
  process.stdout.write(`${GREEN}${BOX_BOTTOM_LEFT}${BOX_HORIZ.repeat(width - 2)}${BOX_BOTTOM_RIGHT}${RESET}\n`);
  process.stdout.write('\n');
}

export function error(msg) {
  process.stderr.write(`\n${RED}${BOLD}错误：${msg}${RESET}\n`);
  process.exit(1);
}

// ── 交互 ──────────────────────────────────────────────────
export async function prompt(msg, defaultValue = '') {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => {
    const suffix = defaultValue ? ` [${defaultValue}]` : '';
    rl.question(`  ${msg}${suffix}: `, (answer) => {
      rl.close();
      resolve(answer.trim() || defaultValue);
    });
  });
}

export async function confirm(msg, defaultYes = true) {
  const yn = defaultYes ? 'Y/n' : 'y/N';
  const answer = await prompt(`${msg} (${yn})`);
  if (!answer) return defaultYes;
  const a = answer.toLowerCase();
  return a === 'y' || a === 'yes' || a === '是';
}

// ── Spinner ────────────────────────────────────────────────
const SPIN_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

export function createSpinner(msg) {
  let i = 0;
  let timer = null;
  let active = false;

  return {
    start() {
      if (active) return;
      active = true;
      timer = setInterval(() => {
        stdout.write(`\r  ${CYAN}${SPIN_FRAMES[i++ % SPIN_FRAMES.length]}${RESET} ${msg}${CLREOL}`);
      }, 80);
    },
    stop(successMsg) {
      if (!active) return;
      clearInterval(timer);
      timer = null;
      active = false;
      stdout.write(`\r  ${GREEN}✓${RESET} ${successMsg || msg}${CLREOL}\n`);
    },
    fail(errorMsg) {
      if (!active) return;
      clearInterval(timer);
      timer = null;
      active = false;
      stdout.write(`\r  ${RED}✗${RESET} ${errorMsg || msg}${CLREOL}\n`);
    },
  };
}


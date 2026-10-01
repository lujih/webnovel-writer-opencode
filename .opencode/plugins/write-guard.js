/**
 * OpenCode Write Guard Plugin（V1 + V2 双兼容）
 *
 * 阻止 AI 通过 Write/Edit 工具直接写入受保护的运行时文件。
 * 这些文件只能通过 CLI 命令（webnovel.py chapter-commit 等）写入。
 *
 * 受保护文件：
 * - .webnovel/state.json
 * - .webnovel/index.db
 * - .webnovel/vectors.db
 * - .webnovel/memory_scratchpad.json
 * - .story-system/commits/
 * - .story-system/events/   （SSOT append-only 事件日志——伪造即改写历史）
 * - .story-system/MASTER_SETTING.json （合同源文件，改写即绕过 story_system_engine）
 *
 * 禁用方式：设置环境变量 WEBNOVEL_DISABLE_WRITE_GUARD=1
 *
 * ── 为什么是双入口 ──
 * OpenCode 2.0 换了插件 API，**V1 插件实现在 V2 里根本不会被加载**
 * （https://opencode.ai/v2/docs/build/plugins/migrate-v1）。两代形态：
 *
 *   V1: export default async () => ({ 'tool.execute.before': (input, output) => {} })
 *   V2: export default { id, setup(ctx) { ctx.tool.hook('execute.before', event => {}) } }
 *
 * 官方给的并存写法是同一个 default export 上同时提供 `setup`（V2 调用）与
 * `server`（V1 调用），前提是 OpenCode >= 1.18.29。本机当前装的是 1.18.34，
 * 满足该前提：升级前守卫照常工作，升级后也不会静默失效（V1 插件在 V2 里
 * 不报错、只是不加载——那才是真正危险的失败模式）。
 *
 * 有意**不** `import { Plugin } from '@opencode/plugin'`：该包在本机并未安装
 * （只有 @kilocode/plugin 与全局 opencode-ai CLI），静态 import 解析失败会让
 * 整个插件在 V1 下也加载不了——等于把"升级后失效"换成"现在就崩"。V2 文档对
 * 默认导出的要求是"带 id 与 setup(ctx) 的定义"，属形状要求；`Plugin.define`
 * 是 TS 侧的书写便利，本文件是 .js，不需要类型。
 */

const PROTECTED_SUFFIXES = [
  '.webnovel/state.json',
  '.webnovel/index.db',
  '.webnovel/vectors.db',
  '.webnovel/memory_scratchpad.json',
  '.story-system/commits/',
  '.story-system/events/',
  '.story-system/master_setting.json',
]

const ALLOWED_MARKERS = [
  'webnovel.py',
  'chapter-commit',
  'write-gate',
  'projections retry',
  'projections replay',
  'chapter_commit_service',
  'state_projection_writer',
  'atomic_write_json',
]

export const PLUGIN_ID = 'webnovel.write-guard'

function isDisabled() {
  return (
    process.env.WEBNOVEL_DISABLE_WRITE_GUARD === '1' ||
    process.env.WEBNOVEL_DISABLE_WRITE_GUARD === 'true'
  )
}

function isProtected(filePath) {
  if (!filePath) return false
  const normalized = filePath.replace(/\\/g, '/').toLowerCase()
  return PROTECTED_SUFFIXES.some(suffix => normalized.includes(suffix))
}

function hasAllowedMarker(command) {
  if (!command) return false
  const lower = command.toLowerCase()
  return ALLOWED_MARKERS.some(marker => lower.includes(marker))
}

/**
 * 核心判定，命中即 throw，由宿主拒绝该次工具调用。
 * 两代 hook 只在入参形状上不同：
 *   V1 → tool: input.tool,  args: output.args
 *   V2 → tool: event.tool, args: event.input
 */
export function guard(tool, args) {
  if (isDisabled()) return
  const name = String(tool || '').toLowerCase()
  const a = args || {}

  // 拦截 Write/Edit 工具
  if (name === 'write' || name === 'edit') {
    const path = a.path || a.file_path || ''
    if (isProtected(path)) {
      throw new Error(
        `🚫 禁止直接写入 ${path}。` +
        `这些文件只能通过 CLI 命令写入：\n` +
        `  python webnovel.py chapter-commit\n` +
        `  python webnovel.py state set-chapter-status\n` +
        `如需临时禁用此保护，设置环境变量 WEBNOVEL_DISABLE_WRITE_GUARD=1`
      )
    }
  }

  // 拦截 Bash 工具中的直接文件写入
  if (name === 'bash' || name === 'shell') {
    const command = a.command || ''
    const lower = command.toLowerCase()

    // 关键：先判定是否写入受保护路径，再判定是否 CLI 白名单。
    // 旧实现先 hasAllowedMarker 就 return——形如
    //   python webnovel.py status > .webnovel/state.json
    // 的命令含 'webnovel.py' 标记会被直接放行，重定向却把受保护文件
    // 覆盖掉。改为：含受保护路径 + 写操作意图时，无论是否有标记都拦截。
    let writesProtected = false
    for (const suffix of PROTECTED_SUFFIXES) {
      const normalizedSuffix = suffix.replace(/\\/g, '/').toLowerCase()
      if (lower.includes(normalizedSuffix)) {
        // 写操作意图：重定向(>/>>) 或 常见写命令
        if (lower.includes('>') || /\b(write|tee|mv|cp|rm|del|erase|rename)\b/.test(lower)) {
          writesProtected = true
        }
        break
      }
    }
    if (writesProtected) {
      throw new Error(
        `🚫 禁止通过 Bash 直接写入受保护文件。` +
        `请使用 CLI 命令：python webnovel.py chapter-commit`
      )
    }
    // 仅放行明显走 CLI 白名单且不含受保护路径写入的命令
    if (hasAllowedMarker(command)) return
  }
}

export default {
  id: PLUGIN_ID,

  // ── OpenCode V2 ──
  async setup(ctx) {
    await ctx.tool.hook('execute.before', (event) => {
      guard(event.tool, event.input)
    })
  },

  // ── OpenCode V1（>= 1.18.29 支持 object 入口）──
  async server() {
    return {
      'tool.execute.before': async (input, output) => {
        guard(input.tool, output.args)
      },
    }
  },
}

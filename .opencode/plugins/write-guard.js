/**
 * OpenCode Write Guard Plugin
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
 *
 * 禁用方式：设置环境变量 WEBNOVEL_DISABLE_WRITE_GUARD=1
 */

const PROTECTED_SUFFIXES = [
  '.webnovel/state.json',
  '.webnovel/index.db',
  '.webnovel/vectors.db',
  '.webnovel/memory_scratchpad.json',
  '.story-system/commits/',
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

export default async function ({ project }) {
  // 环境变量禁用开关
  if (process.env.WEBNOVEL_DISABLE_WRITE_GUARD === '1' ||
      process.env.WEBNOVEL_DISABLE_WRITE_GUARD === 'true') {
    return {}
  }

  return {
    'tool.execute.before': async (input, output) => {
      const tool = input.tool?.toLowerCase()

      // 拦截 Write/Edit 工具
      if (tool === 'write' || tool === 'edit') {
        const path = output.args?.path || output.args?.file_path || ''
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
      if (tool === 'bash') {
        const command = output.args?.command || ''
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
  }
}

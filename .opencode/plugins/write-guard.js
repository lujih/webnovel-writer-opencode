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
 * 有意**不** `import { Plugin } from '@opencode/plugin'`。原因为静态 import
 * 解析失败会让整个插件在 V1 下也加载不了——等于把"升级后失效"换成"现在就崩"。
 * V2 文档对默认导出的要求是"带 id 与 setup(ctx) 的定义"，属形状要求；`Plugin.define`
 * 是 TS 侧的书写便利，本文件是 .js，不需要类型。
 *
 * ⚠️ 该包现已**会被自动装上**：2026-10-04 实测，只要在项目里跑过一次 opencode，
 *     `.opencode/package.json` 就会被加上 `"@opencode-ai/plugin"`（与 lock 同步），
 *     回退后下次运行又出现，无法持久去除。所以上面那句"该包未安装"已不成立——
 *     保留不用它的理由只剩"少一个 import 依赖"，而非"装不上"。
 *     即便已安装也不用：形状要求用原生 default export 就满足，引入它没有收益。
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

/**
 * 会写文件的工具名。
 *
 * ⚠️ `patch` 是**内置工具**，不是第三方扩展：官方工具列表里它与 write/edit 并列，
 * 文档明写「`patch` 工具由 `edit` 权限控制，该权限涵盖所有文件修改操作
 * （`edit`、`write`、`patch`）」。旧实现只匹配 `write`/`edit`，于是
 * 「用 patch 改 state.json」整条路径不经过任何判定。
 */
const FILE_WRITE_TOOLS = new Set([
  'write', 'edit', 'patch', 'multiedit', 'apply_patch', 'str_replace_editor',
])

/**
 * 路径参数可能落在这些键上，**不同宿主/版本不一致**。
 *
 * 官方 plugins 文档里 `.env` 保护示例用的是 `output.args.filePath`（大驼峰）；
 * 本仓库的测试一直只喂 `path` / `file_path`。旧实现只读后两个，等于对
 * `filePath` 完全失明——而它很可能是真实宿主传的那个。
 * 多个都读是**单调收紧**：多读一个键只会多拦，不会放过原本拦得住的。
 */
const PATH_KEYS = ['filePath', 'file_path', 'path', 'filepath', 'filename', 'fileName']

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

/**
 * 从工具参数里抽出**目标路径**候选（不含正文）。
 *
 * 判据是「这个字符串描述的是要写到哪，而不是要写什么」：
 *
 * - 已知路径键（PATH_KEYS）——直接采纳；
 * - `patch` 的补丁正文（`patchText` / `patch` / `diff`）——虽然整体是正文，
 *   但补丁头 `*** Update File: <path>` / `--- a/<path>` / `+++ b/<path>`
 *   明确指定目标，必须解析出来，否则 patch 完全绕过守卫；
 * - 其余键一律**不扫**。早先的实现把所有字符串都当候选，结果「写一个正文里
 *   提到 `.webnovel/state.json` 的文档」会被误拦——那是误杀，用户会干脆
 *   设 WEBNOVEL_DISABLE_WRITE_GUARD=1 把整层保护关掉，比漏拦更糟。
 *
 * 取舍：宁可漏掉形态完全未知的写工具，也不误伤正常写作与改文档。
 */
const CONTENT_KEYS = new Set([
  'content', 'newString', 'new_string', 'oldString', 'old_string',
  'text', 'body', 'message', 'description', 'reason', 'prompt',
])

const PATCH_KEYS = ['patchText', 'patch', 'diff', 'patch_text']

// 补丁头里提取目标路径：*** Update File: /a/b、--- a/b、+++ b/c
const PATCH_HEADER_RE = /(?:^\*\*\* (?:Update|Add|Delete) File:\s*|\+\+\+\s+(?:b\/)?|---\s+(?:a\/)?)(\S+)/gm

function extractPatchTargets(patchText) {
  const out = []
  if (typeof patchText !== 'string') return out
  let m
  PATCH_HEADER_RE.lastIndex = 0
  while ((m = PATCH_HEADER_RE.exec(patchText)) !== null) {
    if (m[1]) out.push(m[1])
  }
  return out
}

function collectPathCandidates(args, depth = 0) {
  const out = []
  if (!args || typeof args !== 'object' || depth > 4) return out

  for (const key of PATH_KEYS) {
    const v = args[key]
    if (typeof v === 'string' && v) out.push(v)
  }
  for (const key of PATCH_KEYS) {
    const v = args[key]
    if (typeof v === 'string' && v) out.push(...extractPatchTargets(v))
  }
  for (const [key, value] of Object.entries(args)) {
    if (PATH_KEYS.includes(key) || PATCH_KEYS.includes(key)) continue
    if (CONTENT_KEYS.has(key)) continue // 正文不是目标，别误伤
    if (value && typeof value === 'object') {
      // 嵌套载荷（multiedit 的 edits[] 等）继续找路径键
      out.push(...collectPathCandidates(value, depth + 1))
    }
  }
  return out
}

function hasAllowedMarker(command) {
  if (!command) return false
  const lower = command.toLowerCase()
  return ALLOWED_MARKERS.some(marker => lower.includes(marker))
}

/**
 * 判断一条 shell 命令是否**有写入意图**（传进来的已是小写）。
 *
 * 旧实现只有 `>` 与 `\b(write|tee|mv|cp|rm|del|erase|rename)\b`，实测漏掉：
 *   - PowerShell `Set-Content` / `Out-File` / `Add-Content`
 *   - 解释器内写文件 `node -e "require('fs').writeFileSync(...)"`、`python -c "...open(...,'w')"`
 *   - `sed -i` / `perl -pi` / `truncate` / `dd of=`
 * 判据只在**命中受保护路径之后**才被执行，所以宽松一点不会误伤正常写作命令。
 */
function hasWriteIntent(lower) {
  if (!lower) return false
  if (lower.includes('>')) return true // 重定向 / >> / PowerShell 也用它
  if (/\b(write|tee|mv|cp|rm|del|erase|rename|move|copy)\b/.test(lower)) return true
  // PowerShell cmdlet
  if (/\b(set-content|add-content|out-file|clear-content|new-item|remove-item|rename-item|move-item|copy-item)\b/.test(lower)) return true
  // 解释器内写盘：writeFileSync / writeFile / appendFile / createWriteStream / fs.write
  if (/(writefilesync|writefile|appendfile|createwritestream|fs\.write|open\([^)]*['"][wa])/.test(lower)) return true
  // 就地编辑 / 截断 / dd
  if (/\b(sed\s+-i|perl\s+-pi|truncate)\b/.test(lower)) return true
  // `dd ... of=` 的末尾是 `=`，后面跟非单词字符时 `\b` 不成立，不能加尾锚点
  if (/\bdd\s+[^|]*\bof=/.test(lower)) return true
  return false
}

/**
 * 诊断开关：把本次工具调用的参数**键名**打到 stderr。
 *
 * 之所以需要它：`filePath` vs `path` 这个键名决定守卫是否真正生效，
 * 但只有**真实宿主**能给出最终答案（本仓库没有可运行的 OpenCode 二进制）。
 * 在真机上设 WEBNOVEL_WRITE_GUARD_DEBUG=1 跑一次 Edit，看 stderr 打印的键名
 * 即可确证。不设该变量时零输出、零开销。
 */
function debugLog(tool, args) {
  if (process.env.WEBNOVEL_WRITE_GUARD_DEBUG !== '1') return
  let keys = []
  try {
    keys = args && typeof args === 'object' ? Object.keys(args) : []
  } catch {
    keys = ['<unreadable>']
  }
  try {
    process.stderr.write(
      `[write-guard] tool=${JSON.stringify(tool)} argKeys=${JSON.stringify(keys)}\n`)
  } catch {
    // stderr 不可写时不能让守卫失效
  }
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

  debugLog(tool, a)

  // 拦截所有会写文件的工具（write / edit / patch / ...）
  if (FILE_WRITE_TOOLS.has(name)) {
    const candidates = collectPathCandidates(a)
    const hit = candidates.find(p => isProtected(p))
    if (hit) {
      throw new Error(
        `🚫 禁止直接写入 ${hit}。` +
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
        if (hasWriteIntent(lower)) {
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

""".opencode/opencode.json 与 .opencode/commands/ 的 OpenCode 契约。

每条断言都对应一个**已实测**的结论，不是防御式编程：

1. 配置不得含 V2 `permissions`——实测 2026-10-04（opencode 1.18.34
   `opencode debug config`）：V1 遇到该键以 rc=1 **拒绝整份配置**，
   "V2 permissions are not supported by OpenCode V1"，所有 V1 用户无法启动。
   同次实测：agent frontmatter 里的 V2 `permissions` 只是被静默忽略，不致命。
2. $schema 不写——实测 2026-10-01，https://opencode.ai/config.json 返回的仍是
   V1 形状（顶层 additionalProperties: false，只有 permission/agent/command/
   plugin/snapshot/attachment/provider 这些 V1 键），挂上去只会把正确的 V2 字段
   全标红。哪天它改成 V2 了，本测试会失败并提醒去掉这条禁令。
3. instructions 不写——V2 文档原文 "OpenCode accepts this field but does not
   load its entries; use AGENTS.md for instructions"，是静默空操作。
4. formatter 必须 false——依据 V2 文档，内置 prettier/biome 都覆盖 .md，
   而本书 65 章正文全是 .md；formatter 在写盘**之后**才跑，拦不住，
   只会把用户的作品重排。（文档转述，未在本书正文上实测。）
5. 斜杠命令的 `!` shell 块绕过工具权限流（V2 commands 文档的 Warning），
   因此只允许只读子命令出现在 shell 块里。
6. AGENTS.md 必须真的在 git 里——.gitignore 曾把它当 legacy 产物忽略掉，
   全新 clone 下 OpenCode V2 就看不到任何项目说明，而 --check 门禁反而通过。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG = REPO_ROOT / ".opencode" / "opencode.json"
COMMANDS_DIR = REPO_ROOT / ".opencode" / "commands"

sys.path.insert(0, str(REPO_ROOT / ".opencode" / "scripts"))


def _strip_jsonc(text: str) -> dict:
    """去行注释 + 去尾逗号。OpenCode 的 schema 声明 allowComments/allowTrailingCommas。"""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


@pytest.fixture(scope="module")
def config():
    return _strip_jsonc(CONFIG.read_text(encoding="utf-8"))


class TestConfigParses:
    def test_is_valid_jsonc(self, config):
        assert isinstance(config, dict)

    def test_native_v2_keys(self, config):
        """配置只出现 V2 原生键（本项目已放弃 V1 兼容）。

        历史：2026-10 时这里断言的是「只允许 V1 与 V2 都能加载的 4 个键」，
        因为 opencode 1.18.34 遇到 V2 `permissions` 会 rc=1 拒绝**整份**配置。
        现在改为 V2-only：`$schema` + `permissions` 是 V2 的核心，V1 用户请用
        v3.1.x 及更早版本。
        """
        allowed = {"$schema", "permissions", "formatter", "compaction", "watcher", "tool_output"}
        assert set(config) == allowed, (
            f"出现未列入 V2 白名单的键: {set(config) - allowed}。"
            "新增前请对照 https://opencode.ai/config.json 确认形状。"
        )

    def test_schema_points_at_official_v2_schema(self, config):
        """`$schema` 必须是官方地址——编辑器校验与自动补全靠它。

        历史：此前刻意不写 `$schema`，理由是 2026-10-01 抓到的
        https://opencode.ai/config.json 仍是 V1 形状（顶层
        additionalProperties:false）。该判断已过期：官方 config 页现在把
        `$schema` 作为推荐写法，且示例里包含 permissions/agents/skills 等 V2 键。
        """
        assert config.get("$schema") == "https://opencode.ai/config.json"


class TestNativeV2Shape:
    """顶层的 `permissions` 必须存在且形状正确。

    这是 SSOT 写保护的**第一层**（第二层是 write-guard.js 的运行时拦截，
    第三层是 6 个 agent 各自的 permissions）。

    历史：2026-10-04 实测 opencode 1.18.34 遇到 V2 `permissions` 会 rc=1
    **拒绝整份配置**，所以当时把它移除了，只留 write-guard 兜底。现在配置
    改为 V2-only，这一层得以恢复——main agent 也能在权限判定阶段看到 deny，
    而不是只靠运行时抛错。
    """

    SSOT_PATHS = (
        "*.webnovel/state.json",
        "*.webnovel/index.db",
        "*.webnovel/vectors.db",
        "*.webnovel/memory_scratchpad.json",
        "*.story-system/events/",
        "*.story-system/commits/",
        "*.story-system/master_setting.json",
    )

    def test_permissions_present(self, config):
        assert "permissions" in config, "顶层 permissions 缺失"
        rules = config["permissions"]
        assert isinstance(rules, list) and rules, "permissions 必须是非空数组"

    def test_every_rule_has_three_string_fields(self, config):
        for i, rule in enumerate(config["permissions"]):
            assert set(rule) == {"action", "resource", "effect"}, f"第 {i} 条规则字段不对: {rule}"
            for key, value in rule.items():
                assert isinstance(value, str) and value, f"第 {i} 条规则的 {key} 必须是非空字符串"
            assert rule["effect"] in {"allow", "deny", "ask"}, f"第 {i} 条 effect 非法: {rule}"

    def test_uses_v2_action_names(self, config):
        """V1 的 action 名（bash/task/write/patch）在 V2 里已改名。"""
        legacy = {"bash", "task", "write", "patch"}
        used = {r["action"] for r in config["permissions"]}
        assert not (used & legacy), f"出现 V1 action 名: {sorted(used & legacy)}（应为 shell/subagent/edit）"

    def test_every_ssot_path_is_denied_globally(self, config):
        denied = {(r["action"], r["resource"]) for r in config["permissions"] if r["effect"] == "deny"}
        missing = [p for p in self.SSOT_PATHS if ("edit", p) not in denied]
        assert not missing, (
            f"这些 SSOT 路径没有全局 edit deny: {missing}。"
            "缺少它们时，写保护只剩 write-guard 的运行时兜底。"
        )

    def test_external_directory_restores_ask(self, config):
        """V2 默认**允许**访问项目外；本项目依赖仓库外的书项目，故恢复 ask。"""
        ask_rules = [r for r in config["permissions"]
                     if r["action"] == "external_directory" and r["effect"] == "ask"]
        assert ask_rules, "external_directory 未设为 ask——V2 默认放行，会静默读取仓库外任意路径"

    def test_ssot_guard_still_exists(self):
        """移除全局 permissions 后，SSOT 写保护不能同时消失。

        实际执行层是 write-guard.js 的 tool hook：与 agent 无关、对主 agent 也
        生效、V1/V2 都可用。若这个文件也没了，SSOT 就真的没有运行时拦截了。
        """
        plugin = (REPO_ROOT / ".opencode" / "plugins" / "write-guard.js")
        text = plugin.read_text(encoding="utf-8")
        assert "tool.execute.before" in text, "write-guard 的拦截 hook 不见了"
        assert "ctx.tool.hook('execute.before'" in text.replace('"', "'"), \
            "write-guard 缺少 V2 的 setup 入口"


class TestAgentFrontmatterIsV1Safe:
    """子 agent 的 V2 permissions 在 V1 下是静默忽略，不得是致命错误。

    实测：`.opencode/agents/*.md` 带 V2 `permissions` frontmatter 时，
    `opencode debug agent <name>` 正常返回，解析出的 permission 是 V1 默认集
    （102 条），不是我们写的 V2 规则。也就是说：V2 用户拿到前端拦截，
    V1 用户安全降级到运行时 hook，两边都不报错。
    """

    AGENTS = ("context-agent", "observer-agent", "chapter-writer-agent",
              "data-agent", "reviewer", "deconstruction-agent")

    def test_agents_still_use_v2_permissions(self):
        """前提条件：子 agent 仍保留 V2 规则（前端拦截层）。"""
        for name in self.AGENTS:
            path = REPO_ROOT / ".opencode" / "agents" / f"{name}.md"
            text = path.read_text(encoding="utf-8")
            assert "permissions:" in text, f"{name}.md 丢掉了 permissions 段"


class TestAgentsMdIsActuallyCommitted:
    """AGENTS.md 必须真的在 git 里。

    实测踩过的坑：`.gitignore` 曾把 `AGENTS.md` 当作 "Legacy Claude workspace
    artifact" 忽略掉，于是全新 clone 下这个文件根本不存在——OpenCode V2 只
    发现 AGENTS.md，等于整套项目说明对模型完全不可见，而 `sync-agents-md
    --check` 在"缺失"时才会报 stale，CI 于是**误判通过**。光有门禁不够，
    得确认它进得了版本库。
    """

    def test_not_gitignored(self):
        # 注意：不能用 `git check-ignore` 判断——文件一旦被跟踪，gitignore 对它
        # 就完全无效，check-ignore 会返回"未忽略"，即使 .gitignore 里写着规则。
        # 那正是原 bug 难被发现的原因：本地一切正常，只有全新 clone 才暴露。
        # 这里直接读 .gitignore 的实际规则。
        ignore_file = REPO_ROOT / ".gitignore"
        patterns = [
            line.strip()
            for line in ignore_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert "AGENTS.md" not in patterns, (
            ".gitignore 仍忽略 AGENTS.md——一旦取消跟踪，全新 clone 下 "
            "OpenCode V2 就看不到任何项目说明"
        )

    def test_tracked_by_git(self):
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "AGENTS.md"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, (
            "AGENTS.md 未被 git 跟踪；.gitignore 里的忽略规则已移除，"
            "请确认 git add 过"
        )

    def test_matches_claude_md(self):
        """内容逐字节一致，避免两套说明互相矛盾。"""
        claude = REPO_ROOT / "CLAUDE.md"
        agents = REPO_ROOT / "AGENTS.md"
        assert agents.is_file(), "AGENTS.md 缺失"
        assert agents.read_bytes() == claude.read_bytes(), (
            "AGENTS.md 与 CLAUDE.md 不一致——运行 "
            "`python .opencode/scripts/webnovel.py sync-agents-md`"
        )


class TestSyncAgentsMdCommand:
    """门禁行为：缺失/漂移必须报错，不能静默通过。"""

    @pytest.fixture(autouse=True)
    def _importable(self):
        from data_modules import sync_agents_md  # noqa: F401

    def _run(self, tmp_path, monkeypatch, *argv):
        from data_modules import sync_agents_md

        monkeypatch.setattr(sync_agents_md, "CLAUDE_MD", tmp_path / "CLAUDE.md")
        monkeypatch.setattr(sync_agents_md, "AGENTS_MD", tmp_path / "AGENTS.md")
        monkeypatch.setattr(sys, "argv", ["sync-agents-md", *argv])
        return sync_agents_md.main()

    def test_missing_agents_md_fails_check(self, tmp_path, monkeypatch):
        (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
        assert self._run(tmp_path, monkeypatch, "--check") == 1, (
            "AGENTS.md 缺失时 --check 必须返回 1，否则 CI 会误判通过"
        )

    def test_generates_then_is_idempotent(self, tmp_path, monkeypatch):
        (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
        assert self._run(tmp_path, monkeypatch) == 0
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "x"
        assert self._run(tmp_path, monkeypatch, "--check") == 0

    def test_drift_is_detected(self, tmp_path, monkeypatch):
        (tmp_path / "CLAUDE.md").write_text("new", encoding="utf-8")
        assert self._run(tmp_path, monkeypatch) == 0
        assert self._run(tmp_path, monkeypatch, "--check") == 0
        (tmp_path / "AGENTS.md").write_text("stale", encoding="utf-8")
        assert self._run(tmp_path, monkeypatch, "--check") == 1

    def test_missing_source_fails(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch) == 1


class TestSchemaDeclaration:
    """配置**必须**声明官方 `$schema`（V2 已把它作为推荐写法）。

    历史：2026-10-01 实测 https://opencode.ai/config.json 返回的仍是 V1 形状
    （顶层 additionalProperties:false），挂上去会让编辑器把正确的 V2 字段标红，
    所以当时刻意不写，并断言"仓库副本不含 $schema"。

    该判断现已过期：官方 config 页把 `$schema` 写进所有示例，且示例本身包含
    permissions/agents/skills 等 V2 键。保留旧断言会让配置与规范脱节，故反转。
    """

    def test_repository_copy_declares_official_schema(self, config):
        assert config.get("$schema") == "https://opencode.ai/config.json", (
            "opencode.json 必须声明官方 $schema，否则编辑器无法校验 V2 键"
        )


class TestNoSilentNoOpInstructions:
    def test_instructions_key_absent(self, config):
        """本项目不使用 `instructions`。

        ⚠️ 理由已更新：V2 **确实会加载** instructions（/docs/config#instructions），
        此前"V2 不加载、是静默空操作"的说法是错的。这里保留断言只是因为这个
        项目把规则集中在单一 AGENTS.md，不需要额外文件清单——不是因为该键无效。
        """
        assert "instructions" not in config, (
            "本项目未使用 instructions；若确实要引入，请同时更新 CLAUDE.md 的说明"
        )


class TestFormatterOff:
    def test_formatter_is_false(self, config):
        """内置 prettier/biome 覆盖 .md；本书 65 章正文全是 .md。"""
        assert config["formatter"] is False, (
            "formatter 会重排 .md 正文——那是改用户的作品，且写盘后才跑拦不住"
        )


class TestSsotGuardSurvivesPermissionsRemoval:
    """全局 permissions 被移除后，SSOT 写保护必须仍然成立。

    2026-10-04 之前，配置里有一段全局 `permissions`：6 条 SSOT 路径 edit deny +
    external_directory/read 的 ask。它覆盖**所有** agent（含主 agent），是
    三层防护的第①层。

    移除原因：V1 见到该键会拒绝加载整份配置（见 TestConfigDoesNotBreakV1）。
    移除代价：V2 的主 agent 不再"看不见" SSOT 路径的 edit 工具。

    但保护本身没消失——第③层 write-guard.js 挂的是全局 tool hook，与 agent 无关、
    对主 agent 生效、V1/V2 都能跑。所以这里钉的是"兜底层不许被一起删掉"。
    """

    # 权威清单在 write-guard.js 的 PROTECTED_SUFFIXES 里——**不要**在这里抄一份。
    # 实测教训：早先按配置里的 glob 写法（`*.story-system/events/*`）抄过来，
    # 而插件实际用的是后缀匹配（`.story-system/events/`），还漏了 commits/，
    # 结果测试自己造出一个不存在的清单。
    LIST_RE = re.compile(
        r"PROTECTED_SUFFIXES\s*=\s*\[(.*?)\]", re.S)

    def _guard_text(self):
        return (REPO_ROOT / ".opencode" / "plugins" / "write-guard.js") \
            .read_text(encoding="utf-8")

    def test_write_guard_has_protected_list(self):
        assert self.LIST_RE.search(self._guard_text()), \
            "write-guard 找不到 PROTECTED_SUFFIXES 清单"

    def test_protected_list_covers_ssot(self):
        """兜底层必须覆盖 SSOT 的全部落盘位置，否则移除全局 permissions 即净损失。"""
        m = self.LIST_RE.search(self._guard_text())
        entries = set(re.findall(r"'([^']+)'", m.group(1)))
        required = {
            ".webnovel/state.json",
            ".webnovel/index.db",
            ".webnovel/vectors.db",
            ".webnovel/memory_scratchpad.json",
            ".story-system/events/",
            ".story-system/master_setting.json",
        }
        missing = required - entries
        assert not missing, f"write-guard 未覆盖: {sorted(missing)}"

    def test_write_guard_is_agent_agnostic(self):
        """兜底层必须对主 agent 也生效，否则移除全局 permissions 就是净损失。"""
        text = self._guard_text()
        assert "tool.execute.before" in text, "缺少 V1 的运行时拦截 hook"
        assert "ctx.tool.hook('execute.before'" in text.replace('"', "'"), \
            "缺少 V2 的 setup 入口"
        # guard() 只看 (tool, args)，不应按 agent 分流——它是全局 tool hook
        start = text.find("export function guard")
        assert start != -1, "找不到共享的 guard()"
        signature = text[start:text.find(")", start)]
        assert "agent" not in signature, f"guard() 签名不应依赖 agent: {signature}"


class TestCompaction:
    def test_no_v1_tail_turns_or_prune(self, config):
        """V2 会忽略 tail_turns/prune 并告警（migrate-v1）。"""
        assert "tail_turns" not in config["compaction"]
        assert "prune" not in config["compaction"]

    def test_uses_keep_tokens(self, config):
        assert config["compaction"]["keep"]["tokens"] > 0


class TestWatcherIgnoresNoise:
    def test_ignores_pycache_and_node_modules(self, config):
        ignores = " ".join(config["watcher"]["ignore"])
        assert "__pycache__" in ignores
        assert "node_modules" in ignores


class TestSlashCommands:
    COMMAND_FILES = sorted(COMMANDS_DIR.glob("*.md"))

    def test_at_least_one_command_exists(self):
        assert self.COMMAND_FILES, ".opencode/commands/ 是空的"

    @pytest.mark.parametrize("path", COMMAND_FILES, ids=lambda p: p.name)
    def test_has_description_frontmatter(self, path):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
        assert m, f"{path.name} 缺少 frontmatter"
        assert "description:" in m.group(1), f"{path.name} 缺少 description"

    @pytest.mark.parametrize("path", COMMAND_FILES, ids=lambda p: p.name)
    def test_shell_blocks_only_run_readonly_verbs(self, path):
        """V2 文档 Warning：shell 块在 agent 的工具权限流**之外**执行，
        write-guard.js 拦不到它。所以只允许只读子命令。"""
        text = path.read_text(encoding="utf-8")
        for cmd in re.findall(r"webnovel\.py\s+([a-z-]+)", text):
            assert cmd not in DESTRUCTIVE, (
                f"{path.name} 在绕过权限的 shell 块里调用了破坏性命令 {cmd!r}；"
                "写路径必须走 skill + agent permissions + write-guard 链路"
            )

    @pytest.mark.parametrize("path", COMMAND_FILES, ids=lambda p: p.name)
    def test_shell_block_uses_repo_relative_path(self, path):
        """shell 块在「active project location」执行，路径必须相对仓库根。"""
        text = path.read_text(encoding="utf-8")
        for m in re.findall(r"webnovel\.py", text):
            pass
        assert "${" not in text.split("```")[0] if "```" in text else True
        for block in re.findall(r"!`([^`]*)`", text):
            assert "webnovel.py" not in block or \
                ".opencode/scripts/webnovel.py" in block, (
                    f"{path.name} 的 shell 块没用仓库相对路径：{block[:60]}")


DESTRUCTIVE = {
    "delete-chapters", "rebuild", "chapter-commit", "publish", "export",
    "backup", "heal", "rewrite", "init", "archive", "entity-clean",
    "migrate",
}
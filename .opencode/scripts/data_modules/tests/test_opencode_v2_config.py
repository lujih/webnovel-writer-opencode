""".opencode/opencode.json 与 .opencode/commands/ 的 OpenCode V2 契约。

每条断言都对应一个**已实测**的 v2 文档结论，不是防御式编程：

1. $schema 不写——实测 2026-10-01，https://opencode.ai/config.json 返回的仍是
   V1 形状（顶层 additionalProperties: false，只有 permission/agent/command/
   plugin/snapshot/attachment/provider 这些 V1 键），挂上去只会把正确的 V2 字段
   全标红。哪天它改成 V2 了，本测试会失败并提醒去掉这条禁令。
2. instructions 不写——V2 文档原文 "OpenCode accepts this field but does not
   load its entries; use AGENTS.md for instructions"，是静默空操作。
3. formatter 必须 false——内置 prettier/biome 都覆盖 .md，而本书 65 章正文
   全是 .md；formatter 在写盘**之后**才跑，拦不住，只会把用户的作品重排。
4. 斜杠命令的 `!` shell 块绕过工具权限流（V2 commands 文档的 Warning），
   因此只允许只读子命令出现在 shell 块里。
5. AGENTS.md 必须真的在 git 里——.gitignore 曾把它当 legacy 产物忽略掉，
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

    def test_only_v2_keys(self, config):
        """V1 形状的键混进来会被静默警告或直接忽略（migrate-v1「Accepted but
        unsupported fields」），所以只允许列出的这几个。"""
        assert set(config) == {
            "permissions", "formatter", "compaction", "watcher", "tool_output",
        }, f"意外的键: {set(config) - {'permissions','formatter','compaction','watcher','tool_output'}}"


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
            cwd=REPO_ROOT, capture_output=True, text=True)
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


class TestNoStaleSchema:
    def test_does_not_reference_published_schema(self, config):
        """实测该地址仍是 V1 形状 schema；挂着只会让编辑器把 V2 字段标红。

        只看真实键，不看注释——配置文件里刻意写了解释为什么**不**加
        $schema 的那段散文，它当然包含这个 URL 字符串。
        """
        assert "$schema" not in config, (
            "发布的 schema 已更改为 V2 形状，请重新评估并移除本文件顶部的禁令——"
            "见 test_opencode_v2_config.py 顶部说明"
        )


class TestNoSilentNoOpInstructions:
    def test_instructions_key_absent(self, config):
        assert "instructions" not in config, (
            "V2 不加载 instructions 条目，是静默空操作；"
            "项目说明的唯一真实通道是 AGENTS.md"
        )


class TestFormatterOff:
    def test_formatter_is_false(self, config):
        """内置 prettier/biome 覆盖 .md；本书 65 章正文全是 .md。"""
        assert config["formatter"] is False, (
            "formatter 会重排 .md 正文——那是改用户的作品，且写盘后才跑拦不住"
        )


class TestGlobalPermissions:
    def test_allows_by_default_like_v2_base(self, config):
        """与 V2 基础策略一致：* → allow，主 agent 不会被过度收紧。"""
        assert config["permissions"][0] == {
            "action": "*", "resource": "*", "effect": "allow"}

    def test_external_directory_stays_ask(self, config):
        """书项目在仓库之外，恢复基础策略里的询问。"""
        rules = config["permissions"]
        assert {"action": "external_directory", "resource": "*",
                "effect": "ask"} in rules

    def test_env_read_stays_ask(self, config):
        assert {"action": "read", "resource": "*.env", "effect": "ask"} in \
            config["permissions"]

    def test_ssot_edits_denied(self, config):
        """主 agent 也必须碰不到 SSOT——之前只有子 agent 和 plugin 两层。"""
        denied = {r["resource"] for r in config["permissions"]
                  if r["action"] == "edit" and r["effect"] == "deny"}
        for suffix in (
            ".webnovel/state.json",
            ".webnovel/index.db",
            ".webnovel/vectors.db",
            ".webnovel/memory_scratchpad.json",
            ".story-system/events/*",
            ".story-system/master_setting.json",
        ):
            assert f"*{suffix}" in denied, f"全局权限漏了 {suffix}"

    def test_ssot_denies_come_after_allows(self, config):
        """最后命中者生效：edit 的 allow 排在 deny 前面，deny 才有意义。"""
        rules = config["permissions"]
        allow_at = next(i for i, r in enumerate(rules)
                        if r["action"] == "edit" and r["effect"] == "allow") \
            if any(r["action"] == "edit" and r["effect"] == "allow" for r in rules) \
            else -1
        deny_at = next(i for i, r in enumerate(rules)
                       if r["action"] == "edit" and r["effect"] == "deny")
        assert allow_at < deny_at, "SSOT deny 排在 edit allow 之前会被盖过"


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
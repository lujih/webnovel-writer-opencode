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
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG = REPO_ROOT / ".opencode" / "opencode.json"
COMMANDS_DIR = REPO_ROOT / ".opencode" / "commands"


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
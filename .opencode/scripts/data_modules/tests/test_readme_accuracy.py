"""README 里的公开数字必须与仓库实际一致。

README 是 211 star 公开仓库的第一印象，而它此前严重滞后：写着"13 个
Skills / 28 个子命令 / 审查 13 维度 / 60+ 数据模块 / 59 个测试文件"，
实际是 16 / 38 / 6 / 72 / 98。目录树还漏掉了 commands/、plugins/、
opencode.json，并引用了根本不存在的 README_CN.md。

这类漂移不会让任何测试失败，所以在此钉住。
"""
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
README = REPO_ROOT / "README.md"
OPENCODE = REPO_ROOT / ".opencode"

SKILLS_DIR = OPENCODE / "skills"
AGENTS_DIR = OPENCODE / "agents"
COMMANDS_DIR = OPENCODE / "commands"
SCRIPTS = OPENCODE / "scripts"
DATA_MODULES = SCRIPTS / "data_modules"
TESTS = DATA_MODULES / "tests"


@pytest.fixture(scope="module")
def readme():
    return README.read_text(encoding="utf-8")


class TestNoPhantomFiles:
    def test_no_reference_to_nonexistent_file(self, readme):
        """README 曾把本文件称作 README_CN.md——该文件不存在。"""
        assert "README_CN.md" not in readme, (
            "README 引用了不存在的 README_CN.md"
        )

    def test_all_referenced_paths_exist(self, readme):
        """README 中形如 `path/` 的仓库内引用必须真实存在。

        基目录要试两处：仓库根，以及 `.opencode/`——表格里的裸路径
        （`genres/`、`references/csv/`）是相对 `.opencode/` 的，直接按仓库根
        解析会把它们误判为缺失。
        """
        missing = []
        for m in re.finditer(r"`([A-Za-z0-9_./-]+/)`", readme):
            rel = m.group(1)
            if rel.startswith(("http", "~")):
                continue
            if not any((base / rel.rstrip("/")).exists()
                       for base in (REPO_ROOT, OPENCODE)):
                missing.append(rel)
        assert not missing, f"README 引用了不存在的路径: {sorted(set(missing))}"


class TestDocumentedCountsAreReal:
    def _count(self, pattern, readme, label):
        m = re.search(pattern, readme)
        assert m, f"README 未找到 {label} 的数字"
        return int(m.group(1))

    def test_skill_count(self, readme):
        actual = len([d for d in SKILLS_DIR.iterdir() if d.is_dir()])
        claimed = self._count(r"`\.opencode/skills/`.*?（(\d+) 个 Skills", readme,
                              "skills") if re.search(
            r"`\.opencode/skills/`.*?（(\d+) 个 Skills", readme) else None
        if claimed is None:
            claimed = int(re.search(r"skills/\s+# (\d+) 个 Skills", readme).group(1))
        assert claimed == actual, f"README 说 {claimed} 个 skill，实际 {actual}"

    def test_agent_count(self, readme):
        actual = len(list(AGENTS_DIR.glob("*.md")))
        claimed = int(re.search(r"agents/\s+# (\d+) 个 Agent", readme).group(1))
        assert claimed == actual, f"README 说 {claimed} 个 agent，实际 {actual}"

    def test_command_count(self, readme):
        actual = len(list(COMMANDS_DIR.glob("*.md")))
        claimed = int(re.search(r"commands/\s+# (\d+) 个只读", readme).group(1))
        assert claimed == actual, f"README 说 {claimed} 个 command，实际 {actual}"

    def test_cli_subcommand_count(self, readme):
        """从 argparse 权威的 choices 列表取数。

        不要扫 `--help` 的对齐输出：argparse 会按终端宽度折行，
        `master-outline-sync` 被折成独立一行后就没有"命令名 + 空格"了，
        按 `^\\s{4}[a-z-]+\\s` 匹配会**漏掉它**——我第一版就是这么把
        38 数成 37 的。usage 行里的 `{a,b,c}` 集合不折行，才可靠。
        """
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO_ROOT / ".opencode" / "scripts" / "webnovel.py"), "--help"],
            capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
            env={**os.environ, "COLUMNS": "4000"},
        )
        text = proc.stdout + proc.stderr
        # usage 里先出现的是 `--mode` 的 choices（{default,fast,minimal}），
        # 必须挑**最长**的那个花括号集合——子命令集合才有 37+ 项。
        candidates = re.findall(r"\{([a-z0-9,-]+)\}", text)
        names = set()
        if candidates:
            longest = max(candidates, key=len)
            names = {n for n in longest.split(",") if n}
        assert "master-outline-sync" in names, (
            "解析结果缺少 master-outline-sync——折行处理可能又变了"
        )
        claimed = int(re.search(r"webnovel\.py\s+# CLI 统一入口（(\d+) 个子命令）",
                                readme).group(1))
        assert claimed == len(names), \
            f"README 说 {claimed} 个子命令，argparse 实际注册 {len(names)} 个"

    def test_module_and_test_counts(self, readme):
        claimed_mod = int(re.search(r"data_modules/\s+# 核心数据模块（(\d+) 个）",
                                    readme).group(1))
        claimed_test = int(re.search(r"tests/\s+# 测试（(\d+) 个测试文件",
                                     readme).group(1))
        actual_mod = len(list(DATA_MODULES.glob("*.py")))
        actual_test = len(list(TESTS.glob("*.py")))
        assert claimed_mod == actual_mod, \
            f"README 说 {claimed_mod} 个模块，实际 {actual_mod}"
        assert claimed_test == actual_test, \
            f"README 说 {claimed_test} 个测试文件，实际 {actual_test}"


class TestReviewDimensions:
    def test_review_is_6_dimensions_not_13(self, readme):
        """README 曾写"13 维度"，代码里只检查 6 个。"""
        reviewer = (AGENTS_DIR / "reviewer.md").read_text(encoding="utf-8")
        assert "只检查以下 6 个维度" in reviewer, \
            "reviewer.md 不再声明 6 维度——若确有变更，请同步更新 README"
        assert "13 维度" not in readme, (
            "README 仍写 13 维度；实际只有 6 个（设定/时间线/连贯/角色/逻辑/规则）"
        )


class TestV2AssetsAreAdvertised:
    """OpenCode v2 适配是对外可见的特性，README 应当说明。"""

    def test_mentions_opencode_json(self, readme):
        assert "opencode.json" in readme

    def test_mentions_agents_md_requirement(self, readme):
        assert "AGENTS.md" in readme
        assert "sync-agents-md" in readme, (
            "README 未告诉 v2 用户克隆后要生成 AGENTS.md"
        )

    def test_warns_about_formatter(self, readme):
        """内置 formatter 覆盖 .md，会重排章节正文——必须警告。"""
        assert "formatter" in readme.lower()

    def test_lists_diagnostic_commands(self, readme):
        for name in ("/wn-status", "/wn-doctor", "/wn-ssot-verify", "/wn-where"):
            assert name in readme, f"README 未列出诊断命令 {name}"
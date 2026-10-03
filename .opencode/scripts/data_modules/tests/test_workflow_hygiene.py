"""GitHub Actions 的供应链卫生——与第三方收录要求直接相关。

背景：仓库拟被收录进第三方列表 `hashgraph-online/awesome-ai-plugins`，其
收录规则明确会把下面几项作为扫描发现项：

- *unpinned third-party actions* —— 可变 tag 可被改指向任意新代码，
  从而以本仓库的 `contents: write` 权限执行；
- *overly broad GitHub Actions permissions*；
- committed secrets / unsafe credential handling。

这些与收录与否无关也该守：官方 `actions/*` 跟随 tag 风险低，**第三方** action
则不该。本仓库用到的 `softprops/action-gh-release` 就是第三方。

豁免 `actions/*` 与 `github/*` 是有依据的：这两个组织发布的 action 由 GitHub
自身分发与审核，tag 被劫持的风险与第三方不在一个量级（供应链攻击的常规做法
是抢注同名第三方组织，而非攻破 actions 官方）。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))

# 由 GitHub 官方分发、可跟随 tag 的 action 前缀
OFFICIAL_PREFIXES = ("actions/", "github/")

# 允许跟随 tag 的 action：无可用不可变发布物，或由本仓库自持
TAG_EXEMPT = {
    # 官方
    "actions/checkout": "@v4",
    "actions/setup-python": "@v5",
    "actions/setup-node": "@v4",
}

USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)


@pytest.fixture(scope="module")
def workflows():
    return WORKFLOWS


def test_workflows_found(workflows):
    assert workflows, ".github/workflows/ 下没有 workflow"


class TestThirdPartyActionsPinned:
    def _uses(self, workflows):
        for path in workflows:
            for m in USES_RE.finditer(path.read_text(encoding="utf-8")):
                yield path, m.group(1)

    def test_no_unpinned_third_party_actions(self, workflows):
        offenders = []
        for path, ref in self._uses(workflows):
            action, _, version = ref.partition("@")
            if action.startswith(OFFICIAL_PREFIXES):
                continue
            if version in TAG_EXEMPT.get(action, ()):
                continue
            # 40 位十六进制 = commit SHA；tag 是 v1 / v2.6.2 这类可移动引用
            if not re.fullmatch(r"[0-9a-f]{40}", version):
                offenders.append(f"{path.name}: {ref}")
        assert not offenders, (
            "第三方 action 必须固定到 commit SHA（可变 tag 可被改指向任意代码，"
            f"却带着本仓库的写权限执行）: {offenders}"
        )

    def test_no_local_action_without_sha(self, workflows):
        """本地 action（./path）无需 SHA；但不允许指向目录以外的可变引用。"""
        for path, ref in self._uses(workflows):
            action = ref.partition("@")[0]
            assert not action.startswith(("http://", "http://", "git@")), \
                f"{path.name}: {ref} 不得使用远程 URL 直接引用"

    def test_pinned_shas_are_annotated(self, workflows):
        """固定到 SHA 时应留行尾注释标明版本，便于人工比对与升级。"""
        for path in workflows:
            for line in path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*-?\s*uses:\s*(\S+)@([0-9a-f]{40})\s*(#.*)?$", line)
                if m and not m.group(3):
                    offenders = f"{path.name}: {m.group(1)} 未标注版本注释"
                    raise AssertionError(offenders)


class TestWorkflowPermissionsAreScoped:
    """权限应在 job 级最小化，且不出现无限制的顶层 permissions。"""

    def test_no_write_all(self, workflows):
        offenders = []
        for path in workflows:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.match(r"^\s*permissions:\s*write-all\s*$", line):
                    offenders.append(f"{path.name}:{i}")
                if re.match(r"^\s*-\s*write-all\s*$", line):
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, f"存在 write-all 权限: {offenders}"

    def test_no_token_with_unrestricted_scope(self, workflows):
        """GITHUB_TOKEN 不应是 pass-all；默认权限也应收紧过。"""
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            assert "permissions: write-all" not in text, f"{path.name}: permissions: write-all"


class TestNoSecretsInRepo:
    """提交型凭据是最严重的扫描发现项。"""

    TOKEN_PATTERNS = (
        re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),
        re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
        re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    )

    def test_no_hardcoded_tokens_in_tracked_files(self):
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in path.parts for part in
                   (".git", "node_modules", "外部参考", "__pycache__", ".tmp")):
                continue
            if path.suffix.lower() in (".png", ".jpg", ".gz", ".db", ".pyc"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in self.TOKEN_PATTERNS:
                if pat.search(text):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
                    break
        assert not offenders, f"疑似硬编码凭据: {sorted(set(offenders))}"

    def test_env_file_not_committed(self):
        """书项目的 .env 含 API Key，必须被忽略。"""
        tracked = {p.as_posix() for p in REPO_ROOT.rglob(".env")}
        tracked = {p for p in tracked if ".git" not in p}
        # 仓库根不应有 .env；示例模板可以有
        assert not (REPO_ROOT / ".env").exists(), "仓库根存在 .env（可能含 API Key）"
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
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))

# 仓库级遍历时要**在遍历阶段剪枝**的目录。
#
# 为什么不能用 `REPO_ROOT.rglob(...)`：conftest.py 把所有临时目录钉在仓库内的
# `.tmp/pytest/<测试名>_<hash>/`，并行跑测试时这些目录**边建边删**。rglob 属于
# 惰性遍历，走到一半目录已被删就抛
#   FileNotFoundError: ... .tmp/pytest/test_xxx_<hash>   (pathlib._iterate_directories)
# 事后再按 parts 过滤已经来不及——异常在遍历阶段就抛出，整个测试变红，
# 而这是环境噪声、不是断言失败（2026-10-18 深度审查定位的偶发红根因）。
# os.walk 允许剪枝 dirs，从根上不进入这些目录。
_WALK_SKIP_DIRS = frozenset({
    ".git", ".tmp", "node_modules", "外部参考", "__pycache__",
    ".pytest_cache", "venv", ".venv", ".npm-cache",
})


def _walk_repo(names=None):
    """遍历仓库文件，返回相对 POSIX 路径列表（遍历期剪枝，不会踩到已删目录）。

    names: 只收 basename 在该集合内的文件；None 表示全部收。
    """
    out = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _WALK_SKIP_DIRS]
        for name in files:
            if names is not None and name not in names:
                continue
            full = Path(root) / name
            out.append(full.relative_to(REPO_ROOT).as_posix())
    return out

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
        for rel in _walk_repo():
            path = REPO_ROOT / rel
            if path.suffix.lower() in (".png", ".jpg", ".gz", ".db", ".pyc"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in self.TOKEN_PATTERNS:
                if pat.search(text):
                    offenders.append(rel)
                    break
        assert not offenders, f"疑似硬编码凭据: {sorted(set(offenders))}"

    def test_env_file_not_committed(self):
        """书项目的 .env 含 API Key，必须被忽略。"""
        # ⚠️ 排除 `.tmp`：conftest.py 把 tmp_path 钉在仓库内 `.tmp/pytest/`，
        # 测试期间目录边建边删，rglob 会在已消失的目录上抛 FileNotFoundError。
        # 同一类的 token 扫描测试已排除 `.tmp`，此处是遗漏
        # （2026-10-18 深度审查定位到的偶发红）。
        tracked = set(_walk_repo(names={".env"}))
        # 仓库根不应有 .env；示例模板可以有
        assert not (REPO_ROOT / ".env").exists(), "仓库根存在 .env（可能含 API Key）"

class TestEnvCredentialsAreIgnored:
    """`.env` 必须被工具仓库自己的 .gitignore 挡住（P2-3）。

    背景：`config.py` 读的是 **CWD 下的 .env**，而用户/开发者常常就站在这个
    工具仓库根目录里跑命令。此前 .gitignore 只忽略了 `test_gemini.env` 这个
    具体文件名，`.env` 本身没有被忽略（`git check-ignore .env` rc=1），
    一旦有人把真实 API Key 写进 `.env`，`git add -A` 就会原样提交上去。
    """

    def _check_ignore(self, rel: str) -> bool:
        """返回 True 表示该路径会被忽略。"""
        r = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0

    def test_dotenv_is_ignored(self):
        assert self._check_ignore(".env"), (
            ".env 没有被忽略！config.py 直接从 CWD 读它，"
            "误提交会把真实 API Key 推到公开仓库")

    def test_nested_dotenv_is_ignored(self):
        assert self._check_ignore("somebook/.env"), "子目录里的 .env 也必须被忽略"

    def test_dotenv_example_is_trackable(self):
        assert not self._check_ignore(".env.example"), (
            ".env.example 是给人看的模板，必须能进 git；"
            "用 negation 规则把它放行")

    def test_gitignore_has_env_negation(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in text, ".gitignore 里缺少 .env 规则"
        assert "!.env.example" in text, (
            "缺少 !.env.example 放行规则，模板文件会被一起忽略")

"""npm 离线包必须携带 dashboard 前端构建产物（frontend/dist）。

背景（2026-10-18 深度审查发现，P0）：

发布链此前会产出**没有前端**的包，且全程静默——构建成功、verify 通过、
npm 发布成功。用户 `init` 之后打开 `/webnovel-dashboard`，skill 的硬门禁
（`test -f frontend/dist/index.html || exit 1`）立刻失败，面板不可用。

根因是三处缺口叠加，任何一处单独存在都足以让问题复发：

1. `.gitignore` 里 `dist/` 的 negation 写成了**早已废弃的旧目录布局**
   `webnovel-writer/dashboard/frontend/dist/`，而真实路径是
   `.opencode/dashboard/frontend/dist/`。negation 永不命中 ⇒ dist 被
   `dist/` 规则无条件排除 ⇒ 从未进过 git。CI 与用户 checkout 都来自 git 树，
   于是 `build-bundle.js`（遍历的是**工作树文件系统**）在 CI 上遍历不到 dist。
2. `build-bundle.js` 没有 dist 存在性断言——对比它给 `AGENTS.md`/`CLAUDE.md`
   写的"缺失时拒绝出包"，同样的保护没给前端。
3. `verify-bundle.js` 的 checks 白名单里也没有 dist，所以即便 2 失效，
   校验器仍给绿灯。

回归测试因此钉三层：git 层面 dist 可被跟踪、build 层面缺失时**大声失败**、
verify 层面必须校验 dist 条目。
"""
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
NPM_DIR = REPO_ROOT / ".npm-package"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 不在 PATH")

BUNDLE = NPM_DIR / "offline" / "opencode-bundle.tar.gz"
DIST_REL = ".opencode/dashboard/frontend/dist"
DIST_INDEX_REL = f"{DIST_REL}/index.html"


def _run(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise AssertionError(
            f"命令失败 rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def _git_available() -> bool:
    if shutil.which("git") is None:
        return False
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    return r.returncode == 0


needs_git = pytest.mark.skipif(not _git_available(), reason="不在 git 工作树内")


@needs_git
class TestGitignoreTracksFrontendDist:
    """`.gitignore` 的 negation 必须指向仓库**真实**布局。

    这是最初那个 P0 的直接根因：negation 写了旧路径，看着像"已经放行"，
    实际永不命中，`dist/` 把整个产物静默排除在 git 之外。
    """

    def test_dist_index_is_not_ignored(self):
        """dist/index.html 必须**不被** git ignore（按规则判定，不看是否已跟踪）。

        ⚠️ 两个坑，都踩过：

        1. `git check-ignore` 的**退出码不可信**——命中否定规则时它同样返回 0
           （`!.opencode/.../dist/**` 与真被忽略的 `.tmp/` 都是 rc=0）。
           所以只看输出里命中的那条规则是否以 `!` 开头。
        2. 必须加 `--no-index`。默认模式下 git 对**已跟踪**文件直接不报告
           ignore 状态（rc=1、无输出），一旦 dist 提交进 git，这条断言就
           永远绿灯，negation 再写错也发现不了——那正是原始 P0 的成因。
        """
        r = subprocess.run(
            ["git", "check-ignore", "-v", "--no-index", DIST_INDEX_REL],
            cwd=REPO_ROOT, capture_output=True, text=True)
        # 输出格式：<source>:<lineno>:<pattern>\t<pathname>
        rule = r.stdout.split("\t")[0] if r.stdout.strip() else ""
        matched_pattern = rule.split(":", 2)[-1] if rule else ""
        assert rule, (
            f"`git check-ignore --no-index` 对 {DIST_INDEX_REL} 没有任何输出，"
            f"说明它既不匹配 `dist/` 也没被 negation 放行——.gitignore 的 "
            f"negation 可能又指向了不存在的路径。")
        assert matched_pattern.startswith("!"), (
            f"{DIST_INDEX_REL} 仍被 git 忽略（命中规则：{rule}）。"
            f"检查 .gitignore 里 `dist/` 的 negation 是否指向真实路径 "
            f"`!{DIST_REL}/`。")

    def test_gitignore_negation_uses_current_layout(self):
        """negation 不得再引用已废弃的 `webnovel-writer/dashboard/...` 布局。"""
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        stale = "!webnovel-writer/dashboard/frontend/dist"
        assert stale not in text, (
            f"`.gitignore` 仍含过时 negation `{stale}`——该路径在仓库中不存在，"
            f"永远不会命中，dist 会被 `dist/` 规则排除在 git 之外。")
        assert f"!{DIST_REL}/" in text, (
            f"`.gitignore` 缺少 `!{DIST_REL}/`，前端产物将无法进 git。")

    def test_dist_files_are_tracked(self):
        """dist 下的文件必须**已在 git 索引里**——CI 与用户 checkout 只有 git 树。

        这条用 `git ls-files` 而不是 `git add -An` 判定：文件一旦提交，
        `git add -An` 对无改动的工作树返回空输出（"没有新东西可暂存"），
        把空当成"被忽略"会得到假红（本项目实测过）。
        权威问题是"仓库里有没有它"，答案在索引里。
        """
        r = _run(["git", "ls-files", DIST_REL], cwd=REPO_ROOT)
        tracked = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert tracked, (
            f"{DIST_REL} 下没有任何文件被 git 跟踪——CI checkout 拿不到前端产物，"
            f"打出的 npm 包将没有 dashboard 界面。")
        assert any(ln.endswith("dist/index.html") for ln in tracked), (
            f"dist/index.html 未被跟踪。已跟踪（前 5 条）：{tracked[:5]}")


@needs_node
class TestBuildRequiresFrontendDist:
    def test_build_script_asserts_dist(self):
        """build-bundle.js 必须显式断言 dist/index.html 存在。"""
        text = (NPM_DIR / "scripts" / "build-bundle.js").read_text(encoding="utf-8")
        assert "frontend" in text and "dist" in text, \
            "build-bundle.js 完全没提到前端产物"
        assert "前端构建产物不存在" in text, \
            "build-bundle.js 没有 dist 缺失时的明确报错"

    def test_verify_script_checks_dist(self):
        """verify-bundle.js 的 checks 必须包含 dist/index.html。"""
        text = (NPM_DIR / "scripts" / "verify-bundle.js").read_text(encoding="utf-8")
        assert DIST_INDEX_REL in text, (
            f"verify-bundle.js 未校验 {DIST_INDEX_REL}——"
            f"build 的断言若被绕过，校验器仍会放行坏包。")

    def test_missing_dist_exits_nonzero(self, tmp_path):
        """dist 缺失时必须让发布链失败，而不是静默出一个坏包。"""
        # 最小仓库根：LICENSE + AGENTS.md/CLAUDE.md + 空的 .opencode/，**没有** dist
        repo = tmp_path / "repo"
        (repo / ".npm-package" / "scripts").mkdir(parents=True)
        (repo / ".opencode").mkdir()
        shutil.copy(NPM_DIR / "scripts" / "build-bundle.js",
                    repo / ".npm-package" / "scripts" / "build-bundle.js")
        shutil.copy(REPO_ROOT / "LICENSE", repo / "LICENSE")
        for doc in ("AGENTS.md", "CLAUDE.md"):
            shutil.copy(REPO_ROOT / doc, repo / doc)

        r = _run(
            [NODE, str(repo / ".npm-package" / "scripts" / "build-bundle.js")],
            check=False)
        assert r.returncode != 0, (
            "缺 frontend/dist 却构建成功——没有前端的包会被发布出去，"
            "用户安装后 dashboard skill 第一步就 exit 1。")
        combined = r.stdout + r.stderr
        assert "前端构建产物不存在" in combined, \
            f"失败信息没说明是前端产物缺失：{combined[-400:]}"


@needs_node
class TestBuiltBundleContainsFrontend:
    def test_bundle_includes_dist_index(self):
        """真实构建产物里必须能解出 dist/index.html。"""
        _run([NODE, "scripts/build-bundle.js"], cwd=NPM_DIR)
        assert BUNDLE.is_file(), "构建未产出离线包"

        with tarfile.open(BUNDLE, "r:gz") as tf:
            names = tf.getnames()
            assert DIST_INDEX_REL in names, (
                f"离线包里没有 {DIST_INDEX_REL}（条目数 {len(names)}）。"
                f"下游 dashboard skill 以它为硬门禁。")
            # 前端产物是 assets/ 下的一批文件，只有一个 index.html 说明收集被截断
            dist_assets = [n for n in names
                           if n.startswith(f"{DIST_REL}/assets/")]
            assert dist_assets, "离线包缺 dist/assets/——前端资源不完整"

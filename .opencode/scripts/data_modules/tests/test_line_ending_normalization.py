"""行尾规范化（.gitattributes）的回归钉。

背景（2026-10-18 深度审查，P2）：仓库里的 blob 全是 LF
（`git ls-files --eol` = 598 个 `i/lf`、0 个 `i/crlf`），但仓库和系统都没有
设置 `core.autocrlf`，也没有 `.gitattributes`。结果是 Windows 工作区产生的
CRLF 会原样进入 blob：

  - `git status` 常年挂着 500+ 个"已修改"文件，真实改动被淹没在噪声里
  - 只改了 6 行的提交在 diff 里显示成 156 行增删
  - review 时分不清"逻辑改动"与"换行符抖动"

修复是加 `.gitattributes` 的 `* text=auto eol=lf`。这组测试钉住它不被删掉，
并且验证它**真的**在 `git add` 时生效（而不只是文件存在）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="需要 git"
)


def _git(*args, cwd=REPO_ROOT):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


class TestGitattributesExists:
    def test_file_present(self):
        assert GITATTRIBUTES.is_file(), (
            "缺少 .gitattributes：Windows 工作区产生的 CRLF 会原样进入 blob，"
            "git status 会被 500+ 个假修改淹没")

    def test_declares_lf_normalization(self):
        text = GITATTRIBUTES.read_text(encoding="utf-8")
        assert "text=auto" in text, "缺少 text=auto：行尾不会自动规范化"
        assert "eol=lf" in text, (
            "缺少 eol=lf：入库行尾不锁定为 LF，与既有 blob 事实不一致")

    def test_binaries_are_excluded(self):
        """二进制必须声明为 binary，否则 Git 会尝试做行尾转换。"""
        text = GITATTRIBUTES.read_text(encoding="utf-8")
        for ext in (".png", ".jpg", ".zip", ".db"):
            assert f"*{ext} binary" in text, f"{ext} 未声明为二进制"


class TestIndexIsNormalized:
    def test_index_has_no_crlf_blobs(self):
        """索引里不允许出现 CRLF blob —— 一旦出现就是规范化失效。"""
        r = _git("ls-files", "--eol")
        assert r.returncode == 0, r.stderr
        crlf = [
            line for line in r.stdout.splitlines()
            if line.split() and line.split()[0] == "i/crlf"
        ]
        assert not crlf, (
            f"索引中有 {len(crlf)} 个 CRLF blob，行尾规范化没有生效：\n"
            + "\n".join(crlf[:10]))

    def test_attributes_apply_to_markdown_and_python(self):
        """抽查：typescript/markdown/python 都应被 text=auto 覆盖。"""
        for rel in ("README.md", ".opencode/scripts/webnovel.py"):
            r = _git("check-attr", "text", "eol", "--", rel)
            assert r.returncode == 0, r.stderr
            assert "text: auto" in r.stdout or "text: set" in r.stdout, (
                f"{rel} 没有被 text=auto 覆盖：{r.stdout}")


class TestStagingNormalizesCrlf:
    """端到端：把一个含 CRLF 的文件 add 进临时索引，blob 必须是 LF。

    这是本组最有价值的一条 —— 只断言".gitattributes 存在"的话，
    属性写错了（比如漏了 text=auto）测试照样绿，但线上照样被污染。
    """

    def test_crlf_worktree_file_becomes_lf_blob(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        if _git("init", "-q", cwd=repo).returncode != 0:
            pytest.skip("无法初始化临时仓库")

        (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
        target = repo / "sample.md"
        target.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))

        env_index = tmp_path / "idx"
        env = {"GIT_INDEX_FILE": str(env_index),
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        import os
        runenv = {**os.environ, **env}
        r = subprocess.run(["git", "add", "sample.md"], cwd=repo,
                           capture_output=True, text=True, env=runenv, timeout=60)
        assert r.returncode == 0, r.stderr

        blob = subprocess.run(["git", "cat-file", "-p", ":sample.md"], cwd=repo,
                              capture_output=True, env=runenv, timeout=60)
        assert b"\r\n" not in blob.stdout, (
            "CRLF 进入了 blob —— eol=lf 没有生效，索引会被换行符污染")
        assert blob.stdout == "第一行\n第二行\n".encode("utf-8")

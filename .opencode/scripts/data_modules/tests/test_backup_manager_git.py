"""backup_manager 的 git 语义测试（第4轮，对齐上游 03ffc9a / ff27f00 / 668c2b6）。

这里测的是**真实 git 仓库**里的行为，不是 mock：回滚是否脱离 HEAD、
备份失败是否被如实报告、降级备份是否真的覆盖正文——都是只有在真 git
仓库里跑才有意义的不变式。
"""
import subprocess

import pytest

from backup_manager import GitBackupManager


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """一个已提交、带 ch0001/ch0002 tag 的最小项目仓库。"""
    root = tmp_path / "book"
    (root / ".webnovel").mkdir(parents=True)
    (root / "chapters").mkdir()
    (root / ".webnovel" / "state.json").write_text('{"v": 1}', encoding="utf-8")
    (root / "chapters" / "001.md").write_text("第一版", encoding="utf-8")

    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "c1")
    _git(root, "tag", "ch0001")

    # 第二版：状态与正文都改
    (root / ".webnovel" / "state.json").write_text('{"v": 2}', encoding="utf-8")
    (root / "chapters" / "001.md").write_text("第二版", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "c2")
    _git(root, "tag", "ch0002")
    return root


def _head_branch(root):
    out = _git(root, "symbolic-ref", "--short", "HEAD")
    return out.stdout.strip()


class TestForwardOnlyRollback:
    def test_rollback_removes_files_added_after_the_tag(self, repo):
        """回滚必须真的删掉备份点之后新增的文件。

        `git checkout <tag> -- .` 只还原 tag 里存在的路径，新增文件原样留下
        （上游 03ffc9a 也有这个漏洞）。对小说项目来说「回滚到第 1 章」却留着
        第 2 章，等于没回滚。
        """
        (repo / "chapters" / "002.md").write_text("第二章节", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add ch2")

        assert GitBackupManager(str(repo)).rollback(1) is True

        assert not (repo / "chapters" / "002.md").exists(), "备份点之后新增的文件没有被删除"
        assert (repo / "chapters" / "001.md").read_text(encoding="utf-8") == "第一版"
        tracked = _git(repo, "ls-files").stdout
        assert "chapters/002.md" not in tracked

    def test_rollback_restores_files_without_detaching_head(self, repo):
        """核心不变式：回滚后必须仍在原分支上。"""
        (repo / "chapters" / "001.md").write_text("第三版", encoding="utf-8")

        bm = GitBackupManager(str(repo))
        assert bm.rollback(1) is True

        assert _head_branch(repo) == "master", "回滚把仓库留在了 detached HEAD"
        assert (repo / "chapters" / "001.md").read_text(encoding="utf-8") == "第一版"
        assert (repo / ".webnovel" / "state.json").read_text(encoding="utf-8") == '{"v": 1}'

    def test_rollback_is_a_forward_commit(self, repo):
        """前滚式：回滚本身是提交，历史不被改写。"""
        (repo / "chapters" / "001.md").write_text("第三版", encoding="utf-8")
        before = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()

        GitBackupManager(str(repo)).rollback(1)

        after = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
        assert int(after) == int(before) + 1, "回滚没有产生前向提交"
        subjects = _git(repo, "log", "--oneline").stdout
        assert "rollback:" in subjects
        # ch0002 仍在，说明旧历史没有被 detach 掉
        assert _git(repo, "rev-parse", "ch0002").returncode == 0

    def test_rollback_is_not_corrupted_by_detached_head_entry(self, repo):
        """已经在 detached HEAD 时必须拒绝，而不是把恢复提交丢进虚空。"""
        _git(repo, "checkout", "ch0001")

        bm = GitBackupManager(str(repo))
        assert bm.rollback(2) is False

    def test_rollback_to_missing_tag_fails_cleanly(self, repo):
        """tag 不存在时不能把工作区还原成一堆 deleted。"""
        (repo / "chapters" / "001.md").write_text("第三版", encoding="utf-8")

        bm = GitBackupManager(str(repo))
        assert bm.rollback(99) is False
        assert (repo / "chapters" / "001.md").read_text(encoding="utf-8") == "第三版"
        assert _head_branch(repo) == "master"

    def test_rollback_reports_no_op_as_success(self, repo):
        """已在目标状态时不应报错（nothing to commit 属正常）。"""
        assert GitBackupManager(str(repo)).rollback(2) is True


class TestDegradedBackup:
    """Git 不可用时的降级备份必须覆盖正文（上游 668c2b6）。"""

    def test_covers_manuscript_not_just_state(self, tmp_path):
        root = tmp_path / "novel"
        for folder, name in (("正文", "001.md"), ("大纲", "outline.md"), ("设定集", "world.md")):
            (root / folder).mkdir(parents=True)
            (root / folder / name).write_text(f"{folder}内容", encoding="utf-8")
        (root / ".webnovel").mkdir()
        (root / ".webnovel" / "state.json").write_text('{"v": 1}', encoding="utf-8")

        bm = GitBackupManager(str(root))
        bm.git_available = False  # 强制走降级路径
        assert bm._local_backup(1) is True

        snaps = sorted((root / ".webnovel" / "backups").glob("snapshot_ch*"))
        assert len(snaps) == 1
        snap = snaps[0]
        assert (snap / "正文" / "001.md").read_text(encoding="utf-8") == "正文内容"
        assert (snap / "大纲" / "outline.md").exists()
        assert (snap / "设定集" / "world.md").exists()
        assert (snap / ".webnovel" / "state.json").exists()

    def test_keeps_only_recent_snapshots(self, tmp_path):
        root = tmp_path / "novel"
        (root / "正文").mkdir(parents=True)
        (root / "正文" / "001.md").write_text("正文", encoding="utf-8")
        (root / ".webnovel").mkdir()
        (root / ".webnovel" / "state.json").write_text('{"v": 1}', encoding="utf-8")

        bm = GitBackupManager(str(root))
        bm.git_available = False
        for i in range(1, 14):
            assert bm._local_backup(i) is True

        snaps = list((root / ".webnovel" / "backups").glob("snapshot_ch*"))
        assert len(snaps) == 10, f"未限制快照数量: {len(snaps)}"

    def test_repeated_backup_does_not_overwrite(self, tmp_path):
        """同章连续降级备份必须各自成目录（微秒时间戳）。"""
        root = tmp_path / "novel"
        (root / "正文").mkdir(parents=True)
        (root / "正文" / "001.md").write_text("正文", encoding="utf-8")
        (root / ".webnovel").mkdir()
        (root / ".webnovel" / "state.json").write_text('{"v": 1}', encoding="utf-8")

        bm = GitBackupManager(str(root))
        bm.git_available = False
        bm._local_backup(5)
        bm._local_backup(5)
        assert len(list((root / ".webnovel" / "backups").glob("snapshot_ch*"))) == 2

    def test_empty_project_is_not_reported_as_backed_up(self, tmp_path):
        """什么都没复制到时不得报成功——否则用户以为有备份。"""
        root = tmp_path / "empty"
        (root / ".webnovel").mkdir(parents=True)
        bm = GitBackupManager(str(root))
        bm.git_available = False
        assert bm._local_backup(1) is False
        assert not list((root / ".webnovel" / "backups").glob("snapshot_ch*"))


class TestCliExitCodes:
    """CLI 失败必须非零退出——否则调用方以为回滚成功了。"""

    def _cli(self, repo, *args):
        import subprocess
        import sys as _sys
        from pathlib import Path

        entry = Path(__file__).resolve().parents[2] / "backup_manager.py"
        return subprocess.run(
            [_sys.executable, "-X", "utf8", str(entry),
             "--project-root", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_rollback_failure_exits_nonzero(self, repo):
        r = self._cli(repo, "--rollback", "99")
        assert r.returncode != 0, "回滚失败却返回 0"

    def test_rollback_success_exits_zero(self, repo):
        r = self._cli(repo, "--rollback", "2")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_create_branch_failure_exits_nonzero(self, repo):
        r = self._cli(repo, "--create-branch", "99", "--branch-name", "nope")
        assert r.returncode != 0, "创建分支失败却返回 0"


class TestGitFailureReporting:
    def test_tag_failure_aborts_backup(self, repo, monkeypatch):
        """tag 是回滚的唯一入口——打不出来就不能报备份成功。"""
        # 需要真实变更，否则「无变更」分支会提前返回，走不到打 tag
        (repo / "chapters" / "001.md").write_text("新章节内容", encoding="utf-8")

        bm = GitBackupManager(str(repo))
        real = bm._run_git_command

        def fake(args, check=True):
            if args and args[0] == "tag" and "-d" not in args:
                return False, "fatal: tag already exists"
            return real(args, check=check)

        monkeypatch.setattr(bm, "_run_git_command", fake)
        assert bm.backup(1) is False

    def test_commit_failure_reported(self, repo, monkeypatch):
        """git commit 失败必须返回 False。"""
        bm = GitBackupManager(str(repo))
        real = bm._run_git_command

        def fake(args, check=True):
            if args and args[0] == "commit":
                return False, "fatal: identity unknown"
            return real(args, check=check)

        monkeypatch.setattr(bm, "_run_git_command", fake)
        assert bm.backup(3) is False

    def test_check_false_still_reports_non_zero_exit(self, repo):
        """check=False 只应抑制抛异常，不应把失败伪装成成功。"""
        bm = GitBackupManager(str(repo))
        ok, _ = bm._run_git_command(["rev-parse", "definitely-not-a-tag"], check=False)
        assert ok is False, "check=False 掩盖了非零退出码"

    def test_create_branch_detects_missing_tag(self, repo):
        """tag 存在性检查此前是死代码，应真正生效。"""
        bm = GitBackupManager(str(repo))
        assert bm.create_branch(99, "nope") is False
        assert bm.create_branch(2, "restored") is True


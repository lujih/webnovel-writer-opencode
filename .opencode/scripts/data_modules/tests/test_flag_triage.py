#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flag-triage（review_flags 人工仲裁）与 context 注入的测试。

门禁分层后 LLM 语义问题不拒章、降级为 `review_flags`（triage=None 即待仲裁）。
本文件钉住两条链路：

1. **flag_triage.py CLI**：list / confirm / dismiss
   - 只记录，不改 commit status
   - 幂等（重复裁决覆盖 triage 值）
   - 越界 / 无 commit 时明确报错

2. **context 注入**（`_load_pending_review_flags`）：
   - 写第 N 章前注入第 N-1 章的未仲裁 flags（上限 3）
   - 已 confirm/dismiss 的不再注入
   - 无 commit 时降级返回 []（不阻断）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules import flag_triage  # noqa: E402


def _make_project(tmp_path: Path, *, chapter: int = 4,
                  flags=None) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    if flags is not None:
        commits = tmp_path / ".story-system" / "commits"
        commits.mkdir(parents=True)
        (commits / f"chapter_{chapter:03d}.commit.json").write_text(
            json.dumps({
                "meta": {"chapter": chapter, "status": "accepted"},
                "review_flags": flags,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    return tmp_path


def _flags(sev_desc: list[tuple[str, str]]):
    return [
        {"severity": s, "category": "other", "description": d, "triage": None}
        for s, d in sev_desc
    ]


class TestFlagTriageCli:
    """flag_triage CLI 行为。"""

    def test_list_reports_pending(self, tmp_path):
        project = _make_project(tmp_path, flags=_flags([("critical", "A"), ("high", "B")]))
        capsys = pytest.MonkeyPatch.__class__  # 仅占位；用 monkeypatch fixture
        # 直接调 main 用 monkeypatch
        def run(args_list):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + args_list
            with pytest.raises(SystemExit) as exc:
                flag_triage.main()
            return exc.value.code

        assert run(["list", "--chapter", "4", "--limit", "20"]) == 0

    def test_confirm_marks_triage(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path, flags=_flags([("critical", "A"), ("high", "B")]))
        cap = []

        def fake_main(argv):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + argv
            try:
                flag_triage.main()
            except SystemExit:
                pass

        fake_main(["confirm", "--chapter", "4", "--index", "0"])
        saved = json.loads(
            (project / ".story-system" / "commits" / "chapter_004.commit.json")
            .read_text(encoding="utf-8"))
        assert saved["review_flags"][0]["triage"] == "confirmed"
        assert saved["review_flags"][1]["triage"] is None
        # 不改 status
        assert saved["meta"]["status"] == "accepted"

    def test_dismiss_marks_triage(self, tmp_path):
        project = _make_project(tmp_path, flags=_flags([("critical", "A")]))
        def fake_main(argv):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + argv
            try:
                flag_triage.main()
            except SystemExit:
                pass
        fake_main(["dismiss", "--chapter", "4", "--index", "0"])
        saved = json.loads(
            (project / ".story-system" / "commits" / "chapter_004.commit.json")
            .read_text(encoding="utf-8"))
        assert saved["review_flags"][0]["triage"] == "dismissed"

    def test_idempotent_repeat_decision(self, tmp_path):
        """重复 confirm 同一 index 只是覆盖 triage 值，不报错。"""
        project = _make_project(tmp_path, flags=_flags([("critical", "A")]))
        def fake_main(argv):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + argv
            try:
                flag_triage.main()
            except SystemExit:
                pass
        fake_main(["confirm", "--chapter", "4", "--index", "0"])
        fake_main(["dismiss", "--chapter", "4", "--index", "0"])  # 改判
        saved = json.loads(
            (project / ".story-system" / "commits" / "chapter_004.commit.json")
            .read_text(encoding="utf-8"))
        assert saved["review_flags"][0]["triage"] == "dismissed", "最新裁决生效"

    def test_out_of_range_index_fails(self, tmp_path, capsys):
        project = _make_project(tmp_path, flags=_flags([("critical", "A")]))
        def fake_main(argv):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + argv
            try:
                flag_triage.main()
            except SystemExit as e:
                return e.code
            return 0
        rc = fake_main(["confirm", "--chapter", "4", "--index", "9"])
        assert rc == 1, "越界必须报错"

    def test_no_commit_fails(self, tmp_path):
        project = _make_project(tmp_path, flags=None)   # 无 commit
        def fake_main(argv):
            sys.argv = ["flag_triage.py", "--project-root", str(project)] + argv
            try:
                flag_triage.main()
            except SystemExit as e:
                return e.code
            return 0
        rc = fake_main(["list", "--chapter", "4"])
        assert rc == 1, "无 commit 时 list 报错（不能静默）"


class TestContextInjection:
    """context_manager._load_pending_review_flags 注入行为。"""

    def _cm(self, project: Path):
        from data_modules.config import DataModulesConfig
        from data_modules.context_manager import ContextManager

        cfg = DataModulesConfig.from_project_root(project)
        return ContextManager(cfg)

    def test_injects_untriaged_flags(self, tmp_path):
        project = _make_project(tmp_path, chapter=3,
                                flags=_flags([("critical", "A"), ("medium", "B"),
                                              ("low", "C"), ("low", "D")]))
        cm = self._cm(project)
        # 写第 4 章 → 读第 3 章
        flags = cm._load_pending_review_flags(4)
        # 上限 3，取前 3 条（未仲裁的）
        assert [f["description"] for f in flags] == ["A", "B", "C"], (
            f"应取前 3 条未仲裁 flag，实际 {[f['description'] for f in flags]}"
        )

    def test_excludes_triangied_flags(self, tmp_path):
        flags = _flags([("critical", "A"), ("high", "B")])
        flags[0]["triage"] = "confirmed"
        project = _make_project(tmp_path, chapter=3, flags=flags)
        cm = self._cm(project)
        got = cm._load_pending_review_flags(4)
        assert [f["description"] for f in got] == ["B"], (
            "已 confirm 的 flag 不得再注入警戒段"
        )

    def test_no_commit_returns_empty(self, tmp_path):
        project = _make_project(tmp_path, flags=None)
        cm = self._cm(project)
        assert cm._load_pending_review_flags(4) == []

    def test_chapter_one_returns_empty(self, tmp_path):
        project = _make_project(tmp_path, chapter=4, flags=_flags([("critical", "A")]))
        cm = self._cm(project)
        assert cm._load_pending_review_flags(1) == [], "第 1 章无上一章"

    def test_reader_signal_carries_pending_flags(self, tmp_path):
        project = _make_project(tmp_path, chapter=3,
                                flags=_flags([("critical", "A"), ("high", "B")]))
        cm = self._cm(project)
        signal = cm._load_reader_signal(4)
        assert "pending_review_flags" in signal, "有未仲裁 flag 时 signal 必须带警戒段"
        assert signal["pending_review_flags"][0]["chapter"] == 3

    def test_reader_signal_no_flags_key_absent_or_empty(self, tmp_path):
        project = _make_project(tmp_path, chapter=3, flags=None)
        cm = self._cm(project)
        signal = cm._load_reader_signal(4)
        # 无 flag 时键可以缺席（不伪造空数组也行），但不得报错
        assert signal.get("pending_review_flags") in (None, [])


class TestDashboardFlagEndpoints:
    """/api/review/flags GET + /api/review/flags/triage PUT。"""

    @pytest.fixture
    def client(self, tmp_path):
        import pytest as _pytest
        _pytest.importorskip("fastapi")
        _pytest.importorskip("httpx")

        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        commits = tmp_path / ".story-system" / "commits"
        commits.mkdir(parents=True)
        import json as _json
        (commits / "chapter_003.commit.json").write_text(
            _json.dumps({
                "meta": {"chapter": 3, "status": "accepted"},
                "review_flags": [
                    {"severity": "critical", "category": "setting",
                     "description": "境界矛盾", "triage": None},
                    {"severity": "high", "category": "logic",
                     "description": "因果断裂", "triage": "confirmed"},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        if str(SCRIPTS.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPTS.parent))
        from fastapi.testclient import TestClient
        from dashboard.app import create_app

        return TestClient(create_app(project_root=tmp_path),
                          base_url="http://127.0.0.1:8765"), tmp_path

    def test_get_flags_reports_pending(self, client):
        c, project = client
        data = c.get("/api/review/flags").json()
        assert data["total_pending"] == 1
        ch3 = data["chapters"][0]
        assert ch3["pending"] == 1 and ch3["triaged"] == 1
        assert ch3["pending_severity"] == {"critical": 1}

    def test_put_triage_writes_back(self, client):
        c, project = client
        r = c.put("/api/review/flags/triage",
                  json={"chapter": 3, "index": 0, "action": "confirm"})
        assert r.status_code == 200 and r.json()["ok"]
        import json as _json
        saved = _json.loads(
            (project / ".story-system" / "commits" / "chapter_003.commit.json")
            .read_text(encoding="utf-8"))
        assert saved["review_flags"][0]["triage"] == "confirmed"
        # 仲裁后 pending 归零
        assert c.get("/api/review/flags").json()["total_pending"] == 0

    def test_put_out_of_range_404(self, client):
        c, project = client
        r = c.put("/api/review/flags/triage",
                  json={"chapter": 99, "index": 0, "action": "confirm"})
        assert r.status_code == 404

    def test_put_bad_index_400(self, client):
        c, project = client
        r = c.put("/api/review/flags/triage",
                  json={"chapter": 3, "index": 9, "action": "confirm"})
        assert r.status_code == 400

    def test_put_bad_action_400(self, client):
        c, project = client
        r = c.put("/api/review/flags/triage",
                  json={"chapter": 3, "index": 0, "action": "explode"})
        assert r.status_code == 400

    def test_get_no_commits_dir_empty(self, client):
        c, project = client
        # 删掉 commit 目录 → 空结构不 500
        import shutil
        shutil.rmtree(project / ".story-system" / "commits")
        data = c.get("/api/review/flags").json()
        assert data == {"chapters": [], "total_pending": 0}

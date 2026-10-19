#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard 全部写端点的冒烟矩阵——每条都断言「副作用真的发生」。

为什么单独一个文件（2026-10-20）：

既有的 `test_dashboard_csrf_coverage.py` 从 app.routes 枚举写路由、发一个
**空 body** 的请求，然后只看「有没有被 CSRF 拦」。这有两个盲区：

1. 空 body 在参数校验阶段就被 400 挡掉，根本没走到写盘逻辑，
   所以「处理器自身崩溃（500）」永远暴露不出来；
2. 只看状态码，不看落盘结果。

结果就是 `POST /api/style/anti-patterns` 自锁后稳定返回 500 长达数月，
而测试全绿。本文件补上这两点：用**合法 payload** 打进去，并检查副作用。

约定：每个用例都要能在「端点坏掉」时给出明确的失败信息，
而不是笼统的 assert。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import create_app  # noqa: E402

BASE_URL = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE_URL}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """最小可用的书项目骨架。"""
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"project_info": {"title": "冒烟测试书"}, "progress": {"current_chapter": 0}}),
        encoding="utf-8",
    )
    (tmp_path / ".story-system").mkdir(parents=True, exist_ok=True)
    for name in ("正文", "大纲", "设定集"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def client(project: Path) -> TestClient:
    return TestClient(create_app(project_root=project), base_url=BASE_URL, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 文风：master-setting
# ---------------------------------------------------------------------------

class TestMasterSettingWrite:

    @pytest.fixture
    def seeded(self, project: Path) -> Path:
        path = project / ".story-system" / "MASTER_SETTING.json"
        path.write_text(
            json.dumps({"master_constraints": {"core_tone": "原调性"}, "override_policy": {"locked": []}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_update_persists(self, client, seeded: Path):
        response = client.put(
            "/api/style/master-setting",
            headers=ORIGIN,
            json={"master_constraints": {"core_tone": "新调性"}},
        )
        assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"

        data = json.loads(seeded.read_text(encoding="utf-8"))
        assert data["master_constraints"]["core_tone"] == "新调性"

    def test_partial_update_keeps_other_keys(self, client, seeded: Path):
        seeded.write_text(
            json.dumps({"master_constraints": {"core_tone": "A", "pov": "第三人称"}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        client.put("/api/style/master-setting", headers=ORIGIN,
                   json={"master_constraints": {"core_tone": "B"}})

        data = json.loads(seeded.read_text(encoding="utf-8"))
        assert data["master_constraints"]["core_tone"] == "B"
        assert data["master_constraints"]["pov"] == "第三人称", "部分更新不该抹掉其它字段"

    def test_locked_field_rejected(self, client, seeded: Path):
        seeded.write_text(
            json.dumps({"master_constraints": {"core_tone": "锁定值"},
                        "override_policy": {"locked": ["master_constraints.core_tone"]}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        response = client.put("/api/style/master-setting", headers=ORIGIN,
                              json={"master_constraints": {"core_tone": "想改"}})
        assert response.status_code == 403
        data = json.loads(seeded.read_text(encoding="utf-8"))
        assert data["master_constraints"]["core_tone"] == "锁定值", "被锁字段不该被改动"

    def test_missing_file_returns_404(self, client, project: Path):
        response = client.put("/api/style/master-setting", headers=ORIGIN,
                              json={"master_constraints": {"core_tone": "x"}})
        assert response.status_code == 404

    def test_bad_payload_returns_400(self, client, seeded: Path):
        response = client.put("/api/style/master-setting", headers=ORIGIN,
                              json={"master_constraints": "不是对象"})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 文风：prompts
# ---------------------------------------------------------------------------

class TestPromptWrites:

    def test_create_then_update_then_delete(self, client, project: Path):
        prompts_dir = project / "设定集" / "prompts"

        created = client.post("/api/style/prompts", headers=ORIGIN,
                              json={"name": "测试提示词", "content": "第一版"})
        assert created.status_code == 200, f"创建失败：{created.status_code} {created.text[:300]}"
        target = prompts_dir / "测试提示词.md"
        assert target.is_file(), "创建成功但文件不在"
        assert "第一版" in target.read_text(encoding="utf-8")

        updated = client.put("/api/style/prompts/测试提示词.md", headers=ORIGIN,
                             json={"content": "第二版"})
        assert updated.status_code == 200, f"更新失败：{updated.status_code} {updated.text[:300]}"
        assert "第二版" in target.read_text(encoding="utf-8")

        deleted = client.delete("/api/style/prompts/测试提示词.md", headers=ORIGIN)
        assert deleted.status_code == 200, f"删除失败：{deleted.status_code} {deleted.text[:300]}"
        assert not target.exists(), "删除返回成功但文件还在"

    def test_duplicate_create_returns_409(self, client):
        body = {"name": "重复名", "content": "内容"}
        assert client.post("/api/style/prompts", headers=ORIGIN, json=body).status_code == 200
        assert client.post("/api/style/prompts", headers=ORIGIN, json=body).status_code == 409

    @pytest.mark.parametrize("bad_name", ["../逃逸", "带/斜杠", "带\\反斜杠", "带:冒号", "带*星号"])
    def test_rejects_path_traversal_names(self, client, project: Path, bad_name):
        response = client.post("/api/style/prompts", headers=ORIGIN,
                               json={"name": bad_name, "content": "x"})
        assert response.status_code == 400, f"{bad_name!r} 未被拒绝：{response.status_code}"
        # 关键：确认没有在 prompts 目录之外产生文件
        escaped = list(project.rglob("*.md"))
        assert not escaped, f"非法文件名产生了文件：{escaped}"


# ---------------------------------------------------------------------------
# 文件写入
# ---------------------------------------------------------------------------

class TestFileWrite:

    def _seed_chapter(self, project: Path) -> Path:
        chapter = project / "正文" / "第1卷" / "第0001章-测试.md"
        chapter.parent.mkdir(parents=True, exist_ok=True)
        chapter.write_text("原文", encoding="utf-8")
        return chapter

    def test_write_persists(self, client, project: Path):
        chapter = self._seed_chapter(project)
        response = client.put("/api/files/write", headers=ORIGIN,
                              json={"path": "正文/第1卷/第0001章-测试.md", "content": "新内容"})
        assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
        assert chapter.read_text(encoding="utf-8") == "新内容"

    def test_backup_created(self, client, project: Path):
        chapter = self._seed_chapter(project)
        client.put("/api/files/write", headers=ORIGIN,
                   json={"path": "正文/第1卷/第0001章-测试.md", "content": "新内容"})
        backup = chapter.with_suffix(chapter.suffix + ".bak")
        assert backup.is_file(), "写入前应留有 .bak"
        assert backup.read_text(encoding="utf-8") == "原文"

    def test_outside_allowed_dirs_rejected(self, client, project: Path):
        secret = project / ".webnovel" / "state.json"
        before = secret.read_text(encoding="utf-8")
        response = client.put("/api/files/write", headers=ORIGIN,
                              json={"path": ".webnovel/state.json", "content": "{}"})
        assert response.status_code == 403
        assert secret.read_text(encoding="utf-8") == before, "SSOT 文件被改写了"

    def test_traversal_rejected(self, client):
        response = client.put("/api/files/write", headers=ORIGIN,
                              json={"path": "../../etc/passwd", "content": "x"})
        assert response.status_code == 403

    def test_missing_file_returns_404(self, client):
        response = client.put("/api/files/write", headers=ORIGIN,
                              json={"path": "正文/不存在.md", "content": "x"})
        assert response.status_code == 404

    def test_missing_fields_returns_400(self, client):
        assert client.put("/api/files/write", headers=ORIGIN, json={}).status_code == 400


# ---------------------------------------------------------------------------
# 运维 / 批量：不真跑 CLI，只验证白名单与参数校验
# ---------------------------------------------------------------------------

class TestActionWhitelist:
    """真实 CLI 调用会写 state.json，冒烟测试里不触发；只验证拒绝路径。"""

    def test_unknown_action_forbidden(self, client):
        for action in ("rebuild-everything", "rm-rf", "delete-chapters"):
            response = client.post(f"/api/actions/{action}", headers=ORIGIN, json={})
            assert response.status_code == 403, f"{action} 未被白名单拒绝"

    def test_unknown_batch_forbidden(self, client):
        for action in ("publish", "export", "oracle"):
            response = client.post(f"/api/batch/{action}", headers=ORIGIN,
                                   json={"chapters": "1-2"})
            assert response.status_code == 403, f"{action} 未被白名单拒绝"


class TestBatchChaptersValidation:

    @pytest.mark.parametrize("bad", [
        "1; rm -rf /", "1 && echo", "$(whoami)", "1|2", "../1", "abc", "",
    ])
    def test_rejects_shell_metacharacters(self, client, bad):
        response = client.post("/api/batch/write", headers=ORIGIN, json={"chapters": bad})
        # 空字符串走 400（chapters 不能为空），其余走格式校验 400
        assert response.status_code == 400, (
            f"{bad!r} 未被拒绝（{response.status_code}）——命令注入面"
        )

    def test_accepts_valid_spec(self, client):
        """合法章节规格不该被格式校验误杀（dry-run 删除，不真改数据）。"""
        response = client.post("/api/batch/delete", headers=ORIGIN,
                               json={"chapters": "1-2,5", "confirm": False})
        # 允许 200（dry-run 输出）或 500（CLI 在空项目上失败），
        # 但**不能**是 400——那说明正则误杀了合法输入。
        assert response.status_code != 400, f"合法章节规格被格式校验拒绝：{response.text[:200]}"

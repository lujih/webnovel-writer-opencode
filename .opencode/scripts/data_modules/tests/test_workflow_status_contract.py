"""/api/workflow/status 的响应形状契约（第5轮）。

后端 `all_chapters_progress` 返回的是**按章聚合的摘要**：
    {"3": {"stage": "REVIEWING", "complete": false, "steps": 3}}

而前端曾按「阶段名做键的映射」读它——`stages['PLANNING'] !== undefined`
与 `stages._current_stage`。这两个键在后端形状里都不存在，于是 WORKFLOW
卡片里五个进度点**永远是灰的、当前阶段永远不高亮**：一个静默的显示错误，
不报错、不留痕。

现在前端改为由 `stage` 在阶段序列中的位置派生。阶段顺序必须与
`workflow_checkpoint.STAGES` 一致，否则「已走过的阶段」会算错。

本文件把两侧形状钉死：后端仍是聚合摘要（CLI 的 find_interrupted 依赖
info["complete"]，不能改成阶段映射）；前端按 stage 派生。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import create_app  # noqa: E402
from data_modules.workflow_checkpoint import STAGES, checkpoint  # noqa: E402


@pytest.fixture
def client(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return TestClient(create_app(project_root=tmp_path), raise_server_exceptions=False)


def test_returns_aggregated_summary_not_stage_keyed_map(client, tmp_path):
    for stage in ("PLANNING", "DRAFTING", "REVIEWING"):
        checkpoint(3, stage, tmp_path)
    checkpoint(4, "PLANNING", tmp_path)

    payload = client.get("/api/workflow/status").json()

    assert set(payload) == {"3", "4"}, f"键应只含章号，实际: {set(payload)}"
    assert payload["3"] == {"stage": "REVIEWING", "complete": False, "steps": 3}
    assert payload["4"]["stage"] == "PLANNING"
    assert payload["4"]["complete"] is False


def test_complete_flag_true_only_at_committed(client, tmp_path):
    for stage in STAGES[:-1]:
        checkpoint(1, stage, tmp_path)
    assert client.get("/api/workflow/status").json()["1"]["complete"] is False

    checkpoint(1, "COMMITTED", tmp_path)
    assert client.get("/api/workflow/status").json()["1"]["complete"] is True


def test_no_stage_name_keys_leak_into_payload(client, tmp_path):
    """前端曾按这些键取值——它们不得出现在响应里，否则会再次掩盖不匹配。"""
    checkpoint(1, "DRAFTING", tmp_path)
    info = client.get("/api/workflow/status").json()["1"]

    for stage in STAGES:
        assert stage not in info, f"响应里出现了阶段名键 {stage}"
    assert "_current_stage" not in info


def test_frontend_stage_sequence_matches_backend():
    """前端 WORKFLOW_STAGES 必须与后端 STAGES 同序，否则「已走过」算错。"""
    frontend = REPO_ROOT / ".opencode" / "dashboard" / "frontend" / "src" / "pages" / "OverviewPage.jsx"
    source = frontend.read_text(encoding="utf-8")
    marker = "const WORKFLOW_STAGES = ["
    assert marker in source, "前端缺少 WORKFLOW_STAGES 常量"
    line = next(l for l in source.splitlines() if marker in l)
    frontend_stages = tuple(
        part.strip().strip("'\"")
        for part in line.split("[", 1)[1].rsplit("]", 1)[0].split(",")
        if part.strip()
    )
    assert frontend_stages == tuple(STAGES), (
        f"前端阶段序列 {frontend_stages} 与后端 {tuple(STAGES)} 不一致"
    )

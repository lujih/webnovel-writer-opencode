#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard 写端点必须真的把数据写进去。

背景（2026-10-20 实测发现，P0）：

`POST/DELETE /api/style/anti-patterns` 长期返回 500，但没有任何测试发现，
因为既有断言全部只看状态码——`test_dashboard_csrf_coverage.py` 的反向验证
写的是「不是 403 就算通过」，而 500 恰好满足那个条件。

根因：处理器用 `with _locked_anti_patterns()` 持有 `.lock`，随后调用
`_atomic_write_json()` → `security_utils.atomic_write_json()` 内部**再次**
acquire 同一把锁。filelock 不可重入，第二次 acquire 等到超时后抛
`AtomicWriteError`，被 FastAPI 吞成 500。

因此本文件的核心原则是：**断言落盘后的文件内容**，而不是响应状态码。
状态码 200 但文件没变，对用户来说与失败没有区别。
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

# Host 头守卫只放行本机 Host，而 Starlette TestClient 默认发 `Host: testserver`。
# 把 base_url 钉成回环地址即可，不必给生产白名单开后门。
_LOCAL_BASE_URL = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    app = create_app(project_root=tmp_path)
    # raise_server_exceptions=False：让 500 变成响应而不是测试中断，
    # 否则自锁 bug 会以异常形式炸穿整轮，看不出是哪个断言该红。
    return TestClient(app, base_url=_LOCAL_BASE_URL, raise_server_exceptions=False)


def _anti_patterns_path(project_root: Path) -> Path:
    return project_root / ".story-system" / "anti_patterns.json"


def _read_texts(project_root: Path) -> list[str]:
    path = _anti_patterns_path(project_root)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [str(item.get("text", "")) for item in data if isinstance(item, dict)]


class TestAddAntiPatternPersists:
    """新增反模式：必须 200 且文件真的多一条。"""

    def test_add_returns_200_and_writes_to_disk(self, client, tmp_path):
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": _LOCAL_BASE_URL},
            json={"text": "主角不能突然降智"},
        )
        assert response.status_code == 200, (
            f"新增反模式失败（{response.status_code}）：{response.text[:300]}"
        )
        assert _read_texts(tmp_path) == ["主角不能突然降智"], (
            "响应 200 但文件没落盘——用户以为改了、实际没改"
        )

    def test_add_twice_appends_both(self, client, tmp_path):
        for text in ("第一条", "第二条"):
            response = client.post(
                "/api/style/anti-patterns",
                headers={"Origin": _LOCAL_BASE_URL},
                json={"text": text},
            )
            assert response.status_code == 200, f"写入 {text} 失败：{response.text[:300]}"
        assert _read_texts(tmp_path) == ["第一条", "第二条"]

    def test_duplicate_returns_409_and_does_not_duplicate(self, client, tmp_path):
        body = {"text": "重复项"}
        headers = {"Origin": _LOCAL_BASE_URL}
        assert client.post("/api/style/anti-patterns", headers=headers, json=body).status_code == 200

        response = client.post("/api/style/anti-patterns", headers=headers, json=body)
        assert response.status_code == 409, "重复文本应返回 409"
        assert _read_texts(tmp_path) == ["重复项"], "409 不应写入任何内容"

    def test_empty_text_returns_400_without_writing(self, client, tmp_path):
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": _LOCAL_BASE_URL},
            json={"text": "   "},
        )
        assert response.status_code == 400
        assert _read_texts(tmp_path) == []


class TestDeleteAntiPatternPersists:
    """删除反模式：必须 200 且文件真的少一条。"""

    def _seed(self, project_root: Path, texts: list[str]) -> None:
        path = _anti_patterns_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [{"text": t, "source_table": "dashboard_manual"} for t in texts],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_delete_removes_and_persists(self, client, tmp_path):
        self._seed(tmp_path, ["留下的", "要删的"])

        response = client.delete(
            "/api/style/anti-patterns",
            params={"text": "要删的"},
            headers={"Origin": _LOCAL_BASE_URL},
        )
        assert response.status_code == 200, (
            f"删除反模式失败（{response.status_code}）：{response.text[:300]}"
        )
        assert _read_texts(tmp_path) == ["留下的"]

    def test_delete_missing_returns_404_and_keeps_file(self, client, tmp_path):
        self._seed(tmp_path, ["唯一一条"])

        response = client.delete(
            "/api/style/anti-patterns",
            params={"text": "不存在的"},
            headers={"Origin": _LOCAL_BASE_URL},
        )
        assert response.status_code == 404
        assert _read_texts(tmp_path) == ["唯一一条"], "404 不应改动文件"

    def test_delete_when_file_missing_returns_404(self, client, tmp_path):
        path = _anti_patterns_path(tmp_path)
        if path.is_file():
            path.unlink()
        response = client.delete(
            "/api/style/anti-patterns",
            params={"text": "任意"},
            headers={"Origin": _LOCAL_BASE_URL},
        )
        assert response.status_code == 404


class TestAtomicWriteJsonIsNotSelfDeadlocking:
    """锁语义回归保护：已在持有 .lock 时，内层写不得再次 acquire。

    这条测试直接钉住根因，而不只是钉住某个端点的表现——将来若有新端点
    照抄「外层加锁 + 内层 atomic_write_json」的写法，这里会先红。
    """

    def test_write_inside_held_lock_does_not_timeout(self, tmp_path):
        plugin_root = Path(__file__).resolve().parents[3]
        scripts_dir = plugin_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from security_utils import AtomicWriteError, atomic_write_json

        filelock = pytest.importorskip("filelock")

        target = tmp_path / "guarded.json"
        lock = filelock.FileLock(str(target) + ".lock", timeout=1)
        with lock:
            # use_lock=False 是给「调用方已持锁」场景的逃生口；
            # 不传时保持旧行为（自己加锁），我们只验证逃生口有效。
            atomic_write_json(target, {"ok": True}, use_lock=False)

        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}

    def test_write_with_use_lock_true_inside_held_lock_raises(self, tmp_path):
        """反向确认：没有逃生口时确实会自锁——这正是 P0 的成因。"""
        plugin_root = Path(__file__).resolve().parents[3]
        scripts_dir = plugin_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from security_utils import AtomicWriteError, atomic_write_json

        filelock = pytest.importorskip("filelock")

        target = tmp_path / "guarded.json"
        lock = filelock.FileLock(str(target) + ".lock", timeout=1)
        with lock:
            with pytest.raises(AtomicWriteError):
                atomic_write_json(target, {"ok": True}, use_lock=True)

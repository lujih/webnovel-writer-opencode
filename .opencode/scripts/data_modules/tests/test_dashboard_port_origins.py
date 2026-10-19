#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSRF/CORS 允许源必须跟随实际监听端口。

背景（2026-10-20 实测发现，P0）：

`server.py` 提供 `--port`，且端口被占用时主动提示「使用其他端口: --port 8766」。
但 `_LOCAL_ORIGINS` 曾是写死的 {8765, 5173}，于是用户照提示换端口后：

    浏览器 Origin: http://127.0.0.1:8766
    → _csrf_guard 判定「跨站」
    → 所有 PUT/POST/DELETE 一律 403

面板表面上还能看（GET 不受影响），但任何编辑都"改了没反应"。

修复方式：`create_app(port=...)` 把真实端口并入允许源；不传时保持旧的
默认名单，因此既有调用方与既有测试都不受影响——本文件同时钉住这两点。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import _LOCAL_ORIGINS, _build_local_origins, create_app  # noqa: E402

_DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _client(tmp_path: Path, *, base_url: str, port: int | None = None) -> TestClient:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    app = create_app(project_root=tmp_path, port=port)
    return TestClient(app, base_url=base_url, raise_server_exceptions=False)


class TestLocalOriginsBuilder:
    """纯函数层：端口如何并入允许源。"""

    def test_none_returns_default_set(self):
        assert _build_local_origins(None) == _LOCAL_ORIGINS

    def test_custom_port_is_added(self):
        origins = _build_local_origins(8799)
        assert "http://127.0.0.1:8799" in origins
        assert "http://localhost:8799" in origins

    def test_custom_port_keeps_existing_origins(self):
        """追加而非替换：Vite 5173 反向代理仍要能用。"""
        origins = _build_local_origins(8799)
        assert _LOCAL_ORIGINS <= origins

    def test_default_port_does_not_duplicate(self):
        assert _build_local_origins(8765) == _LOCAL_ORIGINS
        assert _build_local_origins(5173) == _LOCAL_ORIGINS

    @pytest.mark.parametrize("bad", [0, -1, 70000, 999999])
    def test_invalid_port_falls_back_to_default(self, bad):
        """端口越界不该让服务起不来，退回默认名单即可。"""
        assert _build_local_origins(bad) == _LOCAL_ORIGINS


class TestCustomPortAllowsWrites:
    """端到端：换端口后，自带 Origin 的写请求必须放行。"""

    def test_write_from_custom_port_origin_is_allowed(self, tmp_path):
        base = "http://127.0.0.1:8799"
        client = _client(tmp_path, base_url=base, port=8799)

        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": base},
            json={"text": "换端口后也要能写"},
        )
        assert response.status_code == 200, (
            f"自定义端口 {base} 的写请求被拒了（{response.status_code}）："
            f"{response.text[:300]}"
        )

    def test_write_from_custom_port_origin_persists(self, tmp_path):
        """不只是放行——确认真的落盘。"""
        import json

        base = "http://127.0.0.1:8799"
        client = _client(tmp_path, base_url=base, port=8799)

        client.post(
            "/api/style/anti-patterns",
            headers={"Origin": base},
            json={"text": "落盘验证"},
        )
        path = tmp_path / ".story-system" / "anti_patterns.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [item["text"] for item in data] == ["落盘验证"]

    def test_write_from_other_port_still_rejected(self, tmp_path):
        """放行自己的端口，不等于放行所有端口。"""
        base = "http://127.0.0.1:8799"
        client = _client(tmp_path, base_url=base, port=8799)

        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": "http://127.0.0.1:9999"},
            json={"text": "不该成功"},
        )
        assert response.status_code == 403

    def test_evil_origin_still_rejected_on_custom_port(self, tmp_path):
        base = "http://127.0.0.1:8799"
        client = _client(tmp_path, base_url=base, port=8799)

        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": "https://evil.example"},
            json={"text": "不该成功"},
        )
        assert response.status_code == 403
        assert "跨站" in response.text


class TestDefaultBehaviourUnchanged:
    """不传 port 时行为与修复前一致——保护既有调用方。"""

    def test_default_port_write_still_allowed(self, tmp_path):
        client = _client(tmp_path, base_url=_DEFAULT_BASE_URL)
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": _DEFAULT_BASE_URL},
            json={"text": "默认端口仍可用"},
        )
        assert response.status_code == 200

    def test_vite_dev_origin_still_allowed(self, tmp_path):
        client = _client(tmp_path, base_url=_DEFAULT_BASE_URL)
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": "http://localhost:5173"},
            json={"text": "Vite 代理仍可用"},
        )
        assert response.status_code == 200

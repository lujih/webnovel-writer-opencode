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

from dashboard.app import (
    _LOCAL_ORIGINS,
    _build_local_origins,
    _origin_allowed,
    create_app,
)  # noqa: E402

_DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _client(
    tmp_path: Path,
    *,
    base_url: str,
    port: int | None = None,
    host: str | None = None,
) -> TestClient:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    app = create_app(project_root=tmp_path, port=port, host=host)
    return TestClient(app, base_url=base_url, raise_server_exceptions=False)


class TestLocalOriginsBuilder:
    """纯函数层：`_build_local_origins` 返回 (origins, allow_any_ip)。"""

    def test_none_returns_default_set(self):
        origins, any_ip = _build_local_origins(None)
        assert origins == _LOCAL_ORIGINS
        assert any_ip is False

    def test_custom_port_is_added(self):
        origins, _ = _build_local_origins(8799)
        assert "http://127.0.0.1:8799" in origins
        assert "http://localhost:8799" in origins

    def test_custom_port_keeps_existing_origins(self):
        """追加而非替换：Vite 5173 反向代理仍要能用。"""
        origins, _ = _build_local_origins(8799)
        assert _LOCAL_ORIGINS <= origins

    def test_default_port_does_not_grow(self):
        origins, _ = _build_local_origins(8765)
        assert origins == _LOCAL_ORIGINS
        origins2, _ = _build_local_origins(5173)
        assert origins2 == _LOCAL_ORIGINS

    @pytest.mark.parametrize("bad", [0, -1, 70000, 999999])
    def test_invalid_port_falls_back_to_default(self, bad):
        """端口越界不该让服务起不来，退回默认名单即可。"""
        origins, any_ip = _build_local_origins(bad)
        assert origins == _LOCAL_ORIGINS
        assert any_ip is False

    # ---- host 维度 ----

    @pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "", None])
    def test_wildcard_host_enables_any_ip(self, wildcard):
        """通配绑定无法枚举网卡，只能放行任意 IP 字面量。"""
        origins, any_ip = _build_local_origins(8799, wildcard)
        assert any_ip is True
        assert "http://127.0.0.1:8799" in origins

    @pytest.mark.parametrize("loopback", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_host_does_not_enable_any_ip(self, loopback):
        """只监听本机时，别的 IP 访问必须仍被拒。"""
        _, any_ip = _build_local_origins(8799, loopback)
        assert any_ip is False

    def test_specific_lan_host_is_added_exactly(self):
        origins, any_ip = _build_local_origins(8799, "192.168.1.5")
        assert "http://192.168.1.5:8799" in origins
        # 绑具体 IP 不应放宽到任意 IP
        assert any_ip is False

    # ---- _origin_allowed 判定 ----

    def test_no_origin_is_allowed(self):
        """curl / 脚本不带 Origin —— 守卫防的不是它。"""
        origins, any_ip = _build_local_origins(8799)
        assert _origin_allowed(None, origins, any_ip) is True
        assert _origin_allowed("", origins, any_ip) is True

    def test_exact_match_allowed(self):
        origins, any_ip = _build_local_origins(8799)
        assert _origin_allowed("http://127.0.0.1:8799", origins, any_ip) is True

    def test_lan_ip_rejected_when_bound_to_loopback(self):
        origins, any_ip = _build_local_origins(8799, "127.0.0.1")
        assert _origin_allowed("http://192.168.1.5:8799", origins, any_ip) is False

    def test_lan_ip_allowed_when_bound_to_wildcard(self):
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed("http://192.168.1.5:8799", origins, any_ip) is True
        assert _origin_allowed("http://10.0.0.9:8799", origins, any_ip) is True

    def test_domain_never_allowed_even_with_wildcard(self):
        """DNS rebinding 的攻击前提是域名——域名必须精确匹配。"""
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed("https://evil.example", origins, any_ip) is False
        assert _origin_allowed("http://evil.example:8799", origins, any_ip) is False

    def test_wrong_port_rejected_even_with_wildcard(self):
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed("http://192.168.1.5:9999", origins, any_ip) is False

    def test_non_http_scheme_rejected(self):
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed("file://192.168.1.5:8799", origins, any_ip) is False


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


class TestCustomHostAllowsWrites:
    """端到端：`--host 0.0.0.0` 后从局域网 IP 访问，写操作必须能用。

    这是与「端口写死」并列的第二个静默故障：Host 守卫放行任意 IP 字面量
    （GET 正常），但若 Origin 主机名不在名单里，所有编辑都被 CSRF 守卫拒死。
    """

    def test_lan_ip_write_allowed_with_wildcard_host(self, tmp_path):
        base = "http://192.168.1.5:8799"
        client = _client(tmp_path, base_url=base, port=8799, host="0.0.0.0")
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": base},
            json={"text": "局域网写入"},
        )
        assert response.status_code == 200, (
            f"0.0.0.0 监听时局域网 origin 被拒（{response.status_code}）：{response.text[:200]}"
        )

    def test_lan_ip_write_rejected_when_bound_to_loopback(self, tmp_path):
        """只监听本机却用局域网 origin —— 必须拒绝，别把放宽当默认。"""
        base = "http://192.168.1.5:8799"
        client = _client(tmp_path, base_url=base, port=8799, host="127.0.0.1")
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": base},
            json={"text": "不该成功"},
        )
        assert response.status_code == 403

    def test_wildcard_host_still_rejects_domains(self, tmp_path):
        client = _client(tmp_path, base_url="http://127.0.0.1:8799",
                         port=8799, host="0.0.0.0")
        response = client.post(
            "/api/style/anti-patterns",
            headers={"Origin": "https://evil.example"},
            json={"text": "不该成功"},
        )
        assert response.status_code == 403
        assert "跨站" in response.text


class TestMalformedIpLiterals:
    """每段必须 0-255：畸形 IP 不能被当成"本机 IP"放行。

    早先 `\d{1,3}` 每段，`999.999.999.999` 也能过——真实浏览器不会产生这种
    Origin，但守卫不该建立在"攻击者守规矩"之上。
    """

    @pytest.mark.parametrize("bad", [
        "999.999.999.999", "256.1.1.1", "1.2.3.999", "300.0.0.1",
    ])
    def test_out_of_range_ip_rejected(self, bad):
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed(f"http://{bad}:8799", origins, any_ip) is False

    @pytest.mark.parametrize("ok", ["0.0.0.0", "10.0.0.5", "192.168.1.5", "255.255.255.255"])
    def test_valid_ip_still_allowed(self, ok):
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed(f"http://{ok}:8799", origins, any_ip) is True

    @pytest.mark.parametrize("weird", [
        "null", "http://", "http://:", "://evil", "http://[::1",
        "chrome-extension://abc", "file://192.168.1.5:8799",
    ])
    def test_malformed_origins_never_raise(self, weird):
        """畸形 Origin 只能返回 False，不能让守卫抛异常变成 500。"""
        origins, any_ip = _build_local_origins(8799, "0.0.0.0")
        assert _origin_allowed(weird, origins, any_ip) is False

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSRF 守卫对**全部**写端点的覆盖。

`app.py` 的 `_csrf_guard` 挂在中间件上，对 `POST/PUT/PATCH/DELETE` 一律校验
`Origin`。实现本身是正确的——但既有测试
（`test_dashboard_csrf.py`）只覆盖了 `/api/actions/*` 四个端点：

    POST /api/actions/ssot-verify
    POST /api/actions/ssot-rebuild
    POST /api/actions/entity-clean
    POST /api/actions/batch

而面板还有 **7 个写端点**没有任何 CSRF 回归保护：

    PUT    /api/files/write                  ← 任意文件写（受 path_guard 约束）
    PUT    /api/style/master-setting         ← 改设定集
    POST   /api/style/anti-patterns          ← 改反模式
    DELETE /api/style/anti-patterns
    POST   /api/style/prompts
    PUT    /api/style/prompts/{filename}
    DELETE /api/style/prompts/{filename}

这些端点的危害不亚于 actions：跨站页面可以借此改写用户的 MASTER_SETTING
与提示词。之所以"现在没出事"，只是因为中间件恰好覆盖了所有写方法——
一旦有人重构（例如把守卫挪进 actions 的处理函数里），style 端点就会静默
失去保护，而现有测试不会响。

本文件把"中间件覆盖全部写方法"这条不变式钉死：不依赖具体端点清单，而是
**从 app.routes 里枚举所有写方法路由**，逐个验证跨站 Origin 被拒。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
REPO = SCRIPTS.parents[1]
DASHBOARD = REPO / ".opencode"

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

#: 与 test_dashboard_csrf.py 一致的本地 base_url（Host 守卫要求 loopback）
_LOCAL_BASE_URL = "http://127.0.0.1:8765"

#: 一个明确不属于本面板的源
_EVIL_ORIGIN = "https://evil.example"

_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture(scope="module")
def app():
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
    from dashboard.app import create_app

    import tempfile

    root = Path(tempfile.mkdtemp())
    (root / ".webnovel").mkdir(parents=True)
    (root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return create_app(project_root=root)


@pytest.fixture(scope="module")
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=_LOCAL_BASE_URL)


def _write_routes(app) -> list[tuple[str, str]]:
    """从应用里枚举所有写方法路由（方法, 路径）。"""
    found = []
    for route in getattr(app, "routes", []):
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not path:
            continue
        for method in sorted(methods & set(_WRITE_METHODS)):
            found.append((method, path))
    return found


class TestGuardCoversEveryWriteRoute:
    """核心不变式：任何写方法路由都必须拒绝跨站 Origin。"""

    def test_there_are_write_routes(self, app):
        routes = _write_routes(app)
        assert routes, "没有枚举到任何写路由——枚举逻辑需要更新"

    def test_every_write_route_rejects_cross_origin(self, client, app):
        routes = _write_routes(app)
        assert routes

        unprotected = []
        for method, path in routes:
            # 把路径参数替换成占位值，让请求能到达守卫层
            url = path
            for segment in path.split("/"):
                if segment.startswith("{") and segment.endswith("}"):
                    url = url.replace(segment, "probe")

            response = client.request(
                method, url,
                headers={"Origin": _EVIL_ORIGIN},
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            # 403 = 被 CSRF 守卫拦下（这是期望）；其他状态码说明守卫没生效，
            # 或者请求在到达业务逻辑前因别的原因失败——后者也算"没被 CSRF 保护"，
            # 但要区分开，所以只把 403 视为通过。
            if response.status_code != 403:
                unprotected.append((method, path, response.status_code))

        assert not unprotected, (
            f"这些写端点没有拒绝跨站 Origin：{unprotected}。"
            f"_csrf_guard 应当对 POST/PUT/PATCH/DELETE 一律校验。"
        )

    def test_local_origin_is_allowed(self, client, app):
        """反向验证：本地源不该被误拦（否则面板自己用不了）。

        ⚠️ 这条断言**曾经**写成 `status_code != 403 or "跨站" not in text`，
        于是 500 也能通过——2026-10 的「反模式写入 100% 失败」P0 正是借此
        藏了很久：处理器在锁上自锁后抛 500，测试却认为"没被 CSRF 拦，很好"。

        CSRF 守卫的职责只是「不要把本地源当成跨站」。判断它尽职与否，
        应当看**是否被 CSRF 拒绝**，而不是看最终状态码是否为 2xx——
        业务层返回 400/404/409/422 都是正常拒绝，属于"守卫已放行、
        处理器在正常校验"，必须与"守卫误拦"区分开。
        """
        for method, path in _write_routes(app):
            url = path
            for segment in path.split("/"):
                if segment.startswith("{") and segment.endswith("}"):
                    url = url.replace(segment, "probe")
            response = client.request(
                method, url,
                headers={"Origin": _LOCAL_BASE_URL},
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            # 只有被 CSRF 守卫拦下才算失败；业务层拒绝是允许的。
            blocked_by_csrf = response.status_code == 403 and "跨站" in response.text
            assert not blocked_by_csrf, (
                f"{method} {path} 把本地源也拒了：{response.text[:200]}"
            )
            # 5xx 说明处理器崩了——这与 CSRF 无关，但同样必须报出来，
            # 否则「能通过但一用就 500」的端点会继续蒙混过关。
            assert response.status_code < 500, (
                f"{method} {path} 在本地源下返回 {response.status_code}（服务端错误），"
                f"说明处理器自身有问题：{response.text[:300]}"
            )


class TestGuardAppliesToStyleEndpoints:
    """style 端点是最容易被忽略的一组（既有测试没覆盖）。"""

    STYLE_ROUTES = [
        ("PUT", "/api/style/master-setting"),
        ("POST", "/api/style/anti-patterns"),
        ("DELETE", "/api/style/anti-patterns"),
        ("POST", "/api/style/prompts"),
    ]

    @pytest.mark.parametrize("method,path", STYLE_ROUTES)
    def test_rejects_cross_origin(self, client, method, path):
        response = client.request(
            method, path,
            headers={"Origin": _EVIL_ORIGIN},
            json={} if method in ("POST", "PUT") else None,
        )
        assert response.status_code == 403, (
            f"{method} {path} 未拒绝跨站写请求（返回 {response.status_code}）"
        )

    def test_files_write_rejects_cross_origin(self, client):
        response = client.put(
            "/api/files/write",
            headers={"Origin": _EVIL_ORIGIN},
            json={"path": "正文/x.md", "content": "pwned"},
        )
        assert response.status_code == 403


class TestReadEndpointsNotBlockedByCsrf:
    """CSRF 只管写；GET 由 Host 守卫负责（另一条测试覆盖）。"""

    @pytest.mark.parametrize("path", ["/api/project/info", "/api/commits"])
    def test_get_with_evil_origin_is_not_403_by_csrf(self, client, path):
        response = client.get(path, headers={"Origin": _EVIL_ORIGIN})
        # 允许 200（数据可能为空）或 4xx（其他原因），但不应是 CSRF 的 403 文案
        assert not (response.status_code == 403 and "跨站" in response.text), (
            f"GET {path} 被 CSRF 守卫拦了——CSRF 只管写操作"
        )

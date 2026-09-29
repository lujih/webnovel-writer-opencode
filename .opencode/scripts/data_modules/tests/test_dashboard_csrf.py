"""dashboard 写操作的 CSRF 守卫（第5轮 dashboard 域）。

CORS 只约束**读取**响应，不阻止副作用。跨站的 <form> POST、以及
content-type 为 text/plain 的简单 fetch 都不触发预检，服务端照常执行——
只是把响应挡在浏览器外。于是任何网页都能对 127.0.0.1:8765 发起：

  POST /api/actions/ssot-rebuild     → 重写 state.json
  POST /api/actions/entity-clean     → 把实体标记为 invalid
  POST /api/actions/batch            → 批量写章节（300s 超时）

守卫规则：写方法（POST/PUT/PATCH/DELETE）若携带 Origin，则必须在允许名单内。
浏览器对跨站写请求必定带 Origin；无 Origin（curl / 本地脚本）放行——能发起
本地请求的进程本就有完整文件系统权限，此处防的不是它。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import _LOCAL_ORIGINS, create_app  # noqa: E402

MUTATING = [
    ("POST", "/api/actions/ssot-verify"),
    ("POST", "/api/actions/ssot-rebuild"),
    ("POST", "/api/actions/entity-clean"),
    ("POST", "/api/actions/batch"),
]


@pytest.fixture
def client(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    app = create_app(project_root=tmp_path)
    return TestClient(app, raise_server_exceptions=False)


class TestCrossOriginWritesRejected:
    @pytest.mark.parametrize("method,path", MUTATING)
    def test_cross_origin_post_is_blocked(self, client, method, path):
        r = client.request(method, path, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, f"{path} 接受了跨站写请求（Origin=evil.example）"

    def test_blocked_request_never_reached_the_handler(self, client):
        """被拦的请求不能真的执行 CLI——403 必须发生在处理器之前。"""
        r = client.post("/api/actions/ssot-rebuild",
                        headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert "拒绝跨站写请求" in r.text

    def test_other_methods_blocked_too(self, client):
        assert client.delete("/api/style/prompts/x",
                             headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.put("/api/style/global",
                          headers={"Origin": "https://evil.example"},
                          json={"x": 1}).status_code == 403


class TestSameOriginAndLocalAllowed:
    @pytest.mark.parametrize("origin", sorted(_LOCAL_ORIGINS))
    def test_allowed_origins_pass_the_guard(self, client, origin):
        """允许名单内的源必须正常通过（否则会误伤 vite dev server 与面板自身）。"""
        r = client.post("/api/actions/ssot-verify", headers={"Origin": origin})
        assert r.status_code != 403, f"合法来源 {origin} 被误伤"

    def test_no_origin_header_is_allowed(self, client):
        """curl / 脚本不带 Origin，应放行。"""
        r = client.post("/api/actions/ssot-verify")
        assert r.status_code != 403, "无 Origin 的本地调用被误伤"


class TestReadsUnaffected:
    def test_get_is_never_blocked(self, client):
        """CSRF 只针对写操作，GET 必须不受影响。"""
        r = client.get("/api/project", headers={"Origin": "https://evil.example"})
        assert r.status_code != 403, "GET 被 CSRF 守卫误伤"


class TestAllowlistIntegrity:
    def test_cors_and_csrf_share_one_source_of_truth(self):
        """CORS 名单与 CSRF 名单必须是同一份，防止改一处漏一处。"""
        from dashboard import app as app_mod

        assert _LOCAL_ORIGINS == app_mod._LOCAL_ORIGINS
        # 名单只应是本机回环，不得混入任何外部域
        for origin in _LOCAL_ORIGINS:
            host = origin.split("//", 1)[1].split(":", 1)[0]
            assert host in ("127.0.0.1", "localhost"), f"允许名单含非本机源: {origin}"

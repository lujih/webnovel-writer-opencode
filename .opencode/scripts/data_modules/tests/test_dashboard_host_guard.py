"""dashboard 的 Host 头守卫（DNS rebinding 的**读取**路径）。

`_csrf_guard` 只守写方法：浏览器对跨站写请求必带 Origin，非本机源一律 403。
但 **GET 没有任何守卫**，而 GET 能读走整本书：

    fetch('http://evil.com:8765/api/project/info')
      └─ evil.com 解析到 127.0.0.1（rebinding）
         └─ 浏览器认为同源（Host 与 Origin 都是 evil.com）
            └─ state.json 全文可读（设定 / 进度 / 全部实体）

`_LOCAL_ORIGINS` 拦不住它——那是"允许哪些**源**"，而这里 Host 与 Origin 都是
攻击者的域名。唯一有效的判据是 **Host 头是否指向本机**。

守卫的取舍：
- 域名只放行 localhost。
- 任何 IPv4 字面量放行：`--host 0.0.0.0` 时用户会用 192.168.x.x 访问，写死会
  误伤；而攻击者的域名在浏览器里不可能呈现成 IP 字面量，放行 IP 不削弱防护。
- 回环 IPv6 放行。
- `WEBNOVEL_DASHBOARD_ALLOW_HOSTS` 供反代/自定义域名显式放开。
- 刻意**不**放行 TestClient 默认的 `testserver`：安全名单里留测试后门，早晚会被
  拿去当放宽的挡箭牌。测试侧改用回环 base_url。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import _host_is_local, create_app  # noqa: E402

LOCAL_BASE_URL = "http://127.0.0.1:8765"

# Host 头 → 是否应当放行
ACCEPTED = [
    "localhost",
    "localhost:8765",
    "LOCALHOST:8765",
    "127.0.0.1",
    "127.0.0.1:8765",
    "192.168.1.7:8765",
    "10.0.0.5",
    "[::1]:8765",
    "[::1]",
]

REJECTED = [
    "evil.com",
    "evil.com:8765",
    "localhost.evil.com",          # 后缀伪装
    "127.0.0.1.evil.com",          # 常见 rebinding 域名形态
    "localhost.evil.com:8765",
    "evil.localhost.com:8765",
    "notlocalhost",
    "[::2]:8765",                  # 非回环 v6
    "",                            # 缺失 Host（HTTP/1.1 必须有，防御性拒绝）
    "   ",
]


class TestHostPredicate:
    @pytest.mark.parametrize("host", ACCEPTED)
    def test_local_hosts_are_accepted(self, host):
        assert _host_is_local(host) is True, f"{host!r} 是合法本机 Host，不应拒绝"

    @pytest.mark.parametrize("host", REJECTED)
    def test_foreign_hosts_are_rejected(self, host):
        assert _host_is_local(host) is False, f"{host!r} 必须拒绝（rebinding 面）"

    def test_localhost_suffix_spoof_is_not_a_local_host(self):
        """只做精确匹配，不做 `endswith('localhost')` 之类的后缀放行。"""
        assert _host_is_local("evil-localhost:8765") is False

    def test_env_allowlist_can_extend(self, monkeypatch):
        """反代场景：显式列出的域名要能放开，否则部署即锁死。"""
        assert _host_is_local("novel.internal:8765") is False
        monkeypatch.setenv("WEBNOVEL_DASHBOARD_ALLOW_HOSTS", "novel.internal,other.lan")
        assert _host_is_local("novel.internal:8765") is True
        assert _host_is_local("other.lan") is True
        assert _host_is_local("third.lan") is False

    def test_testserver_is_not_allowlisted(self):
        """生产名单里不留测试后门——测试用回环 base_url。"""
        assert _host_is_local("testserver") is False


@pytest.fixture
def client(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return TestClient(create_app(project_root=tmp_path), base_url=LOCAL_BASE_URL,
                      raise_server_exceptions=False)


class TestReadsAreProtectedAgainstRebinding:
    """核心断言：rebinding 页面的 GET 必须在中间件层被 400 打回。"""

    @pytest.mark.parametrize("path", [
        "/api/project/info",
        "/api/story-runtime/health",
        "/api/entities/list",
    ])
    def test_get_with_foreign_host_is_rejected(self, client, path):
        r = client.get(path, headers={"Host": "evil.com"})
        assert r.status_code == 400, (
            f"{path} 接受了 Host: evil.com —— rebinding 页面能读走整本书"
        )
        assert "拒绝非本机 Host" in r.text

    def test_rebinding_shape_passes_origin_but_fails_host(self, client):
        """rebinding 请求的 Origin 也是 evil.com：CSRF 守卫只处理写，
        读取由 Host 守卫兜住——两者必须各管一段。"""
        r = client.get(
            "/api/project/info",
            headers={"Host": "evil.com", "Origin": "http://evil.com"},
        )
        assert r.status_code == 400

    def test_local_host_still_reads_fine(self, client):
        r = client.get("/api/project/info", headers={"Host": "localhost:8765"})
        assert r.status_code == 200, "本机 Host 被误挡——面板将完全不可用"

    def test_lan_ip_host_is_allowed(self, client):
        """--host 0.0.0.0 时用户从局域网用 IP 访问，不能误伤。"""
        r = client.get("/api/project/info", headers={"Host": "192.168.1.7:8765"})
        assert r.status_code == 200

    def test_writes_are_also_host_checked(self, client):
        """写路径两道守卫都过一遍：非本机 Host 400，本机 Host + 坏 Origin 403。"""
        r = client.post("/api/actions/ssot-verify", headers={"Host": "evil.com"})
        assert r.status_code == 400
        r = client.post(
            "/api/actions/ssot-verify",
            headers={"Host": "localhost:8765", "Origin": "https://evil.example"},
        )
        assert r.status_code == 403

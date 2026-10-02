"""npm 发布 workflow 的认证方式契约（Trusted Publishing / OIDC）。

背景：2026-10-02 排查出 npm 发布连续失败两周。根因链条是

    长期 NPM_TOKEN 过期 → 报错被后续推送淹没 → 无人察觉

最终改用 Trusted Publishing：CI 用 GitHub 的短期 OIDC 身份换取 npm 凭证，
不再有需要轮换的长期凭据。这里钉住该形态，防止后续被改回 token 方式——
那会把已经消除的一类故障重新引回来。

同时钉住一个容易漏的前提：**npm CLI 必须 ≥ 11.5.1**。OIDC 令牌交换是该版本
引入的，而 node 22 自带 npm 10.9.9，认不出 OIDC 会退回找令牌、最终报
E401/EOTP。所以 workflow 必须在 publish 之前自升级 npm。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "npm-publish.yml"

# OIDC 令牌交换的最低 npm 版本；低于此值 npm 不认得 OIDC。
MIN_NPM_MAJOR = 11
MIN_NPM_MINOR = 5


@pytest.fixture(scope="module")
def text():
    return WORKFLOW.read_text(encoding="utf-8")


def _steps(text):
    """粗解析步骤名与 run 体，够用即可。"""
    out, current = [], None
    for line in text.splitlines():
        m = re.match(r"^\s*- (?:name|uses):\s*(.+?)\s*$", line)
        if m:
            if current:
                out.append(current)
            current = {"name": m.group(1), "body": []}
        elif current is not None:
            current["body"].append(line)
    if current:
        out.append(current)
    return out


def _index_of(steps, needle):
    for i, s in enumerate(steps):
        if needle in s["name"] or needle in "\n".join(s["body"]):
            return i
    return -1


class TestUsesTrustedPublishing:
    def test_id_token_write_permission(self, text):
        assert re.search(r"^\s*id-token:\s*write\s*$", text, re.M), (
            "缺 permissions.id-token: write——没有它就换不到 OIDC 身份"
        )

    def test_no_auth_token_env(self, text):
        """publish 步骤不得再注入 NODE_AUTH_TOKEN。

        带 token 会让 npm 优先走令牌路径，账号一旦强制 2FA 就退回 EOTP——
        那正是本 workflow 连续失败两周的直接原因。
        """
        publish_body = "\n".join(_steps(text)[-2]["body"]) if len(_steps(text)) > 1 else ""
        assert "NODE_AUTH_TOKEN:" not in text, (
            "workflow 仍在注入 NODE_AUTH_TOKEN；应改用 Trusted Publishing（OIDC）"
        )
        assert "secrets.NPM_TOKEN" not in text

    def test_publish_uses_provenance(self, text):
        assert "--provenance" in text, (
            "Trusted Publishing 下应带 --provenance（npm 侧可据此关联构建来源）"
        )


class TestNpmVersionPrerequisite:
    def test_upgrades_npm_before_publish(self, text):
        steps = _steps(text)
        upgrade = _index_of(steps, "npm@latest")
        publish = _index_of(steps, "npm publish")
        assert upgrade != -1, "缺少 npm 升级步骤"
        assert publish != -1, "找不到 publish 步骤"
        assert upgrade < publish, (
            f"npm 必须在 publish 之前升级（upgrade@{upgrade} > publish@{publish}）"
        )

    def test_documented_minimum(self, text):
        assert str(MIN_NPM_MAJOR) in text and str(MIN_NPM_MINOR) in text, (
            f"应记录 OIDC 所需的最低 npm 版本 {MIN_NPM_MAJOR}.{MIN_NPM_MINOR}"
        )


class TestVersionBumpGuards:
    """版本计算那段的两个坑，回归防护。"""

    def test_no_silent_fallback_to_base_tag(self, text):
        """`npm view ... || echo "$BASE_TAG"` 会算出早已发布的 ${BASE_TAG}-1，
        每次都撞 EPUBLISHCONFLICT，且报错与真实原因无关。"""
        assert not re.search(r'npm view[^|]*\|\|\s*echo', text), (
            "禁止把 npm view 的失败静默回落到 BASE_TAG"
        )

    def test_fails_loudly_when_registry_unreachable(self, text):
        assert "exit 1" in text, "查不到版本时应明确失败而非继续"

    def test_checks_version_not_already_taken(self, text):
        assert re.search(r'npm view "\$PKG@\$VER"', text), (
            "发布前应检查目标版本是否已被占用，避免 EPUBLISHCONFLICT"
        )


class TestManualRetryEntry:
    def test_has_workflow_dispatch(self, text):
        """修好配置后要能主动重发，不能靠制造空提交触发。"""
        assert re.search(r"^\s*workflow_dispatch:\s*$", text, re.M)
"""6 个 agent 的 OpenCode V2 `permissions:` 契约。

迁移动机（依据 V1 /docs/permissions 与 V2 /docs/permissions）：

V1 文档明写 "the legacy `tools` boolean config is deprecated and has been
merged into `permission`"，而两代的默认策略都是**放行**：

    { "action": "*", "resource": "*", "effect": "allow" }

也就是说仓库里那 6 个 `tools: {read: true, ...}` **从未构成白名单**——未列出的
工具回落到默认放行。只把 true 翻成 allow 规则的话，迁移前后行为完全一致，
约束仍是零。要让声明的意图真正生效，必须先 deny-all 再逐条放行。

本文件钉住四条容易踩空的不变式，其中第 3 条是迁移过程中真踩到的坑。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"

AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    assert m, f"{path.name} 缺少 frontmatter"
    return m.group(1)


def _rules(path: Path) -> list[tuple[str, str, str]]:
    """解析 permissions 列表为 (action, resource, effect) 三元组，保持顺序。"""
    fm = _frontmatter(path)
    assert "permissions:" in fm, f"{path.name} 还没有迁移到 permissions"
    assert not re.search(r"^tools:", fm, re.M), f"{path.name} 仍残留 legacy tools:"
    return [
        (m.group(1), m.group(2), m.group(3))
        for m in re.finditer(
            r'^\s*- action:\s*"?([^"\n]+?)"?\s*\n\s*resource:\s*"?([^"\n]+?)"?\s*\n'
            r'\s*effect:\s*(\w+)\s*$',
            fm, re.M)
    ]


def _effect_of(rules, action, resource="*", default="ask"):
    """最后命中的规则生效（V2 语义）。

    匹配规则：规则的 action 等于查询 action，或规则 action 为 `*`（兜底）；
    再看 resource——查询用固定 resource 时，规则的 resource 也要匹配
    （`*` 通配一切，其余按字面量比）。

    这里刻意只查**单个 action + 单个 resource** 的最终效果，因为测试要的
    是「某条具体规则有没有被后面的规则盖掉」——例如 SSOT 的
    `edit: *.webnovel/state.json deny` 排在 `edit: * allow` 之后，
    对该具体路径而言 deny 生效，但对普通章节文件的 edit 仍是 allow。
    """
    effect = default
    for rule_action, rule_resource, rule_effect in rules:
        if rule_action not in (action, "*"):
            continue
        if rule_resource != "*" and rule_resource != resource:
            continue
        effect = rule_effect
    return effect


@pytest.fixture(params=AGENT_FILES, ids=lambda p: p.name)
def agent(request):
    return request.param


class TestMigratedShape:
    def test_uses_v2_permissions(self, agent):
        rules = _rules(agent)
        assert rules, f"{agent.name} 的 permissions 是空的"

    def test_first_rule_is_deny_all(self, agent):
        """没有 deny-all 就等于没迁移——V2 默认放行一切。"""
        assert _rules(agent)[0] == ("*", "*", "deny")

    def test_uses_v2_action_names(self, agent):
        """V1 的 bash/task/write 不是 V2 动作名。"""
        for action, _r, _e in _rules(agent):
            assert action not in {"bash", "task", "write", "patch"}, (
                f"{agent.name} 仍用 V1 动作名 {action}"
                )

    def test_deny_all_comes_before_allows(self, agent):
        """顺序即优先级；allow 排在 deny-all 前面会被整体拒绝吃掉。"""
        rules = _rules(agent)
        deny_at = next(i for i, r in enumerate(rules) if r == ("*", "*", "deny"))
        assert all(r[2] != "allow" for r in rules[:deny_at]), (
            f"{agent.name} 在 deny-all 之前就有 allow 规则"
        )


class TestExternalDirectoryTrap:
    """书项目在仓库之外，deny * 会连 external_directory 一起拒掉。"""

    def test_external_directory_is_restored_to_ask(self, agent):
        assert _effect_of(_rules(agent), "external_directory") == "ask", (
            f"{agent.name} 没有把 external_directory 放回 ask——"
            "agent 读不到 E:\\workspace\\webnovel2 下的正文与 state.json"
        )


class TestLeastPrivilege:
    def test_read_only_discovery_always_allowed(self, agent):
        """glob 是只读发现工具，各 agent 都要定位章节/参考文件。"""
        for action in ("read", "glob", "grep", "shell"):
            assert _effect_of(_rules(agent), action) == "allow", (
                f"{agent.name} 缺 {action}（V1 默认放行，收紧它属于自造回归）"
            )

    def test_privilege_escalating_actions_are_denied(self, agent):
        """委派 / 联网 / 追问用户都不该由子 agent 自行发起。"""
        for action in ("subagent", "skill", "webfetch", "websearch", "question"):
            assert _effect_of(_rules(agent), action) == "deny", (
                f"{agent.name} 意外放行了 {action}"
            )


class TestSSOTProtectedPaths:
    """SSOT 受保护文件在 permissions 层就要拦住——比运行时拒绝更靠前一层。

    与 .opencode/plugins/write-guard.js 是同一份清单但两道闸门：
    V2 文档对 permissions 的 deny 说会把工具"从模型可见集整体移除"，
    即模型根本拿不到 edit；write-guard 则在 tool.execute.before 抛错。
    两者缺一可：只有 plugin 时模型看得见工具，只有 permissions 时绕过配置
    直接调 hook 就漏了。
    """

    PROTECTED = (
        ".webnovel/state.json",
        ".webnovel/index.db",
        ".webnovel/vectors.db",
        ".webnovel/memory_scratchpad.json",
        ".story-system/events/",
        ".story-system/master_setting.json",
    )

    def _edit_agents(self):
        return [p for p in AGENT_FILES if _effect_of(_rules(p), "edit") == "allow"]

    def test_at_least_some_agent_can_edit(self):
        assert self._edit_agents(), "没有 agent 拿到 edit，迁移把写能力全掐死了"

    @pytest.mark.parametrize("suffix", PROTECTED)
    def test_protected_path_denied_for_every_editing_agent(self, suffix):
        resource = f"*{suffix}"
        for path in self._edit_agents():
            matching = [r for r in _rules(path)
                        if r[0] == "edit" and r[1] == resource]
            assert matching, f"{path.name} 缺少对 {resource} 的 edit deny"
            # 最后命中者生效：deny 必须排在 edit 的 allow 之后
            assert matching[-1][2] == "deny", (
                f"{path.name}: {resource} 的最后一条规则不是 deny，"
                "会被前面的 edit allow 盖过"
            )

    def test_deny_comes_after_the_edit_allow(self, agent):
        rules = _rules(agent)
        allow_at = [i for i, r in enumerate(rules) if r == ("edit", "*", "allow")]
        if not allow_at:
            return  # 只读 agent，没有 edit allow，也就不需要这层
        first_deny = next(i for i, r in enumerate(rules)
                          if r[0] == "edit" and r[2] == "deny")
        assert first_deny > allow_at[0], (
            f"{agent.name}: SSOT deny 排在 edit allow 之前，会被盖过"
        )

    def test_matches_write_guard_plugin_list(self):
        """两份清单必须一致——漂移了就等于有一侧失效。"""
        plugin = (REPO_ROOT / ".opencode" / "plugins" / "write-guard.js").read_text(
            encoding="utf-8")
        for suffix in self.PROTECTED:
            assert f"'{suffix}'" in plugin, (
                f"write-guard.js 不再保护 {suffix}，与 permissions 层清单漂移"
            )


class TestPerAgentToolFit:
    """edit 只能给真正要落盘的 agent，且必须与正文一致。"""

    NEEDS_EDIT = {
        "reviewer.md",           # 正文要求「用 Write 工具将 JSON 写入 ${REVIEW_OUTPUT}」
        "chapter-writer-agent.md",  # 写章节文件
        "data-agent.md",           # 原 tools 声明了 write
        "observer-agent.md",       # 原 tools 声明了 write
    }
    MUST_NOT_EDIT = {
        "context-agent.md",        # 只组装上下文，原 tools 未声明 write
        "deconstruction-agent.md",  # 正文明写「只返回结构化结果，不写任何文件」
    }

    def test_edit_granted_only_where_declared(self, agent):
        expected = agent.name in self.NEEDS_EDIT
        actual = _effect_of(_rules(agent), "edit") == "allow"
        assert actual == expected, (
            f"{agent.name} 的 edit 放行={actual}，与原文需求声明 {expected} 不符"
        )

    def test_read_only_agent_body_agrees_with_permissions(self):
        """deconstruction-agent 的正文自述只读，权限必须与之一致。"""
        path = AGENTS_DIR / "deconstruction-agent.md"
        body = path.read_text(encoding="utf-8")
        assert "不写任何文件" in body
        assert _effect_of(_rules(path), "edit") == "deny"


class TestNoFrontmatterName:
    """agent frontmatter 里**不得**有 `name:`——V2 会把 permissions 整个错位。

    实测（2026-10-10，opencode 2.0.26，隔离实验矩阵）：frontmatter 含 `name:`
    时，V2 把同一文件的 `permissions` 解析进 `request.body.permissions`
    （模型请求体位置），顶层生效权限只剩出厂默认 5 条——**全部 SSOT deny
    静默失效**。无 `name:` 则正确追加到顶层（"permission rules append"，
    默认规则在前、本文件规则在后，最后匹配者生效）。

    `name` 本身也是死字段：id/展示名永远取文件名（V2 忽略该值；V1 同样以
    文件名为 id）。即这行零收益、纯破坏。迁移脚本不写它，本测试防回潮。

    为什么本地 pytest 抓不到：测试自己解析 YAML，永远"读得到"规则——错位
    只发生在真实 V2 运行时（`opencode debug agents` 才看得见）。
    """

    def test_no_name_key_in_frontmatter(self, agent):
        fm = _frontmatter(agent)
        assert not re.search(r"^name\s*:", fm, re.M), (
            f"{agent.name} 的 frontmatter 含 name:——OpenCode V2 下会把 "
            "permissions 错位到 request.body，SSOT deny 全部静默失效"
        )

    def test_frontmatter_still_has_permissions(self, agent):
        """删 name 的前提 permissions 还在（防删错对象）。"""
        assert "permissions:" in _frontmatter(agent)

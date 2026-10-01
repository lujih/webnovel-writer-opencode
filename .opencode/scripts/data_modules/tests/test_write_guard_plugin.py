"""write-guard 插件的跨代行为契约。

插件是 .js，pytest 跑不了；这里用 node 直接加载并断言 **两代 hook 形状下
行为完全一致**。这是最容易回归的地方：V1 与 V2 的入参形状不同
（`input.tool`/`output.args` vs `event.tool`/`event.input`），一旦某代忘了
接线或接错字段，守卫会**静默失效**——不报错，AI 就能改写 state.json。

同时钉住：V2 根本不会加载 V1 插件实现（官方 migrate 文档明说），
所以必须双入口；缺任一个都会在升级后无声失效。
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / ".opencode" / "plugins" / "write-guard.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能加载 .js 插件"
)


def _load():
    """在 node 里 import 插件并把 guard 结果转成 JSON 返回。"""
    script = textwrap.dedent(f"""
    import {{ pathToFileURL }} from 'node:url'
    const mod = await import(pathToFileURL({json.dumps(str(PLUGIN))}).href)
    const out = {{}}
    out.hasId = typeof mod.default?.id === 'string' && mod.default.id.length > 0
    out.hasSetup = typeof mod.default?.setup === 'function'
    out.hasServer = typeof mod.default?.server === 'function'
    out.exportsGuard = typeof mod.guard === 'function'

    // 走 V2 形状
    const cases = {json.dumps([])}
    out.results = cases.map(([tool, args]) => {{
      try {{ mod.guard(tool, args); return 'allowed' }}
      catch (e) {{ return 'blocked' }}
    }})
    console.log(JSON.stringify(out))
    """)
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"node 执行失败: {r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def plugin():
    return _load()


CASES = [
    # (tool, args, 期望)
    ("write", {"path": ".webnovel/state.json"}, "blocked"),
    ("edit", {"path": ".story-system/events/0001.event.json"}, "blocked"),
    ("write", {"path": "E:/book/.webnovel/index.db"}, "blocked"),
    ("write", {"file_path": ".story-system/master_setting.json"}, "blocked"),
    ("bash", {"command": "python webnovel.py status > .webnovel/state.json"}, "blocked"),
    ("bash", {"command": "rm -rf .story-system/events/"}, "blocked"),
    ("write", {"path": "正文/第001章.md"}, "allowed"),
    ("read", {"path": ".webnovel/state.json"}, "allowed"),
    ("bash", {"command": "python .opencode/scripts/webnovel.py ssot verify"}, "allowed"),
]


class TestBothHostGenerationsWired:
    def test_v2_entrypoint_present(self, plugin):
        assert plugin["hasId"], "V2 要求默认导出带稳定 id"
        assert plugin["hasSetup"], "缺 setup()：升级到 OpenCode 2 后插件静默不加载"

    def test_v1_entrypoint_present(self, plugin):
        assert plugin["hasServer"], (
            "缺 server()：本机 OpenCode 1.18.34 会加载不了，守卫直接失效"
        )

    def test_guard_is_exported_for_testing(self, plugin):
        assert plugin["exportsGuard"]


def test_v1_and_v2_shapes_agree():
    """把同一批用例分别按 V1 / V2 形状喂进 hook，比对结果必须一致。"""
    script = textwrap.dedent(f"""
    import {{ pathToFileURL }} from 'node:url'
    const mod = await import(pathToFileURL({json.dumps(str(PLUGIN))}).href)

    const hooksV1 = await mod.default.server()
    let v2Event = null
    const fakeCtx = {{
      tool: {{ hook: async (_name, cb) => {{ v2Event = cb }} }},
    }}
    await mod.default.setup(fakeCtx)

    const cases = {json.dumps([[c[0], c[1], c[2]] for c in CASES])}

    const runV1 = async ([tool, args]) => {{
      try {{ await hooksV1['tool.execute.before']({{ tool }}, {{ args }}) ; return 'allowed' }}
      catch {{ return 'blocked' }}
    }}
    const runV2 = async ([tool, args]) => {{
      try {{ v2Event({{ tool, input: args }}); return 'allowed' }}
      catch {{ return 'blocked' }}
    }}

    const v1 = [], v2 = []
    for (const c of cases) {{ v1.push(await runV1(c)); v2.push(await runV2(c)) }}
    console.log(JSON.stringify({{
      v1, v2, expected: cases.map(c => c[2]), v2Hooked: typeof v2Event === 'function' }}))
    """)
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"node 执行失败: {r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])

    assert out["v2Hooked"], "setup() 没有在 ctx.tool.hook 上注册 execute.before"
    assert out["v1"] == out["expected"], f"V1 形状判定不符: {out['v1']} vs {out['expected']}"
    assert out["v2"] == out["expected"], f"V2 形状判定不符: {out['v2']} vs {out['expected']}"
    assert out["v1"] == out["v2"], "两代入参形状给出了不同判定——某一代静默失效了"


class TestProtectedPathsStillProtected:
    """这些路径是 SSOT 的入口，漏一个就是静默数据损坏。"""

    @pytest.mark.parametrize("path", [
        ".webnovel/state.json",
        ".webnovel/index.db",
        ".webnovel/vectors.db",
        ".webnovel/memory_scratchpad.json",
        ".story-system/events/0001.event.json",
        ".story-system/master_setting.json",
        ".story-system/commits/1.json",
    ])
    def test_blocked(self, path):
        script = textwrap.dedent(f"""
        import {{ pathToFileURL }} from 'node:url'
        const mod = await import(pathToFileURL({json.dumps(str(PLUGIN))}).href)
        try {{ mod.guard('write', {{ path: {json.dumps(path)} }}); console.log('allowed') }}
        catch {{ console.log('blocked') }}
        """)
        r = subprocess.run(["node", "--input-type=module", "-e", script],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().endswith("blocked"), f"{path} 未被保护"

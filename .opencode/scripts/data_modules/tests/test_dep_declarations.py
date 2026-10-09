"""代码里的 import 必须在 requirements 中有声明——CI 缺依赖即收集崩溃。

背景（2026-10-09）：Test workflow 自 10/01 起连红 12+ 次，本地却一直全绿。
连环根因：
1. conftest 无条件转发 Python 3.14 才有的 `delete=` 参数 → CI（3.11）收集全灭
   （见 test_conftest_py_compat.py）；
2. 修掉①之后，`export_manager/parser.py` 顶层 `import mistune` 在 CI 报
   ModuleNotFoundError——**本地装过、requirements 没声明**，干净环境必然崩。
   同类硬阻断还有 formats/docx.py 的顶层 `python-docx`。

本测试就是当初那次"手工静态扫描"的固化版：本地环境是超集，跑测试发现不了
缺声明，只有干净 CI 会暴露——与其一轮轮撞 CI，静态对齐。

规则：
- **顶层 import**（模块体，不含函数/类内）：必须声明为必装（requirements）；
- **惰性 import**（函数/类内，产品多为可选+优雅降级）：必须出现在
  requirements 文本里——可以是**注释行的可选依赖说明**（weasyprint/playwright）；
- 本地模块（.opencode 树内同名 .py 或包）与 stdlib 不在此列。
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / ".opencode" / "scripts"
DASH = REPO_ROOT / ".opencode" / "dashboard"
REQ_FILES = [
    SCRIPTS / "requirements.txt",
    REPO_ROOT / ".opencode" / "scripts" / "requirements-dev.txt",
    DASH / "requirements.txt",
]

# import 名 → requirements 里的分发名（不一致的才需要登记）
DIST_NAME = {"docx": "python-docx", "PIL": "Pillow"}
# 明确豁免：与 scripts/ 同名的顶层包（`import scripts.xxx` 风格）
LOCAL_ALIASES = {"scripts"}


def _requirements_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in REQ_FILES if p.is_file())


def _is_local(mod: str) -> bool:
    if mod in LOCAL_ALIASES:
        return True
    # .opencode/ 本身也在 sys.path 上（dashboard 等包与 scripts 平级）
    for base in (SCRIPTS, SCRIPTS / "data_modules", DASH, REPO_ROOT / ".opencode"):
        if (base / f"{mod}.py").is_file() or (base / mod / "__init__.py").is_file():
            return True
    return False


def _imports(path: Path):
    """产出 (模块名, 是否顶层)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in tree.body:  # 顶层：只遍历模块体
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module.split(".")[0], True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for a in sub.names:
                        yield a.name.split(".")[0], False
                elif isinstance(sub, ast.ImportFrom):
                    if sub.level == 0 and sub.module:
                        yield sub.module.split(".")[0], False


def _scan():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    req_text = _requirements_text().lower()
    missing_top, missing_lazy = set(), set()
    for base in (SCRIPTS, DASH):
        for py in base.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for mod, top in _imports(py):
                if mod in stdlib or _is_local(mod):
                    continue
                names = {mod.lower(), DIST_NAME.get(mod, "").lower()} - {""}
                if any(n in req_text for n in names):
                    continue
                (missing_top if top else missing_lazy).add(mod)
    return missing_top, missing_lazy


class TestDeclaredDependenciesMatchImports:
    def test_no_undeclared_top_level_imports(self):
        """顶层 import 缺声明 = 干净环境收集/导入即崩（CI 就是这么红的）。"""
        top, _ = _scan()
        assert not top, (
            f"以下模块在代码顶层 import，却未在 requirements 声明（含注释亦可，"
            f"但必须显式记录）：{sorted(top)}"
        )

    def test_no_undeclared_lazy_imports(self):
        """惰性 import 也须留痕——可选依赖写进 requirements 注释即可。"""
        _, lazy = _scan()
        assert not lazy, (
            f"以下模块仅在函数/类内 import，但 requirements 全文（含注释）"
            f"没有任何记录——可选依赖请在注释中列明：{sorted(lazy)}"
        )

    def test_requirements_files_exist(self):
        for p in REQ_FILES:
            assert p.is_file(), f"缺 {p}"
"""conftest 的 TemporaryDirectory patch 必须兼容 Python 3.11（CI 解释器）。

背景（2026-10-09，Test workflow 连红 6 次的根因）：
`.opencode/scripts/conftest.py` 的 `_SafeTemporaryDirectory` 曾**无条件**把
`delete=` 转发给 `tempfile.TemporaryDirectory`——但 `delete` 是 Python **3.14**
才加入的参数（本机 3.14.5 实测有，3.11 没有）。conftest 在收集前就装上 patch，
于是 CI（test.yml: Python 3.11）上**每一个** TemporaryDirectory 构造都抛
TypeError，整个收集阶段全灭（filelock 导入期的目录探测只是第一个触发者）。

本地全绿、CI 全红的错觉正是这么来的：本机只有 3.14。

这里钉两件事：
1. 静态：转发必须有 `_TEMPDIR_ACCEPTS_DELETE` 守卫，不得退回无条件 `delete=delete`；
2. 运行时：构造实例不得抛 TypeError——3.14 走转发分支，3.11（CI）走丢弃分支，
   两个解释器都必须存活。这正是 CI 上 3.11 分支的真实验证。
"""
import inspect
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFTEST = REPO_ROOT / ".opencode" / "scripts" / "conftest.py"


def _conftest():
    # pytest 已加载过该文件——按**文件路径**找回同一个模块对象，绝不再次
    # import（二次加载会重复执行 _install_safe_tempfile，把 patch 包两层）。
    # 注意模块名不是 "conftest"：scripts/ 有 __init__.py，pytest 实际以
    # 包路径（scripts.conftest）注册。
    target = CONFTEST.resolve()
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if path and Path(path).resolve() == target:
            return mod
    raise AssertionError(f"pytest 未加载 {CONFTEST}？测试执行顺序异常")


class TestGuardIsPresent:
    def test_source_forwards_conditionally(self):
        src = CONFTEST.read_text(encoding="utf-8")
        assert "_TEMPDIR_ACCEPTS_DELETE" in src, "丢失了 3.14 参数探测"
        assert "if _TEMPDIR_ACCEPTS_DELETE" in src, "转发缺少年份守卫"
        # 旧写法 super().__init__(..., delete=delete) 是无条件转发——不许回来
        assert "delete=delete" not in src, (
            "出现无条件 delete=delete 转发：Python 3.11（CI）会 TypeError，"
            "收集阶段全灭"
        )


class TestRuntimeOnThisInterpreter:
    def test_probe_is_bool(self):
        assert isinstance(_conftest()._TEMPDIR_ACCEPTS_DELETE, bool)

    def test_probe_matches_real_signature(self):
        accepts = "delete" in inspect.signature(
            tempfile.TemporaryDirectory.__init__).parameters
        assert _conftest()._TEMPDIR_ACCEPTS_DELETE is accepts, (
            "探测结果与当前解释器真实签名不一致"
        )

    def test_construct_without_typeerror(self):
        """3.14 上验证转发分支；CI 的 3.11 上验证丢弃分支——两边都得活。"""
        tmp = _conftest()._SafeTemporaryDirectory()
        try:
            assert Path(tmp.name).is_dir()
        finally:
            tmp.cleanup()

    def test_construct_with_delete_kwarg(self):
        """调用方传 delete（filelock 新版探测会传）时两个解释器都不得炸。"""
        conf = _conftest()
        for kw in ({}, {"delete": True}):
            tmp = conf._SafeTemporaryDirectory(**kw)
            try:
                assert Path(tmp.name).is_dir()
            finally:
                tmp.cleanup()
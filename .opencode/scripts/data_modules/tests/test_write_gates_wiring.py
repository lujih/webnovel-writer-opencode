#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""write_gates 的**接线**测试。

`data_modules/write_gates/` 有 400 多行、3 个阶段门禁、一份 297 行的设计文档
（`docs/specs/plugin-runtime-hardening-spec.md` §5.5）明确写了接入方式：

    # build_commit() 开头
    precommit = run_write_gate("precommit", self.project_root, chapter)
    if not precommit["ok"]:
        raise ValueError(...)

但**生产代码里一次都没调用过** `run_write_gate` —— 全仓 grep 只命中
`test_write_gates.py` 与那份 spec 本身。也就是说这三个门禁是**死代码**：
测试全绿、文档齐全，而真实写作流程从不经过它们，畸形产物照样进 SSOT。

本文件钉的是「接线」而不是「门禁逻辑」——逻辑已由 `test_write_gates.py`
覆盖。已有的逻辑测试无法发现死代码，因为死代码不会自己报错。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
DM = SCRIPTS / "data_modules"
REPO_ROOT = SCRIPTS.parents[1]


def _prod_py_files():
    """所有非测试、非 write_gates 自身的生产 Python 文件。"""
    for p in SCRIPTS.rglob("*.py"):
        rel = p.relative_to(SCRIPTS).as_posix()
        if "/tests/" in rel or rel.startswith("tests/"):
            continue
        if rel.startswith("data_modules/write_gates/"):
            continue
        if "__pycache__" in rel:
            continue
        yield p


def _imports_write_gate(path: Path) -> bool:
    """该文件是否（直接或间接）引用 write_gates / run_write_gate。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return "run_write_gate" in text or "write_gates" in text


class TestGatesAreWiredIntoProductionCode:
    """门禁必须真的被调用，否则它们只是装饰。"""

    def test_some_production_module_calls_run_write_gate(self):
        callers = [p for p in _prod_py_files() if _imports_write_gate(p)]
        assert callers, (
            "没有任何生产代码调用 write_gates —— 三个门禁（prewrite/precommit/"
            "postcommit）是死代码。设计文档 "
            "docs/specs/plugin-runtime-hardening-spec.md §5.5 明确要求在 "
            "chapter_commit_service.build_commit()/apply_projections() 中接入。"
        )

    def test_chapter_commit_service_consults_precommit_gate(self):
        """提交链路是门禁最该守的地方：畸形产物不能进 SSOT。"""
        text = (DM / "chapter_commit_service.py").read_text(encoding="utf-8")
        assert "run_write_gate" in text or "write_gates" in text, (
            "chapter_commit_service.py 没有引用 write_gates —— "
            "precommit gate 守不住提交链路"
        )

    def test_existing_gates_are_not_orphaned(self):
        """退一步：即使接入点换了地方，也必须至少有一个非测试调用方。

        与上一条的区别：这条不限定文件，防止有人「改个名字绕过测试」
        却仍然没接上。
        """
        callers = [p.relative_to(REPO_ROOT).as_posix()
                   for p in _prod_py_files() if "run_write_gate(" in p.read_text(
                       encoding="utf-8", errors="replace")]
        assert callers, "run_write_gate 零调用方（测试之外）"


class TestGatesFailClosed:
    """门禁自身出错时必须 fail-closed。

    现有实现用 `except Exception: pass` 包住每一项检查——检查器抛异常时
    该项检查被静默跳过，门禁放行。门禁与「读取状态」的区别正在于此：
    读取失败可以是「没有数据」，门禁失败只能是「无法确认安全」，
    后者必须阻断。这与 review_pipeline 里已确立的 fail-closed 原则一致
    （「无法判断有没有问题」≠「没有问题」）。
    """

    GATE_FILES = ("prewrite.py", "precommit.py", "postcommit.py")

    @pytest.mark.parametrize("name", GATE_FILES)
    def test_gate_does_not_silently_swallow_checker_errors(self, name):
        path = DM / "write_gates" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # 「窄化的 pass」是允许的：`except FileNotFoundError: pass` 表达
        # 「这项资源本来就可能不存在」（如 prewrite 阶段章节还没写），
        # 那是正常的控制流。危险的是 `except Exception: pass` —— 它把
        # 检查器自身的崩溃（ImportError/TypeError/KeyError…）也归为「没问题」。
        # 收紧到这个区分，才能既钉住 fail-closed，又不冤枉合法的窄异常。
        silent = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            if not (len(body) == 1 and isinstance(body[0], ast.Pass)):
                continue
            caught = node.type
            # except: / except BaseException / except Exception -> 过宽
            if caught is None:
                silent.append((node.lineno, "裸 except:"))
            elif isinstance(caught, ast.Name) and caught.id in ("Exception", "BaseException"):
                silent.append((node.lineno, f"except {caught.id}"))
            elif isinstance(caught, ast.Tuple):
                names = {e.id for e in caught.elts if isinstance(e, ast.Name)}
                broad = names & {"Exception", "BaseException"}
                if broad:
                    silent.append((node.lineno, f"except ({', '.join(sorted(broad))}, ...)"))

        assert not silent, (
            f"{name} 有 {len(silent)} 处过宽的静默吞异常：{silent}——"
            f"检查器抛异常时该项检查被静默跳过，门禁放行。"
            f"门禁必须 fail-closed：把异常转成 issue 上报，而不是 pass。"
            f"（窄异常如 `except FileNotFoundError: pass` 不算，那是正常控制流。）"
        )


class TestSpecChecklistMatchesReality:
    """设计文档的验收清单必须与实际状态一致。

    `plugin-runtime-hardening-spec.md` 有 41 个未勾选项，但 gating 模块已经
    合入很久（mtime 2026-06）。未勾的清单若对应「已实现」就是文档腐烂，
    若对应「未实现」就该在文首写明——不能两者都不是。
    """

    SPEC = REPO_ROOT / "docs" / "specs" / "plugin-runtime-hardening-spec.md"

    def test_spec_declares_its_status(self):
        text = self.SPEC.read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:30])
        assert any(k in head for k in ("已完成", "未完成", "部分完成", "状态")), (
            "spec 开头没有声明实现状态。41 个未勾选项读起来像待办，"
            "实际 write_gates 早已合入——读者无法判断该文档描述的是计划还是现状。"
        )

"""项目根解析必须以 CWD 为先（第4轮 P0：写进错误的书）。

危害：人站在书 A 的目录里执行命令，命令却作用到书 B——而写路径命令
（chapter-commit / update-state / backup）会**真的改写** B 的 state.json。
这是静默的数据损坏，不是报错。

三条错误来源（均已用真实书项目复现或以沙箱固化）：

1. `_resolve_root` 先从**脚本自身所在工作区**解析，把「脚本 checkout 目录」
   排在了人的 CWD 之前。
2. `resolve_project_root` 把**指针文件 / 全局注册表**排在 CWD 向上搜索
   **之前**：人明明站在书里，却输给了一个祖先目录里的陈旧指针。
3. `_candidate_roots` 会扫描 CWD 的所有子目录并**取第一个**命中项。
   多书工作区里这是字典序，不是用户意图。
"""
import json
from pathlib import Path

import pytest

from project_locator import (
    _global_registry_path,
    resolve_project_root,
    write_current_project_pointer,
)


def _make_book(root: Path, title: str) -> Path:
    book = root / title
    (book / ".webnovel").mkdir(parents=True)
    (book / ".webnovel" / "state.json").write_text(
        json.dumps({"project_info": {"title": title}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (book / "正文").mkdir()
    return book


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """两本书 + 一个工作区指针 + 独立的用户级 home（不污染真实注册表）。

    `.git/` 是**必需的隔离手段**：pytest 的 tmp 目录建在仓库内的 `.tmp/` 下，
    向上搜索会一路走到仓库根，命中真实的
    `.opencode/.webnovel-current-project` 指针并解析出一个真实书项目，
    使断言随开发机状态漂移（曾以此表现为「单跑绿、混跑红」）。
    """
    ws = tmp_path / "工作区"
    ws.mkdir()
    (ws / ".opencode").mkdir()
    (ws / ".git").mkdir()

    book_a = _make_book(ws, "书A")
    book_b = _make_book(ws, "书B")

    home = tmp_path / "home"
    (home / ".opencode").mkdir(parents=True)
    monkeypatch.setenv("WEBNOVEL_OPENCODE_HOME", str(home))
    for var in ("OPENCODE_PROJECT_DIR", "CLAUDE_PROJECT_DIR",
                "WEBNOVEL_PROJECT_ROOT", "OPENCODE_HOME", "CLAUDE_HOME"):
        monkeypatch.delenv(var, raising=False)

    return {"ws": ws, "a": book_a, "b": book_b, "home": home}


def _point_at(ws: Path, book: Path) -> None:
    (ws / ".opencode" / ".webnovel-current-project").write_text(str(book), encoding="utf-8")


class TestCwdBeatsPointer:
    def test_standing_in_book_b_pointer_to_a_yields_b(self, workspace, monkeypatch):
        """核心不变式：人站在哪本书，就操作哪本书。"""
        _point_at(workspace["ws"], workspace["a"])
        monkeypatch.chdir(workspace["b"])

        assert resolve_project_root().resolve() == workspace["b"].resolve(), (
            "陈旧指针赢了 CWD——命令会写进错误的书"
        )

    def test_pointer_still_works_for_workspace_root(self, workspace, monkeypatch):
        """指针的正当用途不能被破坏：站在多书工作区根目录时仍应解析。"""
        _point_at(workspace["ws"], workspace["a"])
        monkeypatch.chdir(workspace["ws"])

        assert resolve_project_root().resolve() == workspace["a"].resolve()

    def test_nested_dir_inside_book_resolves_the_book(self, workspace, monkeypatch):
        """站在书里的子目录（正文/）也应解析到该书。"""
        nested = workspace["b"] / "正文"
        nested.mkdir(exist_ok=True)
        _point_at(workspace["ws"], workspace["a"])
        monkeypatch.chdir(nested)

        assert resolve_project_root().resolve() == workspace["b"].resolve()


class TestAmbiguousWorkspace:
    def test_multiple_books_under_cwd_is_not_resolved_by_dict_order(self, workspace, monkeypatch):
        """多本书时不能靠字典序猜；没有指针就应当明确失败。"""
        _make_book(workspace["ws"], "书C")
        monkeypatch.chdir(workspace["ws"])

        with pytest.raises(FileNotFoundError):
            resolve_project_root()

    def test_single_book_under_cwd_still_resolves(self, tmp_path, monkeypatch):
        """只有一本书时仍可自动解析（不含歧义）。"""
        ws = tmp_path / "solo"
        ws.mkdir()
        book = _make_book(ws, "唯一的书")
        monkeypatch.chdir(ws)
        for var in ("OPENCODE_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "WEBNOVEL_PROJECT_ROOT"):
            monkeypatch.delenv(var, raising=False)

        assert resolve_project_root().resolve() == book.resolve()


class TestSearchStaysInsideGitRoot:
    def test_does_not_escape_above_git_root(self, workspace, monkeypatch):
        """git root 自身就是 CWD 时，不得继续往上找到无关的祖先项目。"""
        ws = workspace["ws"]
        outer = _make_book(workspace["ws"].parent, "外面的书")
        monkeypatch.chdir(ws)

        with pytest.raises(FileNotFoundError):
            resolve_project_root()
        assert outer.exists()  # 上面的书确实存在，只是不该被选中


class TestEntryPointPrefersCwd:
    def test_resolve_root_uses_cwd_not_script_workspace(self, workspace, monkeypatch):
        """`webnovel.py` 入口不得让脚本 checkout 目录压过人的 CWD。"""
        from data_modules import webnovel as entry

        # 模拟「脚本装在别的工作区里」，该工作区指针指向书A
        other_ws = workspace["ws"]
        _point_at(other_ws, workspace["a"])
        monkeypatch.setattr(entry, "_scripts_dir", lambda: other_ws / ".opencode" / "scripts")
        monkeypatch.chdir(workspace["b"])

        assert entry._resolve_root(None).resolve() == workspace["b"].resolve()

    def test_explicit_project_root_still_wins(self, workspace, monkeypatch):
        from data_modules import webnovel as entry

        _point_at(workspace["ws"], workspace["a"])
        monkeypatch.setattr(entry, "_scripts_dir", lambda: workspace["ws"] / ".opencode" / "scripts")
        monkeypatch.chdir(workspace["b"])

        assert entry._resolve_root(str(workspace["a"])).resolve() == workspace["a"].resolve()

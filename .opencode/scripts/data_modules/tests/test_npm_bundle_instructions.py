"""npm 离线包必须携带项目说明文件（AGENTS.md / CLAUDE.md）。

背景（2026-10-09 实测，对照 OpenCode v2 迁移文档）：

OpenCode 2.x **只发现 `AGENTS.md`**（不再回退 CLAUDE.md），且发现的是环境里的
AGENTS.md 文件——`.opencode/` 里没有它。缺文件时是**静默失败**：模型照常调用
skill，但读不到任何项目规则。

追查发现三重缺口（均已修）：
1. `build-bundle.js` 只打包 `.opencode/`，仓库根的两份指令文件从未进离线包
   （已发布的 2.9.2-12 实测包内无此二文件）；
2. 网络安装路径的 PREFIX 把解压限定在 `.opencode/` 内，源码 tarball 里的根
   指令文件被前缀过滤跳过；
3. `init.js` / `update.js` 都不生成、不校验 AGENTS.md 的存在。

回归测试覆盖：构建产物含二文件且内容与仓库根逐字节一致、构建在缺失时**大声
失败**、`extractRootDocs` 的白名单过滤精确、init/update 在删 tmp 之前调用它、
uninstall 只删未被用户改过的镜像。
"""
import json
import shutil
import subprocess
import tarfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
NPM_DIR = REPO_ROOT / ".npm-package"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 不在 PATH")

EXTRACT_JS = (NPM_DIR / "src" / "core" / "extract.js").resolve()
BUNDLE = NPM_DIR / "offline" / "opencode-bundle.tar.gz"


def _run(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(
            f"命令失败 rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def _node_e(script, check=True):
    """以 ESM 求值一段脚本（顶层 await 可用）。"""
    return _run([NODE, "--input-type=module", "-e", script], check=check)


@needs_node
class TestBundleContainsInstructionFiles:
    def test_build_and_inspect_tarball(self, tmp_path):
        """真实跑构建，用 tarfile 检查条目与内容。"""
        _run([NODE, "scripts/build-bundle.js"], cwd=NPM_DIR)
        assert BUNDLE.is_file(), "构建未产出离线包"

        with tarfile.open(BUNDLE, "r:gz") as tf:
            names = tf.getnames()
            assert "AGENTS.md" in names, f"离线包根没有 AGENTS.md（条目数 {len(names)}）"
            assert "CLAUDE.md" in names, "离线包根没有 CLAUDE.md"
            # tar 根 = 工作区根：与 .opencode/ 平级，AGENTS.md 发现路径才可见
            assert not any(n.startswith(".opencode/AGENTS") for n in names), \
                "AGENTS.md 放进 .opencode/ 里了——V2 的发现路径看不到它"
            assert ".opencode/opencode.json" in names
            # 内容必须与仓库根逐字节一致（mirror 政策）
            assert tf.extractfile("AGENTS.md").read() == \
                (REPO_ROOT / "AGENTS.md").read_bytes()
            assert tf.extractfile("CLAUDE.md").read() == \
                (REPO_ROOT / "CLAUDE.md").read_bytes()

    def test_verify_bundle_checks_docs(self):
        """verify-bundle（prepublishOnly 会跑）必须校验这两个文件。"""
        text = (NPM_DIR / "scripts" / "verify-bundle.js").read_text(encoding="utf-8")
        assert "'AGENTS.md'" in text and "'CLAUDE.md'" in text


@needs_node
class TestBuildFailsLoudlyWhenDocsMissing:
    def test_missing_agents_md_exits_nonzero(self, tmp_path):
        """缺 AGENTS.md 时必须让发布链失败，而不是静默出一个坏包。"""
        # 造一个最小仓库根：只有 LICENSE + 空 .opencode/，**没有** AGENTS.md
        repo = tmp_path / "repo"
        (repo / ".npm-package" / "scripts").mkdir(parents=True)
        (repo / ".opencode").mkdir()
        shutil.copy(NPM_DIR / "scripts" / "build-bundle.js",
                    repo / ".npm-package" / "scripts" / "build-bundle.js")
        shutil.copy(REPO_ROOT / "LICENSE", repo / "LICENSE")

        r = _run([NODE, str(repo / ".npm-package" / "scripts" / "build-bundle.js")],
                 check=False)
        assert r.returncode != 0, "缺 AGENTS.md 却构建成功——坏包会被发布出去"
        assert "AGENTS.md 不存在" in (r.stdout + r.stderr)


def _make_source_tarball(path: Path, files: dict) -> Path:
    """合成 GitHub 源码 tarball：`webnovel-writer-opencode-master/...` 布局。"""
    base = "webnovel-writer-opencode-master"
    with tarfile.open(path, "w:gz") as tf:
        for rel, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{base}/{rel}")
            info.size = len(data)
            import io
            tf.addfile(info, io.BytesIO(data))
    return path


@needs_node
class TestExtractRootDocs:
    BASE = "webnovel-writer-opencode-master"

    def _call(self, tarball: Path, dest: Path) -> dict:
        script = textwrap.dedent(f"""
            import {{ extractRootDocs }} from {json.dumps(EXTRACT_JS.as_uri())};
            const r = await extractRootDocs(
              {json.dumps(str(tarball))}, {json.dumps(str(dest))}, {json.dumps(self.BASE)});
            console.log(JSON.stringify(r));
        """)
        r = _node_e(script)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_extracts_both_and_skips_everything_else(self, tmp_path):
        tb = _make_source_tarball(tmp_path / "src.tar.gz", {
            "AGENTS.md": "agents-body",
            "CLAUDE.md": "claude-body",
            ".opencode/opencode.json": "{}",
            "README.md": "should not land",
        })
        dest = tmp_path / "out"
        result = self._call(tb, dest)
        assert sorted(result["extracted"]) == ["AGENTS.md", "CLAUDE.md"]
        assert result["missing"] == []
        # 白名单过滤：只落盘这两个，.opencode/ 与 README 不得出现
        landed = sorted(p.name for p in dest.iterdir())
        assert landed == ["AGENTS.md", "CLAUDE.md"], f"越权落盘: {landed}"
        assert (dest / "AGENTS.md").read_text(encoding="utf-8") == "agents-body"

    def test_reports_missing_doc(self, tmp_path):
        tb = _make_source_tarball(tmp_path / "src.tar.gz", {
            "AGENTS.md": "agents-body",
        })
        dest = tmp_path / "out"
        result = self._call(tb, dest)
        assert result["extracted"] == ["AGENTS.md"]
        assert result["missing"] == ["CLAUDE.md"]


class TestCallersExtractDocsBeforeDeletingTmp:
    """init/update 走网络路径时，必须在删除 tmp tarball **之前**抽根说明文件。"""

    @pytest.mark.parametrize("cmd", ["init.js", "update.js"])
    def test_called_before_unlink(self, cmd):
        text = (NPM_DIR / "src" / "commands" / cmd).read_text(encoding="utf-8")
        assert "extractRootDocs(" in text, f"{cmd} 没有抽取根说明文件"
        call_at = text.index("extractRootDocs(")
        # 存在一次 unlink(tmp) 发生在调用之后（init 里有两条路径，取最后一次无关，
        # 关键是第一次 unlink 不得早于调用）
        unlink_at = text.index("unlinkSync(tmp)")
        assert call_at < unlink_at, (
            f"{cmd}: extractRootDocs 在 unlinkSync(tmp) 之后——tarball 已删，说明文件抽不到")

    @pytest.mark.parametrize("cmd", ["init.js", "update.js"])
    def test_imports_helper(self, cmd):
        text = (NPM_DIR / "src" / "commands" / cmd).read_text(encoding="utf-8")
        assert "extractRootDocs } from '../core/extract.js'" in text or \
            "extractRootDocs, " in text or \
            "{ extractTarGz, extractRootDocs }" in text


class TestUninstallOnlyRemovesUntouchedMirror:
    def test_preserves_user_edited_instructions(self):
        """卸载不得删掉用户自定义过的 AGENTS.md（只删逐字节镜像）。"""
        text = (NPM_DIR / "src" / "commands" / "uninstall.js") \
            .read_text(encoding="utf-8")
        assert "readFileSync(agents).equals(readFileSync(claude))" in text, (
            "卸载应以 AGENTS==CLAUDE 逐字节相等作为可删除条件")


class TestBuildBundleSourceContract:
    def test_build_includes_root_docs_logic(self):
        text = (NPM_DIR / "scripts" / "build-bundle.js").read_text(encoding="utf-8")
        for doc in ("AGENTS.md", "CLAUDE.md"):
            assert doc in text, f"build-bundle.js 未处理 {doc}"
        assert "缺失时拒绝出包" in text or "缺失时拒绝" in text or "拒绝出包" in text
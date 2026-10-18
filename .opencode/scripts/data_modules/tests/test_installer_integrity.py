"""安装器的完整性校验（P1-3）。

背景：`init.js` / `update.js` 此前的唯一下载校验是 **content-length**。镜像站
（`MIRRORS` 里的第三方 ghproxy 是被**自动追加**到每次下载的，不是用户显式选择）
返回一个长度正确的恶意 tarball 就能一路过关；而 `extractRootDocs` 会把其中的
AGENTS.md / CLAUDE.md 写到 **工作区根**——OpenCode 会自动加载它们。这等于安装器
级别的提示词注入，且没有任何完整性门禁。

这一轮加的是两级策略（有意不对称）：

- **离线包**（随 npm 包发布、内容固定）→ **强制**校验 `.sha256` sidecar，不匹配
  拒绝解压。这是唯一能把"发布者指纹"落到用户机器上的路径。
- **网络下载**（`refs/heads/master` 可变）→ 没有可预置的哈希，只计算并打印供
  审计；用户可用 `--sha256` 自行钉死。

不给网络路径做强制校验的原因：分支 tarball 内容随时变，写死的哈希第二天失效；
GitHub 不提供 tarball 官方校验和；钉死到不可变 tag 会改变 tarball 顶层目录名
（PREFIX / REPO_BASE），属于高风险改动。

本文件覆盖：integrity.js 的哈希语义、build-bundle 产出 sidecar 且与内容一致、
init/update 在**解压之前**校验且不匹配时 fail-closed、网络路径只审计不阻断、
`--sha256` 参数解析。
"""
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
NPM_DIR = REPO_ROOT / ".npm-package"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 不在 PATH")

INTEGRITY_JS = (NPM_DIR / "src" / "core" / "integrity.js").resolve()
INIT_JS = (NPM_DIR / "src" / "commands" / "init.js").resolve()
UPDATE_JS = (NPM_DIR / "src" / "commands" / "update.js").resolve()
BUILD_JS = (NPM_DIR / "scripts" / "build-bundle.js").resolve()
BIN_JS = (NPM_DIR / "bin" / "webnovel-writer.js").resolve()
BUNDLE = NPM_DIR / "offline" / "opencode-bundle.tar.gz"
SIDECAR = Path(str(BUNDLE) + ".sha256")


def _text(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _run(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise AssertionError(
            f"命令失败 rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def _node_e(script, check=True):
    return _run([NODE, "--input-type=module", "-e", script], check=check)


@needs_node
class TestIntegrityModule:
    def test_sha256_matches_python_hashlib(self, tmp_path):
        """node:crypto 与 hashlib 对同一份字节必须同值——否则 sidecar 毫无意义。"""
        sample = tmp_path / "sample.bin"
        payload = "凡尘之舞\n第二行\r\n" * 500
        sample.write_bytes(payload.encode("utf-8"))
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        script = (
            f"import {{ sha256File }} from '{INTEGRITY_JS.as_uri()}';"
            f"process.stdout.write(await sha256File({str(sample)!r}));"
        )
        r = _node_e(script)
        assert r.stdout.strip() == expected

    def test_verify_accepts_matching_hash_case_insensitively(self, tmp_path):
        sample = tmp_path / "sample.bin"
        sample.write_bytes(b"abc")
        want = hashlib.sha256(b"abc").hexdigest()
        script = (
            f"import {{ verifyFileSha256 }} from '{INTEGRITY_JS.as_uri()}';"
            f"const r = await verifyFileSha256({str(sample)!r}, {want.upper()!r});"
            "process.stdout.write(JSON.stringify(r));"
        )
        r = _node_e(script)
        assert '"ok":true' in r.stdout.replace(" ", "")

    def test_verify_rejects_truncated_and_extra_bytes(self, tmp_path):
        sample = tmp_path / "sample.bin"
        sample.write_bytes(b"abc")
        good = hashlib.sha256(b"abc").hexdigest()
        for payload in (b"ab", b"abcd", b""):
            sample.write_bytes(payload)
            script = (
                f"import {{ verifyFileSha256 }} from '{INTEGRITY_JS.as_uri()}';"
                f"const r = await verifyFileSha256({str(sample)!r}, {good!r});"
                "process.stdout.write(JSON.stringify(r));"
            )
            r = _node_e(script)
            assert '"ok":false' in r.stdout.replace(" ", ""), f"{payload!r} 竟然通过了"

    def test_verify_rejects_malformed_expected_hash(self, tmp_path):
        """空字符串 / 短哈希 / 非 hex 一律不通过——绝不能 fail-open。"""
        sample = tmp_path / "sample.bin"
        sample.write_bytes(b"abc")
        for bad in ("", "abc", good if (good := "z" * 64) else ""):
            script = (
                f"import {{ verifyFileSha256 }} from '{INTEGRITY_JS.as_uri()}';"
                f"const r = await verifyFileSha256({str(sample)!r}, {bad!r});"
                "process.stdout.write(JSON.stringify(r));"
            )
            r = _node_e(script)
            assert '"ok":false' in r.stdout.replace(" ", ""), f"{bad!r} 竟然通过了"

    def test_sidecar_roundtrip_and_partial_parse(self, tmp_path):
        """sidecar 是 `sha256sum` 格式；读回时只取第一段，忽略文件名栏。"""
        sample = tmp_path / "离线包.tar.gz"
        sample.write_bytes(b"payload")
        want = hashlib.sha256(b"payload").hexdigest()
        script = (
            f"import {{ writeSha256Sidecar, readSha256Sidecar }} from '{INTEGRITY_JS.as_uri()}';"
            f"await writeSha256Sidecar({str(sample)!r});"
            f"const hex = readSha256Sidecar({str(sample)!r} + '.sha256');"
            "process.stdout.write(JSON.stringify({hex}));"
        )
        r = _node_e(script)
        assert want in r.stdout
        on_disk = Path(str(sample) + ".sha256").read_text(encoding="utf-8").strip()
        assert on_disk.startswith(want), "sidecar 首段必须是纯 hex（兼容 sha256sum -c）"

    def test_sidecar_reader_returns_null_when_absent_or_garbage(self, tmp_path):
        sample = tmp_path / "x.bin"
        sample.write_bytes(b"p")
        script = (
            f"import {{ readSha256Sidecar }} from '{INTEGRITY_JS.as_uri()}';"
            f"const a = readSha256Sidecar({str(sample)!r} + '.sha256');"
            f"import('node:fs').then(fs => fs.writeFileSync({str(sample)!r} + '.sha256', 'garbage'));"
            "process.stdout.write(JSON.stringify({a}));"
        )
        r = _node_e(script)
        assert '"a":null' in r.stdout


@needs_node
class TestBuildEmitsSidecar:
    def test_build_writes_sidecar_matching_the_bundle(self):
        """真实跑构建：sidecar 必须存在、且与产物字节一致。"""
        _run([NODE, "scripts/build-bundle.js"], cwd=NPM_DIR)
        assert BUNDLE.is_file(), "构建未产出离线包"
        assert SIDECAR.is_file(), (
            "构建没有产出 .sha256 sidecar —— 安装器的强制校验会退化成"
            "「缺 sidecar 就跳过」，等于没做")
        actual = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
        recorded = SIDECAR.read_text(encoding="utf-8").split()[0]
        assert recorded == actual, "sidecar 记录的哈希与产物不符"

    def test_build_script_calls_sidecar_writer(self):
        src = _text(BUILD_JS)
        assert "writeSha256Sidecar" in src
        assert "from '../src/core/integrity.js'" in src


@needs_node
class TestSidecarShipsInTheNpmTarball:
    """sidecar 必须真的进 npm 包，否则安装器的强制校验形同虚设。

    这是最容易静默失效的一环：`package.json` 的 `files` 是**白名单**，漏一项
    就直接不进 tarball；而安装器对"缺 sidecar"是降级为警告继续（兼容旧包），
    于是整条完整性链会安静地退化成零。构建时不会报错，CI 也不会红。
    """

    def test_package_files_whitelists_the_sidecar(self):
        import json
        files = json.loads((NPM_DIR / "package.json").read_text(encoding="utf-8"))["files"]
        assert "offline/opencode-bundle.tar.gz" in files, "离线包本体不在白名单里"
        assert "offline/opencode-bundle.tar.gz.sha256" in files, (
            "sidecar 不在 package.json 的 files 白名单里——npm 不会打包它，"
            "安装器会走\"缺校验文件则跳过\"的分支，强制校验等于没做")

    def test_repo_ignores_the_sidecar_like_the_bundle(self):
        """sidecar 是构建产物，两份都应被 git 忽略，避免提交过期哈希。"""
        for rel in ("offline/opencode-bundle.tar.gz",
                    "offline/opencode-bundle.tar.gz.sha256"):
            r = _run(["git", "check-ignore", "-q", "--no-index",
                      str(NPM_DIR / rel)], cwd=REPO_ROOT, check=False)
            assert r.returncode == 0, f"{rel} 没有被忽略——构建产物会污染提交"


@needs_node
class TestInstallerVerifiesBeforeExtract:
    def test_init_verifies_offline_bundle_before_extracting(self):
        src = _text(INIT_JS)
        idx_verify = src.index("verifyFileSha256(offlinePath")
        idx_extract = src.index("extractTarGz(offlinePath")
        assert idx_verify < idx_extract, (
            "校验必须在解压之前——解压会直接覆盖用户已有的 .opencode/，无法回滚")

    def test_init_refuses_to_extract_on_mismatch(self):
        src = _text(INIT_JS)
        assert "离线包完整性校验失败，已拒绝解压" in src

    def test_init_warns_instead_of_silently_passing_when_sidecar_absent(self):
        """旧版本包没有 sidecar：必须出声，不能静默当通过。"""
        src = _text(INIT_JS)
        assert "缺少 .sha256 校验文件" in src

    def test_update_verifies_offline_bundle_before_extracting(self):
        src = _text(UPDATE_JS)
        idx_verify = src.index("verifyFileSha256(offline")
        idx_extract = src.index("extractTarGz(offline")
        assert idx_verify < idx_extract

    def test_update_refuses_to_extract_on_mismatch(self):
        src = _text(UPDATE_JS)
        assert "离线包完整性校验失败，已拒绝更新" in src

    def test_both_installers_use_the_shared_module(self):
        for path in (INIT_JS, UPDATE_JS):
            src = _text(path)
            assert "from '../core/integrity.js'" in src, f"{path.name} 没复用共享模块"


@needs_node
class TestNetworkPathIsAuditOnly:
    def test_network_download_prints_sha256(self):
        """分支 tarball 无预置哈希，但必须打印出来供用户审计。"""
        for path in (INIT_JS, UPDATE_JS):
            src = _text(path)
            assert "下载包 sha256:" in src, f"{path.name} 没有输出下载包哈希"

    def test_network_path_honours_explicit_pin(self):
        for path in (INIT_JS, UPDATE_JS):
            src = _text(path)
            assert "looksLikeSha256" in src
            assert "不匹配，已中止" in src

    def test_no_checksum_requirement_for_branch_tarball(self):
        """确认没有把可变的 master tarball 写死成某个哈希（会立刻失效）。"""
        for path in (INIT_JS, UPDATE_JS):
            src = _text(path)
            pinned = re.findall(r"['\"]([0-9a-f]{64})['\"]", src)
            assert not pinned, f"{path.name} 写死了哈希 {pinned}——分支包内容会变"


@needs_node
class TestCliFlag:
    def test_sha256_flag_is_parsed(self):
        r = _run([NODE, str(BIN_JS), "--help"])
        assert "--sha256" in r.stdout

    def test_sha256_flag_requires_a_value(self):
        r = _run([NODE, str(BIN_JS), "init", "--sha256"], check=False)
        assert r.returncode != 0
        assert "需要 64 位十六进制哈希" in (r.stdout + r.stderr)

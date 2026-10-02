"""npm 包的许可证合规。

这是对外可见的仓库（211 stars，npm 上已发布 17 个版本），许可证声明错了
会直接误导使用者，也会让 GPL §4 的分发义务无法验证。

三件事必须同时成立：

1. `package.json` 的 `license` 与仓库正本一致——此前是 **MIT**，而 LICENSE
   是 GPL-3.0、README §6.1 也写明"继承自原项目"。MIT 是宽松许可，与 GPL 的
   copyleft 义务不兼容；声明错等于把 GPL 代码按 MIT 再分发。
2. `files` 白名单含 LICENSE——`files` 以 `.npm-package/` 为基准，正本在仓库
   根，不复制过去就等于没随包分发。
3. 构建时复制的那份与正本逐字节相同（防止两份正本各自漂移）。
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PKG_DIR = REPO_ROOT / ".npm-package"
PKG_JSON = PKG_DIR / "package.json"
ROOT_LICENSE = REPO_ROOT / "LICENSE"
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def pkg():
    return json.loads(PKG_JSON.read_text(encoding="utf-8"))


def _root_license_spdx() -> str:
    """从 LICENSE 正文判定：GPL-3.0。"""
    text = ROOT_LICENSE.read_text(encoding="utf-8", errors="replace")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text
    return "GPL-3.0"


class TestLicenseDeclaration:
    def test_package_license_is_not_mit(self, pkg):
        """MIT 与 GPL 的 copyleft 义务不兼容，不能声明成 MIT。"""
        declared = pkg.get("license", "")
        assert declared != "MIT", (
            "npm 包声明 MIT，但仓库正本是 GPL-3.0（README §6.1 明确继承自 "
            "lingfengQAQ/webnovel-writer）。MIT 允许闭源再分发，与 GPL 冲突。"
        )

    def test_package_license_matches_root(self, pkg):
        expected = _root_license_spdx()
        declared = pkg.get("license", "")
        assert expected in declared, (
            f"package.json 声明 {declared!r}，与仓库正本 {expected} 不符"
        )

    def test_readme_agrees_with_license_file(self):
        readme = README.read_text(encoding="utf-8")
        assert "GPL v3" in readme or "GPL-3.0" in readme, (
            "README §6 与 LICENSE 文件对许可证的说法不一致"
        )


class TestLicenseShipsWithPackage:
    def test_files_whitelist_includes_license(self, pkg):
        files = pkg.get("files", [])
        assert "LICENSE" in files, (
            "files 白名单缺 LICENSE——`files` 以 .npm-package/ 为基准，"
            "正本在仓库根，不复制过去就等于没随包分发（GPL §4(a)）"
        )

    def test_build_script_copies_license(self):
        """构建脚本负责把正本复制进包目录，避免两份正本漂移。"""
        src = (PKG_DIR / "scripts" / "build-bundle.js").read_text(encoding="utf-8")
        assert "copyFileSync" in src and "LICENSE" in src, (
            "build-bundle.js 不再复制 LICENSE——"
            "要么包内缺许可证，要么得把第二份 LICENSE 提交进 git 而它会漂移"
        )

    def test_copied_license_identical_to_root(self):
        copied = PKG_DIR / "LICENSE"
        if not copied.is_file():
            pytest.skip("尚未构建（运行 node scripts/build-bundle.js 后校验）")
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
        assert digest(copied) == digest(ROOT_LICENSE), (
            ".npm-package/LICENSE 与仓库根 LICENSE 不一致——重新构建"
        )


class TestNoStrayMitElsewhere:
    """其他 package.json 不应声明 MIT（会让打包方再次引入冲突）。"""

    def test_no_mit_declarations(self):
        offenders = []
        for path in REPO_ROOT.rglob("package.json"):
            if "node_modules" in path.parts or "外部参考" in path.parts:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if data.get("license") == "MIT":
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, f"这些 package.json 仍声明 MIT: {offenders}"
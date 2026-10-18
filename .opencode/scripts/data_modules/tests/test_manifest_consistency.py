"""manifest.json 的可复现性回归测试。

这份清单此前有三个独立的失效点，每个都让它在现实里毫无用处：

1. **哈希随检出平台变化**。旧实现直接哈希工作树原始字节，而 Windows 检出是
   CRLF、Linux CI 是 LF——同一 commit 算出的哈希不同。实测 485 个条目里
   428 个"不匹配"纯粹是行尾差异。
2. **文件集合来自工作树遍历**。于是开发机上任何被 gitignore 的产物都会混进
   清单与发布包：`.coverage`（二进制）、`.webnovel-current-project`
   （泄露构建机绝对路径）、一堆与本项目无关的本地 skill。
3. **没有可执行的漂移检查**。没有 `--verify` 时没人会去手 diff 500 行哈希，
   清单可以静默陈旧很久。

注意：本文件**不**断言仓库里的 manifest.json 是"最新"的。清单由 CI 的
`manifest.yml` 在每次推 master 时重新生成，而开发者改任何一个已跟踪文件的
第一行都会让哈希变化——把"新鲜度"钉进单元测试等于让正常工作流永远踩红。
这里只钉住上面三个根因：行尾不变性、集合正确性、`--verify` 真的能报错。
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / '.opencode' / 'scripts'
MANIFEST = REPO_ROOT / 'manifest.json'
NPM_DIR = REPO_ROOT / '.npm-package'

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='需要 git')


def _load_gen_manifest():
    """按路径加载 gen_manifest.py（它不是 data_modules 下的包成员）。"""
    path = SCRIPTS / 'gen_manifest.py'
    spec = importlib.util.spec_from_file_location('gen_manifest_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def gen_manifest():
    return _load_gen_manifest()


class TestHashingIsCheckoutIndependent:
    """根因 1：同一内容在 CRLF / LF 两种检出上必须算出同一串哈希。"""

    def test_crlf_and_lf_content_hash_identically(self, gen_manifest, tmp_path):
        lf = tmp_path / 'lf.txt'
        crlf = tmp_path / 'crlf.txt'
        lf.write_bytes('第一行\n第二行\n'.encode('utf-8'))
        crlf.write_bytes('第一行\r\n第二行\r\n'.encode('utf-8'))

        assert (gen_manifest.hash_file_normalized(lf)
                == gen_manifest.hash_file_normalized(crlf))

    def test_hash_matches_sha256sum_of_normalized_bytes(self, gen_manifest, tmp_path):
        """归一化后的哈希必须等于 `sha256sum` 对 LF 内容算出的值——
        否则外来工具无法复算这份清单。"""
        import hashlib

        f = tmp_path / 'sample.py'
        f.write_bytes(b'a\r\nb\r\nc\r\n')
        expected = hashlib.sha256(b'a\nb\nc\n').hexdigest()
        assert gen_manifest.hash_file_normalized(f) == expected

    def test_size_is_measured_after_normalization(self, gen_manifest, tmp_path):
        """size 与 sha256 必须描述同一份字节，否则清单内部自相矛盾。"""
        f = tmp_path / 'x.txt'
        f.write_bytes(b'a\r\nb\r\n')
        assert gen_manifest.normalized_size(f) == 4          # 'a\nb\n'
        assert gen_manifest.hash_file_normalized(f) == __import__('hashlib').sha256(b'a\nb\n').hexdigest()

    def test_repo_manifest_has_no_duplicate_hashes_for_identical_eol(self, tmp_path):
        """两根只差行尾的文件在同一清单里必须同哈希（端到端版上面的单测）。"""
        mod = _load_gen_manifest()
        root = tmp_path / 'proj'
        # 造一个「非 git 目录」，走到 walk_files 回退分支
        (root / '.opencode').mkdir(parents=True)
        (root / '.opencode' / 'a.md').write_bytes(b'x\r\ny\r\n')
        (root / '.opencode' / 'b.md').write_bytes(b'x\ny\n')

        manifest = mod.build_manifest(root / '.opencode', version='v0')
        hashes = {v['sha256'] for v in manifest['files'].values()}
        assert len(hashes) == 1, f'行尾不同的同内容文件哈希不一致: {hashes}'


class TestFileSetComesFromGitIndex:
    """根因 2：清单与发布包只能含**仓库里有的**文件。"""

    def test_manifest_has_no_local_artifacts(self):
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        names = set(data['files'])

        forbidden = {'.opencode/scripts/.coverage',
                     '.opencode/.webnovel-current-project'}
        assert not (names & forbidden), f'清单里混进本地产物: {names & forbidden}'
        assert not any('/.agents/' in n for n in names), \
            '清单里混进开发者本机的 skill 包（.agents/）'

    def test_every_manifest_entry_is_git_tracked(self):
        """清单条目必须都在 git 索引里——否则就是遍历工作树的残留。"""
        tracked = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'ls-files', '-z', '--', '.opencode'],
            capture_output=True, text=True, check=True, timeout=120,
        ).stdout.split('\0')
        tracked = {p for p in tracked if p}

        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        untracked = sorted(set(data['files']) - tracked)
        assert not untracked, f'清单含 {len(untracked)} 个未跟踪文件: {untracked[:5]}'

    def test_manifest_paths_use_opencode_prefix(self):
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        bad = [n for n in data['files'] if not n.startswith('.opencode/')]
        assert not bad, f'清单路径形状不对: {bad[:5]}'

    def test_git_fallback_walks_worktree_outside_a_repo(self, gen_manifest, tmp_path):
        """非 git 目录（测试里的假仓库）必须回退到遍历，不能返回空清单。"""
        root = tmp_path / 'not-a-repo'
        (root / '.opencode').mkdir(parents=True)
        (root / '.opencode' / 'only.py').write_text('x = 1\n', encoding='utf-8')

        assert gen_manifest.git_tracked_files(root / '.opencode') == []
        files = gen_manifest.iter_manifest_files(root / '.opencode')
        assert [f.name for f in files] == ['only.py']


class TestVerifyModeReportsDrift:
    """根因 3：`--verify` 必须在漂移时非零退出，且指出具体文件。"""

    def _fake_repo(self, tmp_path):
        root = tmp_path / 'proj'
        (root / '.opencode').mkdir(parents=True)
        (root / '.opencode' / 'a.py').write_text('a = 1\n', encoding='utf-8')
        (root / '.opencode' / 'b.py').write_text('b = 2\n', encoding='utf-8')
        mod = _load_gen_manifest()
        manifest = mod.build_manifest(root / '.opencode', version='v1')
        mpath = root / 'manifest.json'
        mpath.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return mod, root, mpath

    def test_verify_passes_on_fresh_manifest(self, tmp_path, capsys):
        mod, root, mpath = self._fake_repo(tmp_path)
        assert mod.verify(root / '.opencode', mpath) == 0
        assert '一致' in capsys.readouterr().out

    def test_verify_detects_tampered_file(self, tmp_path, capsys):
        mod, root, mpath = self._fake_repo(tmp_path)
        (root / '.opencode' / 'a.py').write_text('a = 999\n', encoding='utf-8')
        assert mod.verify(root / '.opencode', mpath) == 1
        assert 'a.py' in capsys.readouterr().err

    def test_verify_detects_added_file(self, tmp_path, capsys):
        mod, root, mpath = self._fake_repo(tmp_path)
        (root / '.opencode' / 'new.py').write_text('c = 3\n', encoding='utf-8')
        assert mod.verify(root / '.opencode', mpath) == 1
        assert 'new.py' in capsys.readouterr().err

    def test_verify_detects_deleted_file(self, tmp_path, capsys):
        mod, root, mpath = self._fake_repo(tmp_path)
        (root / '.opencode' / 'b.py').unlink()
        assert mod.verify(root / '.opencode', mpath) == 1
        assert 'b.py' in capsys.readouterr().err

    def test_verify_missing_manifest_is_nonzero(self, tmp_path):
        mod = _load_gen_manifest()
        assert mod.verify(tmp_path, tmp_path / 'nope.json') == 1

    def test_cli_exit_code_is_nonzero_on_drift(self, tmp_path):
        """走真实 CLI，确认 sys.exit 把 1 传出去（CI 靠退出码判定）。"""
        mod, root, mpath = self._fake_repo(tmp_path)
        (root / '.opencode' / 'a.py').write_text('changed\n', encoding='utf-8')
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / 'gen_manifest.py'),
             '--verify', '--opencode-dir', str(root / '.opencode'),
             '--manifest', str(mpath)],
            capture_output=True, text=True, timeout=120,
        )
        assert r.returncode == 1, r.stdout + r.stderr


class TestInstallerDoesNotReadAPathThatNeverExists:
    """死链回归：安装器曾读 `<workspace>/.opencode/manifest.json`，
    而清单实际在仓库根、且从不进发布包——那个分支永远空跑，
    version.json 的 opencode_version/opencode_tag 永远是空字符串。"""

    @pytest.mark.parametrize('rel', ['init.js', 'update.js'])
    def test_no_dead_manifest_read(self, rel):
        text = (NPM_DIR / 'src' / 'commands' / rel).read_text(encoding='utf-8')
        # 只要不再构造 manifest.json 路径就算修好；代码注释里提到是允许的
        assert "'manifest.json'" not in text, \
            f'{rel} 仍在读不存在的 manifest.json 路径'

    def test_bundle_does_not_ship_manifest_json(self):
        """清单留在仓库根：放进 .opencode/ 会让它哈希自己（先有鸡还是先有蛋）。"""
        tar = NPM_DIR / 'offline' / 'opencode-bundle.tar.gz'
        if not tar.is_file():
            pytest.skip('离线包未构建')
        import tarfile
        with tarfile.open(tar, 'r:gz') as tf:
            names = tf.getnames()
        assert '.opencode/manifest.json' not in names
        assert 'manifest.json' not in names

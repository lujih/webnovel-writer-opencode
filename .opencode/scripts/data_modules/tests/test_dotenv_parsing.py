#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`.env` 解析的行为测试。

`config._load_dotenv_file()` 是手写的极简解析器（约 15 行）。它把整行
`= ` 之后的内容原样当作值，于是 `.env` 里最常见的**带引号写法**会连带
引号一起进字典：

    EMBED_API_KEY="sk-abc123"
      → os.environ['EMBED_API_KEY'] == '"sk-abc123"'

而这个值会直接拼进 HTTP 头：

    api_client.py:72  headers["Authorization"] = f"Bearer {self.config.embed_api_key}"

结果就是 `Authorization: Bearer "sk-abc123"`，服务端 401。报错信息只会
说鉴权失败，不会提示"你的 .env 多了两个引号"——排查成本很高。

本项目自己生成的 `.env.example` 不带引号，但用户常常：
1. 从别人的教程/文档复制带引号的写法（dotenv 生态普遍支持引号）
2. 编辑器插件自动加引号（尤其是值含空格或 `#` 时）
3. 用 `KEY='value'` 单引号写法

这些写法在 python-dotenv / docker-compose / GitHub Actions 里都合法，
唯独在这里不生效。

本文件钉住：引号必须被剥掉、`#` 行内注释必须被识别（不剥会让
`KEY=value # 注释` 把注释也拼进值里）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
DM = SCRIPTS / "data_modules"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="module")
def loader():
    """取 `config._load_dotenv_file`。

    必须按包导入（`data_modules.config`）——config.py 里有
    `from .context_weights import ...` 这样的相对导入，按文件路径加载会
    ImportError。
    """
    from data_modules.config import _load_dotenv_file

    return _load_dotenv_file


@pytest.fixture
def clean_env(monkeypatch):
    """确保测试用的键不会串到真实环境。"""
    keys = [
        "WN_TEST_DOUBLE_QUOTED", "WN_TEST_SINGLE_QUOTED", "WN_TEST_BARE",
        "WN_TEST_INLINE_HASH", "WN_TEST_WITH_SPACE", "WN_TEST_EMPTY",
        "WN_TEST_NUMERIC",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in keys:
        os.environ.pop(k, None)


class TestQuotingIsStripped:
    """dotenv 生态的标准写法必须能用。"""

    def test_double_quoted_value_has_no_quotes(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text('WN_TEST_DOUBLE_QUOTED="sk-abc123"\n', encoding="utf-8")
        loader(env)

        value = os.environ["WN_TEST_DOUBLE_QUOTED"]
        assert value == "sk-abc123", (
            f"双引号未被剥离：{value!r}。"
            f"该值会拼进 `Authorization: Bearer {{key}}`，导致服务端 401。"
        )

    def test_single_quoted_value_has_no_quotes(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text("WN_TEST_SINGLE_QUOTED='sk-def456'\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_SINGLE_QUOTED"] == "sk-def456"

    def test_bare_value_unchanged(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text("WN_TEST_BARE=plainvalue\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_BARE"] == "plainvalue"

    def test_unbalanced_quote_left_alone(self, loader, tmp_path, clean_env):
        """只有一个引号时不要瞎剥——那多半是值本身的字符。"""
        env = tmp_path / ".env"
        env.write_text('WN_TEST_BARE="unclosed\n', encoding="utf-8")
        loader(env)

        # 不崩、且不把内容吃掉
        assert "unclosed" in os.environ["WN_TEST_BARE"]


class TestInlineComments:
    """`KEY=value # 说明` 是常见写法，注释不能拼进值。"""

    def test_inline_hash_comment_removed(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text("WN_TEST_INLINE_HASH=realvalue  # 这一行是说明\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_INLINE_HASH"] == "realvalue", (
            f"行内注释被拼进了值：{os.environ['WN_TEST_INLINE_HASH']!r}"
        )

    def test_hash_inside_quotes_is_kept(self, loader, tmp_path, clean_env):
        """引号里的 `#` 是值的一部分（如密码），不能当注释切掉。"""
        env = tmp_path / ".env"
        env.write_text('WN_TEST_WITH_SPACE="pass#word"\n', encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_WITH_SPACE"] == "pass#word"


class TestPreservedBehaviour:
    """原有正确行为不能被改坏。"""

    def test_real_env_var_not_overridden_by_default(self, loader, tmp_path, monkeypatch):
        monkeypatch.setenv("WN_TEST_BARE", "from_environment")
        env = tmp_path / ".env"
        env.write_text("WN_TEST_BARE=from_file\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_BARE"] == "from_environment", (
            "默认不应覆盖已有环境变量（显式 > .env 的优先级）"
        )

    def test_override_true_wins(self, loader, tmp_path, monkeypatch):
        monkeypatch.setenv("WN_TEST_BARE", "from_environment")
        env = tmp_path / ".env"
        env.write_text("WN_TEST_BARE=from_file\n", encoding="utf-8")
        loader(env, override=True)

        assert os.environ["WN_TEST_BARE"] == "from_file"

    def test_comment_and_blank_lines_ignored(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text(
            "# 整行注释\n\n   \nWN_TEST_BARE=value\n# 又一行注释\n", encoding="utf-8"
        )
        loader(env)

        assert os.environ["WN_TEST_BARE"] == "value"

    def test_empty_value_is_empty_string(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text("WN_TEST_EMPTY=\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_EMPTY"] == ""

    def test_missing_file_returns_false(self, loader, tmp_path):
        assert loader(tmp_path / "nope.env") is False

    def test_numeric_value_stays_string(self, loader, tmp_path, clean_env):
        env = tmp_path / ".env"
        env.write_text("WN_TEST_NUMERIC=42\n", encoding="utf-8")
        loader(env)

        assert os.environ["WN_TEST_NUMERIC"] == "42"
        assert isinstance(os.environ["WN_TEST_NUMERIC"], str)


class TestValueParserEdgeCases:
    """`_parse_dotenv_value` 的边界：既要剥注释，又不能误伤内容。"""

    @staticmethod
    @pytest.fixture
    def parse():
        from data_modules.config import _parse_dotenv_value
        return _parse_dotenv_value

    def test_url_fragment_preserved(self, parse):
        """URL 里的 `#fragment` 前面没有空白，不是注释。"""
        assert parse("https://api.example.com/v1#frag") == "https://api.example.com/v1#frag"

    def test_url_with_trailing_comment_trimmed(self, parse):
        assert parse("https://api.example.com/v1  # 备用地址") == "https://api.example.com/v1"

    def test_hash_inside_double_quotes_kept(self, parse):
        assert parse('"a # b"') == "a # b"

    def test_hash_inside_single_quotes_kept(self, parse):
        assert parse("'single # inside'") == "single # inside"

    def test_hash_without_preceding_space_kept(self, parse):
        assert parse("key#nospace") == "key#nospace"

    def test_escaped_quotes_in_double_quotes(self, parse):
        assert parse(r'"say \"hi\""') == 'say "hi"'

    def test_empty_is_empty(self, parse):
        assert parse("") == ""

    def test_whitespace_trimmed(self, parse):
        assert parse("   spaced   ") == "spaced"

    def test_mismatched_quotes_preserved_raw(self, parse):
        """引号不配对说明它多半是值的一部分，不瞎剥。"""
        assert parse('"unclosed') == '"unclosed'
        assert parse("unclosed'") == "unclosed'"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两条校验路径对同一份审查产物必须给出一致结论。

提交链路里有两处会读 `review_result`：

1. **`artifact_validator` → `chapter_commit_schema.ReviewResult`**（严格）
   由 `write_gates/precommit.py:86` 调用，`ok=False` 会**阻断整章提交**。

2. **`review_schema.parse_review_output`**（宽松）
   `chapter_commit_service.build_commit()` 用它归一化。

两者对 `blocking_count` 的容忍度不同。实测：

    blocking_count      严格 schema      宽松 review_schema
    -----------------   ---------------   ------------------
    3                   ✅ 通过            ✅ 3
    "3"                 ❌ ValidationError  ✅ 3
    3.0                 ❌ ValidationError  ✅ 3
    -1                  ❌ ValidationError  ✅ 归零
    True                ❌ ValidationError  ✅ 归零

## 为什么这是缺陷

`blocking_count: "3"` 是 LLM 输出里**非常常见**的形态（JSON 里数字被写成
字符串，或模型从 Markdown 抄了个带引号的值）。严格 schema 报 error →
`precommit` 门禁 → **整章提交被阻断**，而下游那个"真正决定阻断与否"的宽松
解析器其实完全能处理它。

也就是说：一个纯格式问题，让作者卡在"提交不了"，而错误信息是
`Input should be a valid integer`——作者看不出这是 `"3"` vs `3` 的问题。

`-1` 的情况更微妙：严格 schema 拒绝（卡死），宽松解析归零（放行）。两条路
都不理想，但至少应当一致。

## 本文件钉住

- 数字字符串 `"3"` 应被接受（归一为 3）
- 浮点 `3.0` 应被接受（归一为 3，截断）
- `True`/`False` 不接受（bool 不是计数）
- 负数不接受（计数不能为负），但**报错信息要说清**
- 两条路径对同一输入的一致性
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.chapter_commit_schema import ReviewResult as StrictReview  # noqa: E402
from data_modules.review_schema import parse_review_output  # noqa: E402


class TestStrictSchemaAcceptsCommonShapes:
    """核心：模型常见的数字字符串/浮点不该阻断提交。"""

    @pytest.mark.parametrize("value,expected", [
        (3, 3),
        ("3", 3),
        (3.0, 3),
        (0, 0),
        ("0", 0),
        (12, 12),
        ("12", 12),
    ])
    def test_accepts(self, value, expected):
        model = StrictReview(blocking_count=value)
        assert model.blocking_count == expected, (
            f"blocking_count={value!r} 归一为 {model.blocking_count}，期望 {expected}"
        )

    def test_string_with_whitespace(self):
        assert StrictReview(blocking_count=" 3 ").blocking_count == 3

    def test_float_truncates(self):
        assert StrictReview(blocking_count=3.7).blocking_count == 3


class TestStrictSchemaRejectsGenuinelyInvalid:
    """真正非法的值仍要拒绝。"""

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected(self, value):
        with pytest.raises(Exception):
            StrictReview(blocking_count=value)

    @pytest.mark.parametrize("value", [-1, -100])
    def test_negative_rejected(self, value):
        with pytest.raises(Exception):
            StrictReview(blocking_count=value)

    @pytest.mark.parametrize("value", ["abc", "3个", "", None])
    def test_garbage_rejected(self, value):
        with pytest.raises(Exception):
            StrictReview(blocking_count=value)

    def test_negative_error_message_is_readable(self):
        with pytest.raises(Exception) as excinfo:
            StrictReview(blocking_count=-1)
        msg = str(excinfo.value)
        assert "blocking_count" in msg, f"错误信息应指明字段：{msg[:200]}"


class TestTwoPathsAgree:
    """两条路径对同一输入必须一致。"""

    @pytest.mark.parametrize("value", [3, "3", 0, "0", 12])
    def test_agree_on_valid(self, value):
        strict_ok = True
        try:
            strict = StrictReview(blocking_count=value).blocking_count
        except Exception:
            strict_ok = False
            strict = None

        lenient = parse_review_output(1, {"blocking_count": value,
                                          "issues": []}).declared_blocking_count

        assert strict_ok, (
            f"blocking_count={value!r} 被严格 schema 拒绝，"
            f"但宽松解析器得到 {lenient}。前者会阻断整章提交。"
        )
        assert strict == lenient, (
            f"blocking_count={value!r}: 严格={strict}, 宽松={lenient}，两条路径不一致"
        )

    @pytest.mark.parametrize("value", [-1, -100])
    def test_agree_on_negative(self, value):
        """负数两边都不该当成有效阻断数。"""
        with pytest.raises(Exception):
            StrictReview(blocking_count=value)
        lenient = parse_review_output(1, {"blocking_count": value,
                                          "issues": []}).declared_blocking_count
        assert lenient == 0, (
            f"blocking_count={value!r} 在宽松路径被当成 {lenient}，应为 0"
        )


class TestNoBlockOnFormatIssue:
    """端到端：格式问题不该让 precommit 门禁阻断。"""

    def test_artifact_validator_accepts_string_count(self):
        from data_modules.artifact_validator import validate_artifact_payload

        report = validate_artifact_payload(
            "review_result", {"blocking_count": "3", "issues": []})
        assert report["ok"], (
            f"artifact_validator 拒绝了 blocking_count='3'："
            f"{[e['message'] for e in report['errors']]}。"
            f"这是模型常见输出形态，会阻断整章提交。"
        )

    def test_artifact_validator_accepts_float_count(self):
        from data_modules.artifact_validator import validate_artifact_payload

        report = validate_artifact_payload(
            "review_result", {"blocking_count": 3.0, "issues": []})
        assert report["ok"]

    def test_artifact_validator_still_rejects_negative(self):
        from data_modules.artifact_validator import validate_artifact_payload

        report = validate_artifact_payload(
            "review_result", {"blocking_count": -1, "issues": []})
        assert not report["ok"], "负数计数应仍被拒绝"


class TestCustomErrorMessage:
    """负数必须由**自定义校验**拒绝，错误信息要能读。

    Pydantic 的 `Field(ge=0)` 也能拦住负数——所以删掉自定义的 `if number < 0`
    后功能测试仍然通过（突变测试证实）。这里钉住错误信息：自定义校验给的
    是中文可读提示，Pydantic 给的是 "Input should be greater than or equal to 0"。
    靠它保证作者看到的是修复建议而不是解析器术语。
    """

    @pytest.mark.parametrize("value", [-1, -100, "-3"])
    def test_negative_has_chinese_message(self, value):
        with pytest.raises(Exception) as excinfo:
            StrictReview(blocking_count=value)
        blob = str(excinfo.value)
        # 必须含中文提示，不能只含字段名——Pydantic 的 `ge=0` 兜底时错误信息
        # 是 "Input should be greater than or equal to 0"，也含 blocking_count，
        # 用 `or` 会漏放。（突变测试证实：删掉自定义校验后 or 版本仍通过。）
        assert "不能为负" in blob, (
            f"负数的错误信息不是自定义的中文提示：{blob[:200]}"
        )

    def test_garbage_message_names_field(self):
        with pytest.raises(Exception) as excinfo:
            StrictReview(blocking_count="abc")
        assert "blocking_count" in str(excinfo.value)

"""审查闸门 fail-closed 回归测试（第4轮审查流水线域 P0）。

``parse_review_output`` 原本对 ``raw.get("issues", [])`` 逐元素做
``isinstance(item, dict)``，非 dict 就 continue。当 LLM 把 issues 写成对象
（``{"severity": "critical", ...}``）或字符串（``"共 3 个 critical 问题"``）时，
迭代产出的是键或字符，全部被丢弃 ⇒ issues 为空 ⇒ ``has_blocking: False``、
``overall_score: 100.0`` ⇒ 审查闸门（review_pipeline.build_review_artifacts 与
chapter_commit_service.build_commit 都只读这一个对象）把被否掉的章节判为
accepted 并写进 SSOT 事件日志。

防回归覆盖：
- 形状异常的 issues 必须 fail-closed（产生 blocking 问题，转人工确认），
  而不是静默放行；
- 正常 list 形状不受影响；
- 顶层载荷不是 dict 时不得抛 AttributeError（chapter_commit.py 只捕获
  FileNotFoundError/JSONDecodeError/ValueError）。
"""
import pytest

from data_modules.review_schema import parse_review_output


def _normal_issue(severity="high", description="金丹期不能飞行"):
    return {"severity": severity, "category": "power_system",
            "description": description, "location": "第3段"}


def test_issues_as_object_fails_closed_not_open():
    """P0：issues 写成对象时不得被判为「无问题 / 满分通过」。"""
    result = parse_review_output(3, {
        "summary": "存在严重设定冲突",
        "issues": {"severity": "critical", "description": "金丹期不能飞行"},
    })

    assert result.has_blocking, "issues 形状异常被静默丢弃，等于放行被否章节"
    assert result.issues, "必须产出至少一个 blocking 问题以转人工确认"
    assert result.blocking_count >= 1
    # 注意：不断言 overall_score。合成问题落在 category="other"，而 overall_score
    # 只按 CONTENT_DIMENSIONS 计算（review_schema._calculate_overall_score），
    # 故非 content 类问题本就不影响分数——那是既有的评分口径，不在本 P0 范围内。
    # 真正的闸门是 has_blocking / blocking_count，两个消费方都只读它们。


def test_issues_as_string_fails_closed():
    """issues 写成字符串（LLM 常见越界）同样必须 fail-closed。"""
    result = parse_review_output(3, {
        "summary": "共 3 个 critical 问题",
        "issues": "共 3 个 critical 问题",
    })

    assert result.has_blocking, "issues 为字符串时被逐字符丢弃后放行"


def test_issues_with_non_dict_entries_still_keeps_valid_ones():
    """list 形状中混入非 dict 元素时，有效问题必须保留，异常内容不得被放行。"""
    result = parse_review_output(3, {
        "summary": "有问题",
        "issues": [_normal_issue(severity="critical"), "多余的字符串"],
    })

    assert result.has_blocking
    assert any("金丹期" in i.description for i in result.issues)


def test_normal_list_shape_unaffected():
    """正常形状不回归：无 blocking 问题时仍是 100 分、has_blocking 为 False。"""
    result = parse_review_output(3, {
        "summary": "整体不错",
        "issues": [_normal_issue(severity="low")],
    })

    assert not result.has_blocking
    assert len(result.issues) == 1


def test_empty_issues_list_is_still_clean():
    """审查者明确报告「无问题」时不应被误判为形状异常。"""
    result = parse_review_output(3, {"summary": "无问题", "issues": []})

    assert not result.has_blocking
    assert result.issues == []


def test_missing_issues_key_is_still_clean():
    """缺 issues 键（审查者只写了 summary）视为无问题，不强行阻断。"""
    result = parse_review_output(3, {"summary": "无问题"})

    assert not result.has_blocking


def test_non_dict_payload_does_not_raise_attributeerror():
    """顶层载荷是 list 时不得抛 AttributeError（chapter_commit 只捕获 ValueError）。"""
    result = parse_review_output(3, [{"severity": "critical"}])

    assert result.has_blocking, "无法解析的审查载荷应 fail-closed"


@pytest.mark.parametrize("bad", [None, "审查通过", 42])
def test_non_dict_scalar_payload_does_not_raise(bad):
    """标量载荷同样不得抛异常。"""
    result = parse_review_output(3, bad)

    assert result.has_blocking

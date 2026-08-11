"""企业尽调公共规范化规则单元测试。"""

from datetime import date, datetime

import pytest

from yuxi.company_due_diligence.models import ConfidenceLevel
from yuxi.company_due_diligence.normalization import (
    is_confidence_allowed,
    normalize_amount,
    normalize_date,
    normalize_decimal,
    normalize_optional_text,
    normalize_percentage,
)


def test_decimal_and_percentage_use_stable_string_values():
    """金额和比例应避免浮点误差并使用统一十进制字符串。"""
    assert normalize_decimal("1,200.5000") == "1200.5"
    assert normalize_decimal("1200") == "1200"
    assert normalize_decimal("0.000") == "0"
    assert normalize_percentage("12.50%") == "0.125"
    assert normalize_percentage("0.125") == "0.125"


def test_amount_normalizes_chinese_magnitude_and_currency():
    """带万或亿后缀的金额应转换为基础货币单位并识别币种。"""
    assert normalize_amount("459.36万元") == ("4593600", "CNY")
    assert normalize_amount("1.5亿美元") == ("150000000", "USD")
    assert normalize_amount("80.50") == ("80.5", None)


def test_date_and_empty_values_have_consistent_representation():
    """日期和空值应转换为 date 与 None。"""
    assert normalize_optional_text("   ") is None
    assert normalize_date("2026/08/10") == date(2026, 8, 10)
    assert normalize_date("2026年08月10日") == date(2026, 8, 10)
    assert normalize_date(datetime(2026, 8, 10, 12, 0)) == date(2026, 8, 10)


def test_invalid_numeric_and_date_values_fail_explicitly():
    """非法非空值不得静默变成缺失数据。"""
    with pytest.raises(ValueError, match="十进制"):
        normalize_decimal("not-a-number")
    with pytest.raises(ValueError, match="业务日期"):
        normalize_date("2026-99-99")


def test_low_confidence_requires_explicit_line_mode():
    """中低置信度内容默认不得进入确定事实答案。"""
    assert is_confidence_allowed(ConfidenceLevel.HIGH) is True
    assert is_confidence_allowed(ConfidenceLevel.MEDIUM) is False
    assert is_confidence_allowed(ConfidenceLevel.LOW) is False
    assert is_confidence_allowed(ConfidenceLevel.LOW, include_low_confidence=True) is True

"""企业尽调公共字段规范化规则。"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from yuxi.company_due_diligence.models import ConfidenceLevel

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")
_AMOUNT_PATTERN = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(万|亿)?\s*(人民币|美元|港元|元)?$")
_CURRENCY_CODES = {"人民币": "CNY", "元": "CNY", "美元": "USD", "港元": "HKD"}


def normalize_optional_text(value: object) -> str | None:
    """把空值和纯空白文本统一转换为 None。"""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_decimal(value: object) -> str | None:
    """把数值转换为无科学计数法的精确十进制字符串。"""
    text = normalize_optional_text(value)
    if text is None:
        return None

    try:
        decimal_value = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"无法规范化十进制数值: {text}") from exc
    if not decimal_value.is_finite():
        raise ValueError("十进制数值必须是有限数")

    normalized = format(decimal_value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return "0" if normalized in {"", "-0"} else normalized


def normalize_percentage(value: object) -> str | None:
    """把百分号文本转换为 0 到 1 语义的十进制字符串。"""
    text = normalize_optional_text(value)
    if text is None:
        return None

    if text.endswith("%"):
        percentage = normalize_decimal(text[:-1])
        return normalize_decimal(Decimal(percentage) / Decimal(100))
    return normalize_decimal(text)


def normalize_amount(value: object) -> tuple[str | None, str | None]:
    """把带万/亿和常见币种后缀的金额转换为基础货币单位。"""
    text = normalize_optional_text(value)
    if text is None:
        return None, None

    compact = text.replace(",", "").replace(" ", "")
    match = _AMOUNT_PATTERN.fullmatch(compact)
    if match is None:
        raise ValueError(f"无法规范化金额: {text}")

    amount_text, magnitude, currency_text = match.groups()
    multiplier = {None: Decimal(1), "万": Decimal(10000), "亿": Decimal(100000000)}[magnitude]
    amount = normalize_decimal(Decimal(amount_text) * multiplier)
    return amount, _CURRENCY_CODES.get(currency_text)


def normalize_date(value: object) -> date | None:
    """把常见业务日期格式转换为 date，非法非空值显式失败。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    for date_format in _DATE_FORMATS[1:]:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"无法规范化业务日期: {text}")


def is_confidence_allowed(
    confidence: ConfidenceLevel,
    *,
    include_low_confidence: bool = False,
) -> bool:
    """默认只允许高置信度事实，显式线索模式才允许中低置信度。"""
    return confidence == ConfidenceLevel.HIGH or include_low_confidence

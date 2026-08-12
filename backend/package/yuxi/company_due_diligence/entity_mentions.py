"""从自然语言中提取企业法律全称、股票代码和统一社会信用代码。"""

from __future__ import annotations

import re

from yuxi.company_due_diligence.models import CompanyReference

_COMPANY_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,80}?(?:股份有限公司|有限责任公司|有限公司))")
_CREDIT_CODE_PATTERN = re.compile(r"(?<![0-9A-Z])([0-9A-Z]{18})(?![0-9A-Z])", re.IGNORECASE)
_STOCK_CODE_PATTERN = re.compile(r"(?<![0-9A-Z])(\d{6}(?:\.(?:SZ|SH|BJ))?)(?![0-9A-Z])", re.IGNORECASE)
_LEADING_MENTION_WORDS = ("请帮我", "帮我", "请分析", "请调查", "调查", "分析", "查询", "比较", "关于", "与", "和")


def extract_company_mentions(message: str) -> list[str]:
    """从明确法律全称中提取去重后的企业提及。"""
    mentions = []
    for match in _COMPANY_PATTERN.finditer(message):
        mention = _normalize_company_mention(match.group(1))
        if mention and mention not in mentions:
            mentions.append(mention)
    return mentions


def extract_company_references(message: str) -> list[CompanyReference]:
    """按自然语言出现顺序提取企业名称、信用代码和股票代码。"""
    matches: list[tuple[int, CompanyReference]] = []
    for match in _COMPANY_PATTERN.finditer(message):
        mention = _normalize_company_mention(match.group(1))
        if mention:
            matches.append((match.start(), CompanyReference(name=mention)))
    matches.extend(
        (match.start(), CompanyReference(credit_code=match.group(1).upper()))
        for match in _CREDIT_CODE_PATTERN.finditer(message)
    )
    matches.extend(
        (match.start(), CompanyReference(stock_code=match.group(1).upper()))
        for match in _STOCK_CODE_PATTERN.finditer(message)
    )
    return [reference for _, reference in sorted(matches, key=lambda item: item[0])]


def _normalize_company_mention(value: str) -> str:
    """去除企业法律全称前的常见提问词。"""
    mention = value.strip("，。；：、,.;: ")
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_MENTION_WORDS:
            if mention.startswith(prefix) and len(mention) > len(prefix):
                mention = mention[len(prefix) :].strip()
                changed = True
    return mention

"""引用式回答门禁：只对带引用的句子做确定性检查，不从回答中猜测事实。

引用格式为句末全角方括号，例如 ``［F:fact:3a1c…］``；前缀 F/R/P/E 分别对应
事实、关系、路径、证据，ID 必须来自本次 Run 中 ``cdd_`` 工具实际返回的结果。

- G1 ``citation_unknown``：引用 ID 不在本次工具结果中，或引用格式无法解析；省略 ``fact:`` 等类型前缀但
  能唯一对应一个结果时视为同一引用（不同模型抄写 ID 的习惯不同，含义没有歧义）。
- G2 ``citation_value_mismatch``：被引用事实的值没有出现在同一句中。只容忍千分位、
  百分比与小数、万元/亿元与元、日期 ``YYYY-MM-DD`` 与 ``YYYY年M月D日`` 几种格式差异；
  数值按句中写法的小数位四舍五入后比较。
- G3 ``citation_subject_mismatch``：被引用事实的主体既不是目标企业，也不是与目标企业
  直接相连的关联主体（如股东）。
- G4 只报告：只引用了证据（E）的句子使用确定性措辞。
- 只报告：未引用的句子含有两位以上的数字（如表格行、概括句），这些数字没有经过 G2 校验。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, Field

_CITATION_BLOCK = re.compile(r"［([^］]*)］")
_CITATION_TOKEN = re.compile(r"^([FRPE]):(\S+)$")
_SENTENCE_END = re.compile(r"(?<=[。！？!?\n])")
_LEADING_CITATIONS = re.compile(r"^\s*(?:［[^］]*］\s*)+")
# 千分位只接受英文逗号三位分组，避免把“10，20”这类列举当成一个数。
_NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|％|万|亿)?")
_SCALES = {"%": Decimal("0.01"), "％": Decimal("0.01"), "万": Decimal(10000), "亿": Decimal(100000000)}
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DEFINITE_WORDING = ("确认", "确定", "证实", "毫无疑问")


class GateViolation(BaseModel):
    """一条拦截原因，定位到句子和引用。"""

    code: str
    sentence_index: int
    citation: str
    message: str


class AnswerGateResult(BaseModel):
    """门禁结论与引用覆盖统计；只有 violations 决定是否通过。"""

    passed: bool
    violations: list[GateViolation] = Field(default_factory=list)
    warnings: list[GateViolation] = Field(default_factory=list)
    sentence_count: int
    cited_sentence_count: int
    uncited_fact_sentence_count: int

    @property
    def citation_coverage(self) -> float | None:
        """带引用的句子占“带引用 + 未引用但含本次事实值”句子的比例。"""
        total = self.cited_sentence_count + self.uncited_fact_sentence_count
        return self.cited_sentence_count / total if total else None


def validate_answer_citations(answer: str, tool_results: list[dict], company_eids: set[str]) -> AnswerGateResult:
    """按句检查引用；未引用的句子只计入覆盖率，不拦截。"""
    index = _index_tool_results(tool_results)
    allowed_subjects = company_eids | {
        other
        for relation in index["R"].values()
        for subject, other in (
            (relation["source_entity_id"], relation["target_entity_id"]),
            (relation["target_entity_id"], relation["source_entity_id"]),
        )
        if subject in company_eids
    }
    violations: list[GateViolation] = []
    warnings: list[GateViolation] = []
    cited = uncited_fact = 0
    # 经营范围等长文本自身含句号，会被切成多句；这类值允许连同引用前紧邻的未引用句一起比对。
    preceding = ""
    sentences = _split_sentences(answer)
    for position, sentence in enumerate(sentences):
        tokens = [t for block in _CITATION_BLOCK.findall(sentence) for t in re.split(r"[\s,，、;；]+", block) if t]
        text = _CITATION_BLOCK.sub("", sentence)
        if not tokens:
            if any(_value_in_sentence(fact, text, min_digits=2) for fact in index["F"].values()):
                uncited_fact += 1
            if any(sum(ch.isdigit() for ch in number) >= 2 for number, _ in _NUMBER.findall(text)):
                warnings.append(
                    GateViolation(
                        code="uncited_number",
                        sentence_index=position,
                        citation="",
                        message="未引用的句子含有数字，未经值一致校验",
                    )
                )
            preceding += text
            continue
        cited += 1
        prefixes = set()
        for token in tokens:
            match = _CITATION_TOKEN.match(token)
            target = _lookup(index[match.group(1)], match.group(2)) if match else None
            if target is None:
                violations.append(
                    GateViolation(
                        code="citation_unknown",
                        sentence_index=position,
                        citation=token,
                        message="引用 ID 不在本次工具结果中，或格式不是 F/R/P/E:ID",
                    )
                )
                continue
            prefixes.add(match.group(1))
            if match.group(1) != "F":
                continue
            if target["subject_id"] not in allowed_subjects:
                violations.append(
                    GateViolation(
                        code="citation_subject_mismatch",
                        sentence_index=position,
                        citation=token,
                        message=f"事实主体 {target['subject_id']} 不属于目标企业及其关联主体",
                    )
                )
            spans_sentences = bool(_SENTENCE_END.search(str(target["normalized_value"]).rstrip("。！？!?\n")))
            if not (
                _value_in_sentence(target, text) or spans_sentences and _value_in_sentence(target, preceding + text)
            ):
                violations.append(
                    GateViolation(
                        code="citation_value_mismatch",
                        sentence_index=position,
                        citation=token,
                        message=f"句中没有出现事实值 {target.get('display_value') or target['normalized_value']}",
                    )
                )
        if prefixes == {"E"} and any(word in text for word in _DEFINITE_WORDING):
            warnings.append(
                GateViolation(
                    code="evidence_definite_wording",
                    sentence_index=position,
                    citation="",
                    message="只引用文本证据的句子使用了确定性措辞",
                )
            )
        preceding = ""
    return AnswerGateResult(
        passed=not violations,
        violations=violations,
        warnings=warnings,
        sentence_count=len(sentences),
        cited_sentence_count=cited,
        uncited_fact_sentence_count=uncited_fact,
    )


def _lookup(results: dict[str, dict], result_id: str) -> dict | None:
    """按 ID 取结果；模型省略“fact:”等类型前缀时，只有唯一匹配才认，ID 本身仍须逐字正确。"""
    if result_id in results:
        return results[result_id]
    matches = [result for key, result in results.items() if key.split(":", 1)[-1] == result_id]
    return matches[0] if len(matches) == 1 else None


def _index_tool_results(tool_results: list[dict]) -> dict[str, dict[str, dict]]:
    """把各 cdd_ 工具返回体按引用前缀和结果 ID 建索引；路径中的边也可被 R 引用。"""
    index: dict[str, dict[str, dict]] = {"F": {}, "R": {}, "P": {}, "E": {}}
    for result in tool_results:
        index["F"].update((fact["fact_id"], fact) for fact in result.get("facts", []))
        index["R"].update((relation["relation_id"], relation) for relation in result.get("relations", []))
        index["E"].update((item["evidence_id"], item) for item in result.get("evidence", []))
        for path in result.get("paths", []):
            index["P"][path["path_id"]] = path
            index["R"].update((edge["relation_id"], edge) for edge in path["edges"])
    return index


def _split_sentences(answer: str) -> list[str]:
    """按句末标点和换行切句；写在标点之后的引用归回前一句。"""
    sentences: list[str] = []
    for segment in _SENTENCE_END.split(answer):
        leading = _LEADING_CITATIONS.match(segment)
        if leading and sentences:
            sentences[-1] += leading.group(0)
            segment = segment[leading.end() :]
        if segment.strip():
            sentences.append(segment)
    return sentences


def _value_in_sentence(fact: dict, text: str, *, min_digits: int = 0) -> bool:
    """事实的展示值或规范值原样出现，或句中某个数按其单位和小数位与规范值一致。"""
    compact = re.sub(r"(?<=\d),(?=\d{3})|\s", "", text)
    if isinstance(fact["normalized_value"], dict):
        # 专利、商标等条目事实的值是 {item_id, name}：比对名称等文本字段，不要求写出内部 ID。
        names = [v for k, v in fact["normalized_value"].items() if not k.endswith("id") and isinstance(v, str)]
        return any(len(name) >= 2 and re.sub(r"\s", "", name) in compact for name in names)
    literals = {
        re.sub(r"\s", "", str(v)) for v in (fact.get("display_value"), fact["normalized_value"]) if v is not None
    }
    if date := _ISO_DATE.fullmatch(str(fact["normalized_value"])):
        year, month, day = (int(part) for part in date.groups())
        literals |= {f"{year}年{month}月{day}日", f"{year}年{month:02d}月{day:02d}日"}
    if any(len(literal) >= 2 and literal in compact for literal in literals):
        return True
    try:
        value = Decimal(str(fact["normalized_value"]))
    except InvalidOperation:
        return False
    if not value.is_finite():
        return False
    for number, unit in _NUMBER.findall(text):
        written = Decimal(number.replace(",", ""))
        if sum(ch.isdigit() for ch in number) < min_digits:
            continue
        places = Decimal(1).scaleb(written.as_tuple().exponent)
        if (value / _SCALES.get(unit, Decimal(1))).quantize(places, ROUND_HALF_UP) == written:
            return True
    return False

"""引用式回答门禁单元测试；事实取值形态与 cdd_query_facts 真实返回一致。"""

from yuxi.company_due_diligence.answer_gate import validate_answer_citations

EID = "eid-a"
TOOL_RESULTS = [
    {
        "facts": [
            {
                "fact_id": "fact:cap",
                "subject_id": EID,
                "fact_type": "registered_capital",
                "normalized_value": "302995606",
                "display_value": "30299.5606万元人民币",
            },
            {
                "fact_id": "fact:rep",
                "subject_id": EID,
                "fact_type": "legal_representative",
                "normalized_value": "包晓军",
            },
            {
                "fact_id": "fact:ratio",
                "subject_id": "shareholder:s1",
                "fact_type": "ownership_ratio",
                "normalized_value": "0.4158572253354724",
                "display_value": "0.4158572253354724",
            },
            {
                "fact_id": "fact:other",
                "subject_id": "eid-b",
                "fact_type": "legal_representative",
                "normalized_value": "张三",
            },
        ],
        "relations": [
            {"relation_id": "relation:holder", "source_entity_id": EID, "target_entity_id": "shareholder:s1"},
        ],
    },
    {"evidence": [{"evidence_id": "evidence:e1", "entity_id": EID, "text": "公告"}]},
]


def _codes(answer: str) -> list[str]:
    return [v.code for v in validate_answer_citations(answer, TOOL_RESULTS, {EID}).violations]


def test_correct_citations_pass_with_multiple_citations_in_one_sentence():
    """同一句多个引用各自核对；正确回答不应被拦。"""
    result = validate_answer_citations(
        "注册资本为30299.5606万元人民币，法定代表人为包晓军［F:fact:cap］［F:fact:rep］。", TOOL_RESULTS, {EID}
    )

    assert result.passed
    assert result.cited_sentence_count == 1


def test_unknown_or_malformed_citation_is_blocked():
    """G1：编造 ID、错用前缀或格式错误都算未知引用。"""
    assert _codes("法定代表人为包晓军［F:fact:missing］。") == ["citation_unknown"]
    assert _codes("法定代表人为包晓军［R:fact:rep］。") == ["citation_unknown"]
    assert _codes("法定代表人为包晓军［fact:rep］。") == ["citation_unknown"]


def test_citation_without_type_prefix_resolves_when_unique():
    """省略 fact: 前缀但唯一对应时视为同一引用；ID 写错或用事实类型名代替仍拦。"""
    assert _codes("法定代表人为包晓军［F:rep］。") == []
    assert _codes("法定代表人为李四［F:rep］。") == ["citation_value_mismatch"]
    assert _codes("法定代表人为包晓军［F:legal_representative］。") == ["citation_unknown"]


def test_value_tolerates_thousands_separator_percent_and_wan_yuan():
    """G2 只容忍三种格式差异，数值按句中写法的小数位四舍五入。"""
    assert _codes("注册资本为302,995,606元［F:fact:cap］。") == []
    assert _codes("注册资本为 30299.5606 万元［F:fact:cap］。") == []
    assert _codes("第一大股东持股41.59%［F:fact:ratio］。") == []
    assert _codes("第一大股东持股比例为0.4159［F:fact:ratio］。") == []
    assert _codes("注册资本约3.03亿元［F:fact:cap］。") == []
    assert _codes("注册资本为3.029956亿元［F:fact:cap］。") == []
    # 恰好落在 5 上时按四舍五入（1.25% 写作 1.3%），而不是银行家舍入。
    half = [{"facts": [{"fact_id": "fact:h", "subject_id": EID, "normalized_value": "0.0125"}]}]
    assert validate_answer_citations("持股1.3%［F:fact:h］。", half, {EID}).passed


def test_date_tolerates_chinese_format_but_not_wrong_day():
    """G2 日期容忍：2014-05-22 可写作 2014年5月22日 或 2014年05月22日，日期写错仍拦。"""
    results = [{"facts": [{"fact_id": "fact:date", "subject_id": EID, "normalized_value": "2014-05-22"}]}]

    for answer in (
        "成立于2014-05-22［F:fact:date］。",
        "成立于2014年5月22日［F:fact:date］。",
        "成立于2014年05月22日［F:fact:date］。",
    ):
        assert validate_answer_citations(answer, results, {EID}).passed, answer
    assert not validate_answer_citations("成立于2014年5月23日［F:fact:date］。", results, {EID}).passed


def test_wrong_value_or_unit_is_blocked():
    """G2：数值写错、单位写错（万元写成元）或换了人名都拦。"""
    assert _codes("注册资本为30299.5606元［F:fact:cap］。") == ["citation_value_mismatch"]
    assert _codes("注册资本为3.13亿元［F:fact:cap］。") == ["citation_value_mismatch"]
    assert _codes("第一大股东持股45.59%［F:fact:ratio］。") == ["citation_value_mismatch"]
    assert _codes("法定代表人为李四［F:fact:rep］。") == ["citation_value_mismatch"]


def test_citation_ids_are_not_read_as_values():
    """引用块中的十六进制 ID 不能被当成句中的数值。"""
    results = [{"facts": [{"fact_id": "fact:302995606", "subject_id": EID, "normalized_value": "302995606"}]}]

    assert not validate_answer_citations("注册资本未披露［F:fact:302995606］。", results, {EID}).passed


def test_item_fact_matches_by_name_not_internal_id():
    """专利等条目事实的值是 {item_id, name}：写出名称即可，名称写错仍拦。"""
    item = {"item_id": "patent:c33d", "name": "一种改性的环氧树脂及其制备方法"}
    results = [{"facts": [{"fact_id": "fact:p", "subject_id": EID, "normalized_value": item}]}]

    assert validate_answer_citations(
        "已登记专利《一种改性的环氧树脂及其制备方法》［F:fact:p］。", results, {EID}
    ).passed
    assert not validate_answer_citations("已登记专利《一种高耐热环氧树脂》［F:fact:p］。", results, {EID}).passed


def test_fact_of_unrelated_subject_is_blocked():
    """G3：混入另一家企业的事实；目标企业的股东事实允许引用。"""
    assert _codes("法定代表人为张三［F:fact:other］。") == ["citation_subject_mismatch"]


def test_citation_after_sentence_punctuation_belongs_to_previous_sentence():
    """引用写在句号之后时归回前一句，而不是成为孤立的一句。"""
    result = validate_answer_citations("法定代表人为包晓军。［F:fact:rep］\n以下为股东情况。", TOOL_RESULTS, {EID})

    assert result.passed
    assert (result.sentence_count, result.cited_sentence_count) == (2, 1)


def test_long_value_containing_full_stop_matches_across_split_sentences():
    """经营范围自身含句号时，原样抄写不应因切句被拦；改写或缩写仍然拦。"""
    scope = "一般项目：软件开发。（依法自主开展经营活动）许可项目：建设工程施工。"
    results = [{"facts": [{"fact_id": "fact:scope", "subject_id": EID, "normalized_value": scope}]}]

    assert validate_answer_citations(f"法定代表人为包晓军。经营范围为{scope}［F:fact:scope］", results, {EID}).passed
    assert not validate_answer_citations("经营范围包括软件开发等［F:fact:scope］。", results, {EID}).passed
    # 普通短值仍只在本句比对，不能借用前一句。
    assert _codes("法定代表人为包晓军。法定代表人为李四［F:fact:rep］。") == ["citation_value_mismatch"]


def test_uncited_fact_sentence_is_counted_not_blocked():
    """没有引用的句子不拦截，只在含本次事实值时计入覆盖率分母。"""
    result = validate_answer_citations(
        "法定代表人为包晓军。以下为第1部分。注册资本为30299.5606万元［F:fact:cap］。", TOOL_RESULTS, {EID}
    )

    assert result.passed
    assert (result.cited_sentence_count, result.uncited_fact_sentence_count) == (1, 1)
    assert result.citation_coverage == 0.5


def test_uncited_numbers_are_warned_not_blocked():
    """表格行等未引用但含数字的句子只告警；一位数字（如“第1部分”）不告警。"""
    result = validate_answer_citations(
        "| 王敏 | 40.61% | 300万 |\n以下为第1部分。\n法定代表人为包晓军［F:fact:rep］。", TOOL_RESULTS, {EID}
    )

    assert result.passed
    assert [(w.code, w.sentence_index) for w in result.warnings] == [("uncited_number", 0)]


def test_evidence_only_definite_wording_is_warning_only():
    """G4：只引用证据却用确定性措辞，只报告不拦截。"""
    result = validate_answer_citations("公告确认公司已完成融资［E:evidence:e1］。", TOOL_RESULTS, {EID})

    assert result.passed
    assert [w.code for w in result.warnings] == ["evidence_definite_wording"]

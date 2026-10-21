"""企业身份提及的边界回归测试。"""

from yuxi.company_due_diligence.entity_mentions import extract_company_references


def test_quoted_legal_names_produce_one_reference_per_mention():
    """引号点名和法律全称匹配重叠时，不能把第二家企业替换成第一家。"""
    references = extract_company_references("查询企业“甲方科技有限公司”和企业“乙方科技有限公司”的关联路径")
    assert [reference.name for reference in references] == ["甲方科技有限公司", "乙方科技有限公司"]

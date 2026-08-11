"""企业尽调 Claim Ledger 单元测试。"""

from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.agent.quality import ClaimValidationError, build_claim_ledger
from yuxi.company_due_diligence.models import Fact, Source


def test_claim_ledger_maps_fact_to_existing_source():
    """每条事实应映射到响应中的有效来源。"""
    source = Source(
        source_id="source-1",
        table="t_enterprise",
        record_key="eid-1",
        queried_at=datetime.now(UTC),
    )
    fact = Fact(
        fact_id="fact-1",
        subject_id="eid-1",
        fact_type="registered_capital",
        normalized_value=100,
        source_id=source.source_id,
    )

    ledger = build_claim_ledger(facts=[fact], relations=[], paths=[], evidence=[], sources=[source])

    assert ledger.validated is True
    assert ledger.claims[0].result_id == fact.fact_id
    assert ledger.claims[0].source_ids == [source.source_id]


def test_claim_ledger_rejects_missing_source():
    """无法回查来源的事实不得进入领域智能体输出。"""
    fact = Fact(
        fact_id="fact-1",
        subject_id="eid-1",
        fact_type="registered_capital",
        normalized_value=100,
        source_id="missing-source",
    )

    with pytest.raises(ClaimValidationError, match="来源校验失败"):
        build_claim_ledger(facts=[fact], relations=[], paths=[], evidence=[], sources=[])

"""企业尽调领域智能体的 Claim Ledger 来源校验。"""

from __future__ import annotations

from yuxi.company_due_diligence.agent.models import Claim, ClaimLedger, ClaimType
from yuxi.company_due_diligence.models import Evidence, Fact, Path, Relation, Source


class ClaimValidationError(RuntimeError):
    """表示最终结果包含无法回查到来源的数据。"""


def build_claim_ledger(
    *,
    facts: list[Fact],
    relations: list[Relation],
    paths: list[Path],
    evidence: list[Evidence],
    sources: list[Source],
) -> ClaimLedger:
    """校验每条最终结果的来源，并建立稳定 Claim Ledger。"""
    available_source_ids = {source.source_id for source in sources}
    claims = [
        Claim(
            claim_id=f"claim:fact:{fact.fact_id}",
            claim_type=ClaimType.FACT,
            result_id=fact.fact_id,
            source_ids=[fact.source_id],
        )
        for fact in facts
    ]
    claims.extend(
        Claim(
            claim_id=f"claim:relation:{relation.relation_id}",
            claim_type=ClaimType.RELATION,
            result_id=relation.relation_id,
            source_ids=list(dict.fromkeys(relation.source_ids)),
        )
        for relation in relations
    )
    claims.extend(
        Claim(
            claim_id=f"claim:path:{path.path_id}",
            claim_type=ClaimType.PATH,
            result_id=path.path_id,
            source_ids=list(dict.fromkeys(path.source_ids)),
        )
        for path in paths
    )
    claims.extend(
        Claim(
            claim_id=f"claim:evidence:{item.evidence_id}",
            claim_type=ClaimType.EVIDENCE,
            result_id=item.evidence_id,
            source_ids=[item.source_id],
        )
        for item in evidence
    )

    missing_source_ids = {
        source_id for claim in claims for source_id in claim.source_ids if source_id not in available_source_ids
    }
    if missing_source_ids:
        raise ClaimValidationError("调查结果来源校验失败，未输出未经验证的事实。")
    return ClaimLedger(claims=claims)

from yuxi.company_relation.paths import get_default_snapshot_path
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


def test_frozen_snapshot_meets_project_scale_and_source_requirements():
    snapshot = CompanyRelationSnapshot.load(get_default_snapshot_path())

    validation = snapshot.validate()

    assert validation.valid is True
    assert validation.node_count >= 500
    assert validation.edge_count >= 1000
    assert validation.source_coverage == 1.0
    assert len(snapshot.list_nodes("Company")) >= 30
    assert len(snapshot.list_nodes("Announcement")) >= 10


def test_snapshot_validation_reports_orphan_edge(company_relation_snapshot):
    payload = company_relation_snapshot.raw_payload | {
        "edges": [
            *company_relation_snapshot.raw_payload["edges"],
            {
                "id": "holds:missing:a",
                "type": "HOLDS",
                "source": "company:missing",
                "target": "company:a",
                "properties": {},
                "source_ids": ["source:holder"],
            },
        ]
    }

    validation = CompanyRelationSnapshot.from_dict(payload).validate()

    assert validation.valid is False
    assert validation.orphan_edge_ids == ("holds:missing:a",)

"""从只读 MySQL 构建企业尽调 Neo4j 投影快照。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import bindparam, text

from yuxi.company_due_diligence.entity_resolution import normalize_company_name
from yuxi.company_due_diligence.graph.schema import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationType,
    ProjectionSnapshot,
    ProjectionSnapshotStore,
    create_staging_store,
    publish_snapshot,
)
from yuxi.company_due_diligence.models import ConfidenceLevel, build_source_id, build_stable_id
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL, ReadonlyConnection, company_due_diligence_mysql
from yuxi.company_due_diligence.normalization import normalize_optional_text, normalize_percentage

ProgressCallback = Callable[[float, str], Awaitable[None]]


class GraphProjectionBuilder:
    """按稳定主键分批抽取 MySQL 并生成磁盘去重快照。"""

    def __init__(
        self,
        mysql: CompanyDueDiligenceMySQL = company_due_diligence_mysql,
        snapshot_root: Path | None = None,
    ) -> None:
        self._mysql = mysql
        self._snapshot_root = snapshot_root

    async def build_snapshot(
        self,
        *,
        eids: list[str] | None = None,
        batch_size: int = 1000,
        progress: ProgressCallback | None = None,
    ) -> ProjectionSnapshot:
        """先完成全量快照和 checksum，再交给 Neo4j 写入。"""
        store = create_staging_store(self._snapshot_root)
        try:
            async with self._mysql.connection() as connection:
                company_eids = await self._extract_companies(connection, store, eids, batch_size)
                if eids:
                    missing = sorted(set(eids) - company_eids)
                    if missing:
                        raise ValueError(f"以下 eid 不存在: {', '.join(missing[:10])}")
                await _report(progress, 10, "企业节点抽取完成")

                await self._extract_shareholders(connection, store, company_eids, eids, batch_size)
                await _report(progress, 22, "股东关系抽取完成")
                await self._extract_financing(connection, store, company_eids, eids, batch_size)
                await _report(progress, 34, "融资关系抽取完成")
                await self._extract_patents(connection, store, company_eids, eids, batch_size)
                await _report(progress, 48, "专利关系抽取完成")
                await self._extract_trademarks(connection, store, company_eids, eids, batch_size)
                await _report(progress, 58, "商标关系抽取完成")
                await self._extract_copyrights(connection, store, company_eids, eids, batch_size)
                await _report(progress, 68, "著作权关系抽取完成")
                await self._extract_administrative_penalties(connection, store, company_eids, eids, batch_size)
                await _report(progress, 78, "行政处罚关系抽取完成")
                await self._extract_environmental_penalties(connection, store, company_eids, eids, batch_size)
                await _report(progress, 86, "环保处罚关系抽取完成")
                await self._extract_licenses(connection, store, company_eids, eids, batch_size)
                await _report(progress, 92, "许可关系抽取完成")

            manifest = store.finalize(sorted(company_eids) if eids else None)
            await _report(progress, 95, "投影快照校验和统计完成")
            return publish_snapshot(store, manifest)
        except Exception:
            with suppress(Exception):
                store.close()
            with suppress(FileNotFoundError):
                store.path.unlink()
            raise

    async def _extract_companies(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        eids: list[str] | None,
        batch_size: int,
    ) -> set[str]:
        """抽取标准企业节点并返回本次有效 eid 集合。"""
        company_eids: set[str] = set()
        query = """
            SELECT e.eid, e.credit_no, e.name, e.status, e.oper_name,
                   e.start_date, l.stock_code, l.stock_abbr
            FROM t_enterprise e
            LEFT JOIN listed_company_gd l ON l.eid = e.eid
            WHERE e.eid > :cursor {eid_filter}
            ORDER BY e.eid
            LIMIT :limit
        """
        async for rows in _iter_batches(
            connection,
            query,
            "e.eid",
            "eid",
            eids,
            batch_size,
            initial_cursor="",
        ):
            for row in rows:
                eid = row["eid"]
                company_eids.add(eid)
                source_id = build_source_id("t_enterprise", eid)
                store.add_node(
                    GraphNode(
                        node_id=eid,
                        node_type=GraphNodeType.COMPANY,
                        name=_text(row.get("name")) or eid,
                        properties=_compact(
                            {
                                "eid": eid,
                                "credit_code": _text(row.get("credit_no")),
                                "stock_code": _text(row.get("stock_code")),
                                "stock_abbreviation": _text(row.get("stock_abbr")),
                                "status": _text(row.get("status")),
                                "legal_representative": _text(row.get("oper_name")),
                                "established_date": _date_text(row.get("start_date")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
            store.commit()
        return company_eids

    async def _extract_shareholders(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """抽取当前和历史持股关系，缺少稳定 ID 时降为低置信度。"""
        query = """
            SELECT p.id, p.eid, p.stock_name, p.stock_id, p.stock_type,
                   p.stock_percent, p.start_date, p.end_date, p.is_history
            FROM t_partners_alls p
            WHERE p.id > :cursor {eid_filter}
            ORDER BY p.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "p.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_partners_alls", record_key)
                shareholder_name = _text(row.get("stock_name")) or "未命名股东"
                stable_key = _text(row.get("stock_id"))
                confidence = ConfidenceLevel.HIGH if stable_key else ConfidenceLevel.LOW
                shareholder_id = build_stable_id(
                    "shareholder",
                    stable_key or normalize_company_name(shareholder_name),
                )
                store.add_node(
                    GraphNode(
                        node_id=shareholder_id,
                        node_type=GraphNodeType.SHAREHOLDER,
                        name=shareholder_name,
                        properties=_compact(
                            {
                                "stock_id": stable_key,
                                "stock_type": _text(row.get("stock_type")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.HOLDS,
                        shareholder_id,
                        row["eid"],
                        "t_partners_alls",
                        record_key,
                        confidence,
                        {
                            "ownership_ratio": _percentage(row.get("stock_percent")),
                            "is_history": bool(row.get("is_history")),
                            "effective_start": _date_text(row.get("start_date")),
                            "effective_end": _date_text(row.get("end_date")),
                        },
                    )
                )
            store.commit()

    async def _extract_financing(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """抽取融资事件和名称型投资人线索关系。"""
        query = """
            SELECT f.id, f.eid, f.round_date, f.round, f.round_type, f.investors_json
            FROM t_company_finance f
            WHERE f.id > :cursor {eid_filter}
            ORDER BY f.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "f.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_company_finance", record_key)
                event_id = build_stable_id("financing", record_key)
                store.add_node(
                    GraphNode(
                        node_id=event_id,
                        node_type=GraphNodeType.FINANCING_EVENT,
                        name=_text(row.get("round")) or _text(row.get("round_type")) or "融资事件",
                        properties=_compact(
                            {
                                "round": _text(row.get("round")),
                                "round_type": _text(row.get("round_type")),
                                "round_date": _date_text(row.get("round_date")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.RECEIVED_FINANCING,
                        row["eid"],
                        event_id,
                        "t_company_finance",
                        record_key,
                        ConfidenceLevel.HIGH,
                        {"effective_start": _date_text(row.get("round_date"))},
                    )
                )
                for index, investor in enumerate(_json_objects(row.get("investors_json"))):
                    investor_name = _text(investor.get("text"))
                    if not investor_name:
                        continue
                    investor_type = _text(investor.get("type"))
                    investor_id = build_stable_id(
                        "investor",
                        normalize_company_name(investor_name),
                        investor_type or "",
                    )
                    store.add_node(
                        GraphNode(
                            node_id=investor_id,
                            node_type=GraphNodeType.INVESTOR,
                            name=investor_name,
                            properties={"investor_type": investor_type} if investor_type else {},
                            source_ids=[source_id],
                        )
                    )
                    store.add_edge(
                        _edge(
                            GraphRelationType.INVESTED_IN,
                            investor_id,
                            row["eid"],
                            "t_company_finance",
                            record_key,
                            ConfidenceLevel.LOW,
                            {
                                "financing_event_id": event_id,
                                "investor_type": investor_type,
                                "effective_start": _date_text(row.get("round_date")),
                                "investor_index": index,
                            },
                            suffix=str(index),
                        )
                    )
            store.commit()

    async def _extract_patents(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """按 eid 和主键游标抽取专利关系，不扫描摘要正文。"""
        query = """
            SELECT i.id, i._id, i.eid, i.patent_name, i.request_num_standard,
                   i.type_name, i.last_status, i.request_date, i.authorize_date, i.is_history
            FROM t_patents_relations_info i
            WHERE i.id > :cursor {eid_filter}
            ORDER BY i.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "i.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_patents_relations_info", record_key)
                stable_key = _text(row.get("request_num_standard"))
                confidence = ConfidenceLevel.HIGH if stable_key else ConfidenceLevel.MEDIUM
                patent_id = build_stable_id("patent", stable_key or row.get("_id") or record_key)
                store.add_node(
                    GraphNode(
                        node_id=patent_id,
                        node_type=GraphNodeType.PATENT,
                        name=_text(row.get("patent_name")) or stable_key or f"专利-{record_key}",
                        properties=_compact(
                            {
                                "request_number": stable_key,
                                "type_name": _text(row.get("type_name")),
                                "last_status": _text(row.get("last_status")),
                                "is_history": bool(row.get("is_history")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.OWNS_PATENT,
                        row["eid"],
                        patent_id,
                        "t_patents_relations_info",
                        record_key,
                        confidence,
                        {
                            "effective_start": _date_text(row.get("authorize_date") or row.get("request_date")),
                            "is_history": bool(row.get("is_history")),
                        },
                    )
                )
            store.commit()

    async def _extract_trademarks(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """按 eid 和主键游标抽取商标稳定注册号关系。"""
        query = """
            SELECT i.id, i._id, i.eid, i.reg_number, i.type_num, i.applicant
            FROM t_trademark_relation i
            WHERE i.id > :cursor {eid_filter}
            ORDER BY i.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "i.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_trademark_relation", record_key)
                stable_key = _text(row.get("reg_number"))
                confidence = ConfidenceLevel.HIGH if stable_key else ConfidenceLevel.MEDIUM
                trademark_id = build_stable_id("trademark", stable_key or row.get("_id") or record_key)
                store.add_node(
                    GraphNode(
                        node_id=trademark_id,
                        node_type=GraphNodeType.TRADEMARK,
                        name=stable_key or f"商标-{record_key}",
                        properties=_compact(
                            {
                                "registration_number": stable_key,
                                "type_number": _text(row.get("type_num")),
                                "applicant": _text(row.get("applicant")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.OWNS_TRADEMARK,
                        row["eid"],
                        trademark_id,
                        "t_trademark_relation",
                        record_key,
                        confidence,
                        {},
                    )
                )
            store.commit()

    async def _extract_copyrights(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """按 eid 和主键游标抽取著作权关系。"""
        query = """
            SELECT i.id, i.eid, i.obj_id, i.number, i.name, i.type_name,
                   i.success_date, i.approval_date, i.state, i.is_history
            FROM t_copyrights_relations i
            WHERE i.id > :cursor {eid_filter}
            ORDER BY i.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "i.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_copyrights_relations", record_key)
                stable_key = _text(row.get("obj_id")) or _text(row.get("number"))
                confidence = ConfidenceLevel.HIGH if stable_key else ConfidenceLevel.MEDIUM
                copyright_id = build_stable_id("copyright", stable_key or record_key)
                store.add_node(
                    GraphNode(
                        node_id=copyright_id,
                        node_type=GraphNodeType.COPYRIGHT,
                        name=_text(row.get("name")) or stable_key or f"著作权-{record_key}",
                        properties=_compact(
                            {
                                "number": _text(row.get("number")),
                                "type_name": _text(row.get("type_name")),
                                "state": _text(row.get("state")),
                                "is_history": bool(row.get("is_history")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.OWNS_COPYRIGHT,
                        row["eid"],
                        copyright_id,
                        "t_copyrights_relations",
                        record_key,
                        confidence,
                        {
                            "effective_start": _date_text(row.get("success_date") or row.get("approval_date")),
                            "is_history": bool(row.get("is_history")),
                        },
                    )
                )
            store.commit()

    async def _extract_administrative_penalties(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """抽取行政处罚事件和名称型监管机关线索。"""
        query = """
            SELECT r.id, r.eid, r.number, r.illegal_type, r.department, r.date, r.status
            FROM t_administrative_punishment r
            WHERE r.id > :cursor {eid_filter}
            ORDER BY r.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "r.eid", "id", eids, batch_size):
            self._add_punishment_rows(
                store,
                company_eids,
                rows,
                table="t_administrative_punishment",
                department_field="department",
                date_field="date",
                title_field="illegal_type",
            )
            store.commit()

    async def _extract_environmental_penalties(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """抽取环保处罚事件和名称型监管机关线索。"""
        query = """
            SELECT r._id AS id, r.eid, r.document_no, r.illegal_type,
                   r.punishment_title, r.punishment_dept, r.punishment_date
            FROM t_huanbaochufas r
            WHERE r._id > :cursor {eid_filter}
            ORDER BY r._id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "r.eid", "id", eids, batch_size, initial_cursor=""):
            self._add_punishment_rows(
                store,
                company_eids,
                rows,
                table="t_huanbaochufas",
                department_field="punishment_dept",
                date_field="punishment_date",
                title_field="punishment_title",
            )
            store.commit()

    def _add_punishment_rows(
        self,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        rows: list[dict],
        *,
        table: str,
        department_field: str,
        date_field: str,
        title_field: str,
    ) -> None:
        """映射处罚事件，并把监管机关名称关系降为低置信度。"""
        for row in rows:
            if row["eid"] not in company_eids:
                continue
            record_key = str(row["id"])
            source_id = build_source_id(table, record_key)
            punishment_id = build_stable_id("punishment", table, record_key)
            store.add_node(
                GraphNode(
                    node_id=punishment_id,
                    node_type=GraphNodeType.PUNISHMENT,
                    name=(
                        _text(row.get(title_field))
                        or _text(row.get("number"))
                        or _text(row.get("document_no"))
                        or "处罚事件"
                    ),
                    properties=_compact(
                        {
                            "document_number": _text(row.get("number")) or _text(row.get("document_no")),
                            "illegal_type": _text(row.get("illegal_type")),
                            "status": _text(row.get("status")),
                            "business_date": _date_text(row.get(date_field)),
                        }
                    ),
                    source_ids=[source_id],
                )
            )
            store.add_edge(
                _edge(
                    GraphRelationType.PUNISHED_BY,
                    row["eid"],
                    punishment_id,
                    table,
                    record_key,
                    ConfidenceLevel.HIGH,
                    {"effective_start": _date_text(row.get(date_field))},
                )
            )
            department = _text(row.get(department_field))
            if department:
                regulator_id = build_stable_id("regulator", normalize_company_name(department))
                store.add_node(
                    GraphNode(
                        node_id=regulator_id,
                        node_type=GraphNodeType.REGULATOR,
                        name=department,
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.PUNISHED_BY,
                        punishment_id,
                        regulator_id,
                        table,
                        record_key,
                        ConfidenceLevel.LOW,
                        {"effective_start": _date_text(row.get(date_field))},
                        suffix="regulator",
                    )
                )

    async def _extract_licenses(
        self,
        connection: ReadonlyConnection,
        store: ProjectionSnapshotStore,
        company_eids: set[str],
        eids: list[str] | None,
        batch_size: int,
    ) -> None:
        """抽取许可事件、有效期和名称型发证机关线索。"""
        query = """
            SELECT r.id, r.eid, r.u_id, r.number, r.license_no, r.name,
                   r.license_name, r.license_category, r.department,
                   r.source_department, r.status, r.start_date, r.end_date, r.license_date
            FROM t_license_info r
            WHERE r.id > :cursor {eid_filter}
            ORDER BY r.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "r.eid", "id", eids, batch_size):
            for row in rows:
                if row["eid"] not in company_eids:
                    continue
                record_key = str(row["id"])
                source_id = build_source_id("t_license_info", record_key)
                license_id = build_stable_id("license", row.get("u_id") or record_key)
                store.add_node(
                    GraphNode(
                        node_id=license_id,
                        node_type=GraphNodeType.LICENSE,
                        name=_text(row.get("license_name")) or _text(row.get("name")) or "行政许可",
                        properties=_compact(
                            {
                                "license_number": _text(row.get("license_no")) or _text(row.get("number")),
                                "category": _text(row.get("license_category")),
                                "status": _text(row.get("status")),
                                "effective_start": _date_text(row.get("start_date") or row.get("license_date")),
                                "effective_end": _date_text(row.get("end_date")),
                            }
                        ),
                        source_ids=[source_id],
                    )
                )
                store.add_edge(
                    _edge(
                        GraphRelationType.LICENSED_BY,
                        row["eid"],
                        license_id,
                        "t_license_info",
                        record_key,
                        ConfidenceLevel.HIGH,
                        {
                            "effective_start": _date_text(row.get("start_date") or row.get("license_date")),
                            "effective_end": _date_text(row.get("end_date")),
                        },
                    )
                )
                department = _text(row.get("source_department")) or _text(row.get("department"))
                if department:
                    regulator_id = build_stable_id("regulator", normalize_company_name(department))
                    store.add_node(
                        GraphNode(
                            node_id=regulator_id,
                            node_type=GraphNodeType.REGULATOR,
                            name=department,
                            source_ids=[source_id],
                        )
                    )
                    store.add_edge(
                        _edge(
                            GraphRelationType.LICENSED_BY,
                            license_id,
                            regulator_id,
                            "t_license_info",
                            record_key,
                            ConfidenceLevel.LOW,
                            {},
                            suffix="regulator",
                        )
                    )
            store.commit()


async def _iter_batches(
    connection: ReadonlyConnection,
    query_template: str,
    eid_expression: str,
    cursor_field: str,
    eids: list[str] | None,
    batch_size: int,
    *,
    initial_cursor: object = 0,
):
    """使用稳定主键游标和可选 eid 白名单读取有限批次。"""
    eid_filter = f"AND {eid_expression} IN :eids" if eids else ""
    statement = text(query_template.format(eid_filter=eid_filter))
    if eids:
        statement = statement.bindparams(bindparam("eids", expanding=True))
    cursor = initial_cursor
    while True:
        parameters: dict[str, object] = {"cursor": cursor, "limit": batch_size}
        if eids:
            parameters["eids"] = tuple(eids)
        result = await connection.execute(statement, parameters)
        rows = [dict(row) for row in result.mappings()]
        if not rows:
            return
        yield rows
        cursor = rows[-1][cursor_field]


def _edge(
    edge_type: GraphRelationType,
    source_node_id: str,
    target_node_id: str,
    table: str,
    record_key: str,
    confidence: ConfidenceLevel,
    properties: dict,
    *,
    suffix: str = "",
) -> GraphEdge:
    """生成带稳定 ID 和完整来源属性的图关系。"""
    source_id = build_source_id(table, record_key)
    edge_id = build_stable_id("edge", edge_type.value, table, record_key, source_node_id, target_node_id, suffix)
    return GraphEdge(
        edge_id=edge_id,
        edge_type=edge_type,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        confidence=confidence,
        properties=_compact(
            {
                **properties,
                "source_id": source_id,
                "source_table": table,
                "source_record_key": record_key,
            }
        ),
        source_ids=[source_id],
    )


def _compact(values: dict) -> dict:
    """删除 Neo4j 属性中没有业务意义的空值。"""
    return {key: _json_value(value) for key, value in values.items() if value is not None and value != ""}


def _json_value(value: object):
    """把日期和数据库标量转换为 JSON 可持久化值。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _text(value: object) -> str | None:
    """规范化可选文本。"""
    return normalize_optional_text(value)


def _date_text(value: object) -> str | None:
    """保留 ISO 日期或数据库原始日期文本。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return _text(value)


def _percentage(value: object) -> str | None:
    """规范化持股比例，异常源值保留为空。"""
    try:
        return normalize_percentage(value)
    except ValueError:
        return None


def _json_objects(value: object) -> list[dict]:
    """只接受融资来源中的 JSON 对象数组。"""
    if not value:
        return []
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


async def _report(progress: ProgressCallback | None, value: float, message: str) -> None:
    """在后台任务存在时报告粗粒度投影进度。"""
    if progress is not None:
        await progress(value, message)

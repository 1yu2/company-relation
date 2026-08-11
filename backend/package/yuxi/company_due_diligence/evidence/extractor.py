"""企业尽调文本证据只读抽取、清洗和语义分块。"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import date
from pathlib import Path

from sqlalchemy import bindparam, text

from yuxi.company_due_diligence.evidence.models import (
    EvidenceDocument,
    EvidenceDocumentType,
    EvidenceIndexRequest,
    EvidenceSnapshot,
    EvidenceSnapshotStore,
    build_chunks,
    build_content_checksum,
    build_document_id,
    create_evidence_store,
    publish_evidence_snapshot,
)
from yuxi.company_due_diligence.models import build_source_id
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL, ReadonlyConnection, company_due_diligence_mysql
from yuxi.company_due_diligence.normalization import normalize_date, normalize_optional_text

ProgressCallback = Callable[[float, str], Awaitable[None]]
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[\t\r\f\v ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；!?])")


class EvidenceExtractor:
    """按来源主键和企业范围生成可重建证据快照。"""

    def __init__(
        self,
        mysql: CompanyDueDiligenceMySQL = company_due_diligence_mysql,
        snapshot_root: Path | None = None,
    ) -> None:
        self._mysql = mysql
        self._snapshot_root = snapshot_root

    async def build_snapshot(
        self,
        request: EvidenceIndexRequest,
        *,
        embedding_model_spec: str,
        embedding_dimension: int,
        minio_bucket: str,
        progress: ProgressCallback | None = None,
    ) -> EvidenceSnapshot:
        """完成全部文档去重、分块和 checksum 后发布 SQLite 快照。"""
        store = create_evidence_store(self._snapshot_root)
        try:
            async with self._mysql.connection() as connection:
                company_names = await self._load_company_names(connection, request.eids)
                if request.eids:
                    missing = sorted(set(request.eids) - set(company_names))
                    if missing:
                        raise ValueError(f"以下 eid 不存在: {', '.join(missing[:10])}")
                await _report(progress, 8, "企业范围校验完成")

                selected = set(request.document_types)
                if EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT in selected:
                    await self._extract_administrative_penalties(connection, store, request)
                await _report(progress, 20, "行政处罚文本抽取完成")
                if EvidenceDocumentType.ENVIRONMENTAL_PUNISHMENT in selected:
                    await self._extract_environmental_penalties(connection, store, request)
                await _report(progress, 32, "环保处罚文本抽取完成")
                if EvidenceDocumentType.LICENSE in selected:
                    await self._extract_licenses(connection, store, request)
                await _report(progress, 46, "许可文本抽取完成")
                if EvidenceDocumentType.PATENT in selected:
                    await self._extract_patents(connection, store, request)
                await _report(progress, 62, "专利摘要抽取完成")
                if EvidenceDocumentType.PRODUCT in selected:
                    await self._extract_products(connection, store, request)
                await _report(progress, 74, "产品描述抽取完成")
                if EvidenceDocumentType.NEWS in selected:
                    await self._extract_news(connection, store, request, company_names)
                    await self._extract_news_interpretations(connection, store, request, company_names)
                await _report(progress, 88, "唯一企业新闻文本抽取完成")

            manifest = store.finalize(
                embedding_model_spec=embedding_model_spec,
                embedding_dimension=embedding_dimension,
                scope_eids=sorted(request.eids) if request.eids else None,
                minio_bucket=minio_bucket,
            )
            await _report(progress, 92, "证据快照 checksum 和统计完成")
            return publish_evidence_snapshot(store, manifest)
        except Exception:
            with suppress(Exception):
                store.close()
            with suppress(FileNotFoundError):
                store.path.unlink()
            raise

    async def _load_company_names(
        self,
        connection: ReadonlyConnection,
        eids: list[str] | None,
    ) -> dict[str, str]:
        """加载 eid 到唯一标准全称，新闻只允许该精确映射。"""
        eid_filter = "WHERE eid IN :eids" if eids else ""
        statement = text(f"SELECT eid, name FROM t_enterprise {eid_filter} ORDER BY eid")
        parameters = {}
        if eids:
            statement = statement.bindparams(bindparam("eids", expanding=True))
            parameters["eids"] = tuple(eids)
        result = await connection.execute(statement, parameters)
        return {row["eid"]: row["name"] for row in result.mappings()}

    async def _extract_administrative_penalties(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
    ) -> None:
        """抽取行政处罚事实、依据、决定和救济渠道。"""
        query = """
            SELECT r.id, r.eid, r.number, r.illegal_type, r.content, r.department,
                   r.date, r.punishment_text, r.based_on, r.reason, r.document,
                   r.description, r.relief_channel, r.pdf_content, r.standard_punish
            FROM t_administrative_punishment r
            WHERE r.id > :cursor {eid_filter}
            ORDER BY r.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "r.eid", "id", request.eids, request.batch_size):
            for row in rows:
                store.record_extracted()
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
                    table="t_administrative_punishment",
                    record_key=str(row["id"]),
                    title=_text(row.get("number")) or "行政处罚",
                    published_at=row.get("date"),
                    sections=(
                        ("违法类型", row.get("illegal_type")),
                        ("违法事实", row.get("content")),
                        ("处罚原因", row.get("reason")),
                        ("处罚依据", row.get("based_on")),
                        ("处罚决定", row.get("punishment_text")),
                        ("标准处罚", row.get("standard_punish")),
                        ("文书内容", row.get("document")),
                        ("补充说明", row.get("description")),
                        ("PDF 内容", row.get("pdf_content")),
                        ("救济渠道", row.get("relief_channel")),
                        ("处罚机关", row.get("department")),
                    ),
                )
            store.commit()

    async def _extract_environmental_penalties(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
    ) -> None:
        """抽取环保处罚原因、依据、内容和结果。"""
        query = """
            SELECT r._id AS id, r.eid, r.document_no, r.punishment_title,
                   r.punishments, r.punishment_date, r.illegal_type,
                   r.punishment_reason, r.punishment_basis,
                   r.punishment_content, r.punishment_result, r.punishment_dept
            FROM t_huanbaochufas r
            WHERE r._id > :cursor {eid_filter}
            ORDER BY r._id
            LIMIT :limit
        """
        async for rows in _iter_batches(
            connection,
            query,
            "r.eid",
            "id",
            request.eids,
            request.batch_size,
            initial_cursor="",
        ):
            for row in rows:
                store.record_extracted()
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.ENVIRONMENTAL_PUNISHMENT,
                    table="t_huanbaochufas",
                    record_key=str(row["id"]),
                    title=_text(row.get("punishment_title")) or _text(row.get("document_no")) or "环保处罚",
                    published_at=row.get("punishment_date"),
                    sections=(
                        ("违法类型", row.get("illegal_type")),
                        ("处罚原因", row.get("punishment_reason")),
                        ("处罚依据", row.get("punishment_basis")),
                        ("处罚内容", row.get("punishment_content")),
                        ("处罚决定", row.get("punishments")),
                        ("处罚结果", row.get("punishment_result")),
                        ("处罚机关", row.get("punishment_dept")),
                    ),
                )
            store.commit()

    async def _extract_licenses(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
    ) -> None:
        """抽取许可事项、正文、机关和有效期。"""
        query = """
            SELECT r.id, r.eid, r.number, r.license_no, r.name, r.license_name,
                   r.license_category, r.content, r.department, r.source_department,
                   r.status, r.start_date, r.end_date, r.license_date
            FROM t_license_info r
            WHERE r.id > :cursor {eid_filter}
            ORDER BY r.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "r.eid", "id", request.eids, request.batch_size):
            for row in rows:
                store.record_extracted()
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.LICENSE,
                    table="t_license_info",
                    record_key=str(row["id"]),
                    title=_text(row.get("license_name")) or _text(row.get("name")) or "行政许可",
                    published_at=row.get("license_date") or row.get("start_date"),
                    sections=(
                        ("许可编号", _text(row.get("license_no")) or _text(row.get("number"))),
                        ("许可类别", row.get("license_category")),
                        ("许可内容", row.get("content")),
                        ("状态", row.get("status")),
                        ("有效期开始", row.get("start_date")),
                        ("有效期结束", row.get("end_date")),
                        ("许可机关", _text(row.get("source_department")) or _text(row.get("department"))),
                    ),
                )
            store.commit()

    async def _extract_patents(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
    ) -> None:
        """只按 eid 和主键游标抽取非空专利摘要。"""
        query = """
            SELECT i.id, i.eid, i.patent_name, i.request_num_standard,
                   i.brief, i.category_num_ipc, i.request_date, i.authorize_date
            FROM t_patents_relations_info i
            WHERE i.id > :cursor
              AND COALESCE(TRIM(i.brief), '') <> ''
              {eid_filter}
            ORDER BY i.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "i.eid", "id", request.eids, request.batch_size):
            for row in rows:
                store.record_extracted()
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.PATENT,
                    table="t_patents_relations_info",
                    record_key=str(row["id"]),
                    title=_text(row.get("patent_name")) or _text(row.get("request_num_standard")) or "专利摘要",
                    published_at=row.get("authorize_date") or row.get("request_date"),
                    sections=(
                        ("申请号", row.get("request_num_standard")),
                        ("IPC 分类", row.get("category_num_ipc")),
                        ("摘要", row.get("brief")),
                    ),
                )
            store.commit()

    async def _extract_products(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
    ) -> None:
        """抽取非空产品描述和渠道信息。"""
        query = """
            SELECT p.id, p.eid, p.pro_name, p.kind, p.description,
                   p.domain, p.links, p.create_time
            FROM t_company_products p
            WHERE p.id > :cursor
              AND COALESCE(TRIM(p.description), '') <> ''
              {eid_filter}
            ORDER BY p.id
            LIMIT :limit
        """
        async for rows in _iter_batches(connection, query, "p.eid", "id", request.eids, request.batch_size):
            for row in rows:
                store.record_extracted()
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.PRODUCT,
                    table="t_company_products",
                    record_key=str(row["id"]),
                    title=_text(row.get("pro_name")) or "产品描述",
                    published_at=row.get("create_time"),
                    sections=(
                        ("产品类型", row.get("kind")),
                        ("产品描述", row.get("description")),
                        ("产品域名", row.get("domain")),
                        ("产品链接", row.get("links")),
                    ),
                )
            store.commit()

    async def _extract_news(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
        company_names: dict[str, str],
    ) -> None:
        """只索引能按唯一标准全称精确映射 eid 的新闻。"""
        name_to_eid = {name: eid for eid, name in company_names.items()}
        query = """
            SELECT n.id, n.company_name, n.pub_time, n.title, n.source,
                   n.url, n.sentiment, n.content_summary, n.content_raw
            FROM company_news n
            WHERE n.id > :cursor {name_filter}
            ORDER BY n.id
            LIMIT :limit
        """
        async for rows in _iter_name_batches(
            connection,
            query,
            list(name_to_eid),
            request.eids,
            request.batch_size,
        ):
            for row in rows:
                store.record_extracted()
                eid = name_to_eid.get(row["company_name"])
                if eid is None:
                    store.record_unmapped_news()
                    continue
                row["eid"] = eid
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.NEWS,
                    table="company_news",
                    record_key=str(row["id"]),
                    title=_text(row.get("title")) or "企业新闻",
                    published_at=row.get("pub_time"),
                    sections=(
                        ("新闻标题", row.get("title")),
                        ("新闻摘要", row.get("content_summary")),
                        ("新闻正文", row.get("content_raw")),
                        ("来源", row.get("source")),
                        ("情感", row.get("sentiment")),
                        ("原文链接", row.get("url")),
                    ),
                )
            store.commit()

    async def _extract_news_interpretations(
        self,
        connection: ReadonlyConnection,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
        company_names: dict[str, str],
    ) -> None:
        """把新闻解读作为独立来源文档，避免混淆来源记录。"""
        name_to_eid = {name: eid for eid, name in company_names.items()}
        query = """
            SELECT i.id, i.news_id, i.factors, i.conclusion,
                   n.company_name, n.pub_time, n.title
            FROM company_news_interpret i
            JOIN company_news n ON n.id = i.news_id
            WHERE i.id > :cursor {name_filter}
            ORDER BY i.id
            LIMIT :limit
        """
        async for rows in _iter_name_batches(
            connection,
            query,
            list(name_to_eid),
            request.eids,
            request.batch_size,
        ):
            for row in rows:
                store.record_extracted()
                eid = name_to_eid.get(row["company_name"])
                if eid is None:
                    store.record_unmapped_news()
                    continue
                row["eid"] = eid
                self._add_document(
                    store,
                    request,
                    row=row,
                    document_type=EvidenceDocumentType.NEWS,
                    table="company_news_interpret",
                    record_key=str(row["id"]),
                    title=f"新闻解读：{_text(row.get('title')) or row['news_id']}",
                    published_at=row.get("pub_time"),
                    sections=(
                        ("解读结论", row.get("conclusion")),
                        ("影响因素", _json_text(row.get("factors"))),
                    ),
                )
            store.commit()

    def _add_document(
        self,
        store: EvidenceSnapshotStore,
        request: EvidenceIndexRequest,
        *,
        row: dict,
        document_type: EvidenceDocumentType,
        table: str,
        record_key: str,
        title: str,
        published_at: object,
        sections: tuple[tuple[str, object], ...],
    ) -> None:
        """清洗单条来源并添加稳定文档和语义分块。"""
        try:
            document_text = join_sections(sections)
            if len(document_text) < 20:
                store.record_empty()
                return
            document_id = build_document_id(table, record_key, document_type)
            document = EvidenceDocument(
                document_id=document_id,
                eid=row["eid"],
                document_type=document_type,
                title=clean_text(title),
                text=document_text,
                source_id=build_source_id(table, record_key),
                source_table=table,
                source_record_key=record_key,
                published_at=_safe_date(published_at),
                checksum=build_content_checksum(document_text),
            )
            chunks = build_chunks(document_id, split_semantic_text(document_text, request.chunk_size))
            if not chunks:
                store.record_empty()
                return
            store.add_document(document, chunks)
        except (TypeError, ValueError):
            store.record_failed()


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
    """按主键游标和可选 eid 白名单流式读取文本来源。"""
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


async def _iter_name_batches(
    connection: ReadonlyConnection,
    query_template: str,
    company_names: list[str],
    scoped_eids: list[str] | None,
    batch_size: int,
):
    """按新闻主键游标读取，并在受限任务中仅取允许企业全称。"""
    name_filter = "AND n.company_name IN :company_names" if scoped_eids else ""
    statement = text(query_template.format(name_filter=name_filter))
    if scoped_eids:
        statement = statement.bindparams(bindparam("company_names", expanding=True))
    cursor = 0
    while True:
        parameters: dict[str, object] = {"cursor": cursor, "limit": batch_size}
        if scoped_eids:
            parameters["company_names"] = tuple(company_names)
        result = await connection.execute(statement, parameters)
        rows = [dict(row) for row in result.mappings()]
        if not rows:
            return
        yield rows
        cursor = rows[-1]["id"]


def clean_text(value: object) -> str:
    """清除 HTML、控制空白和明显空模板行。"""
    text_value = normalize_optional_text(value)
    if text_value is None:
        return ""
    normalized = html.unescape(_HTML_TAG.sub(" ", text_value)).replace("\u3000", " ")
    lines = []
    for raw_line in normalized.splitlines():
        line = _WHITESPACE.sub(" ", raw_line).strip()
        if line and line not in {"暂无", "无", "--", "-"}:
            lines.append(line)
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def join_sections(sections: tuple[tuple[str, object], ...]) -> str:
    """把非空字段组织为保留语义标题的完整文档。"""
    parts = []
    for heading, value in sections:
        cleaned = clean_text(value)
        if cleaned:
            parts.append(f"{heading}：{cleaned}")
    return "\n\n".join(parts)


def split_semantic_text(text_value: str, max_chars: int) -> list[str]:
    """优先按段落和句末切分，只有超长单句才按字符硬切。"""
    paragraphs = [paragraph.strip() for paragraph in text_value.split("\n\n") if paragraph.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(paragraph) if sentence.strip()]
        for sentence in sentences:
            if len(sentence) <= max_chars:
                units.append(sentence)
            else:
                units.extend(sentence[index : index + max_chars] for index in range(0, len(sentence), max_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _safe_date(value: object) -> date | None:
    """日期异常时保留文档但不伪造业务日期。"""
    try:
        return normalize_date(value)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    """规范化可选文本。"""
    return normalize_optional_text(value)


def _json_text(value: object) -> str:
    """把新闻解读 JSON 转换为稳定可读文本。"""
    if value is None or value == "":
        return ""
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return clean_text(value)
    return json.dumps(decoded, ensure_ascii=False, sort_keys=True)


async def _report(progress: ProgressCallback | None, value: float, message: str) -> None:
    """在后台任务存在时报告粗粒度抽取进度。"""
    if progress is not None:
        await progress(value, message)

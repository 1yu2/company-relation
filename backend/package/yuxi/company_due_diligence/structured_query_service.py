"""企业尽调白名单结构化查询服务。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from yuxi.company_due_diligence.models import (
    ConfidenceLevel,
    DataQuality,
    Entity,
    EntityType,
    Fact,
    LatencyBreakdown,
    Pagination,
    Relation,
    Source,
    SourceStatus,
    StructuredQueryRequest,
    StructuredQueryResult,
    StructuredQueryType,
    build_source_id,
    build_stable_id,
)
from yuxi.company_due_diligence.mysql import (
    CompanyDueDiligenceMySQL,
    MySQLUnavailableError,
    company_due_diligence_mysql,
)
from yuxi.company_due_diligence.normalization import (
    normalize_amount,
    normalize_date,
    normalize_decimal,
    normalize_optional_text,
    normalize_percentage,
)
from yuxi.company_due_diligence.repositories.enterprise import EnterpriseRepository
from yuxi.company_due_diligence.repositories.finance import FinanceRepository
from yuxi.company_due_diligence.repositories.financial_metric import FinancialMetricRepository
from yuxi.company_due_diligence.repositories.intellectual_property import IntellectualPropertyRepository
from yuxi.company_due_diligence.repositories.product import ProductRepository
from yuxi.company_due_diligence.repositories.risk import RiskRepository
from yuxi.company_due_diligence.repositories.shareholder import ShareholderRepository
from yuxi.utils import logger

_PROFILE_QUERY_TYPES = {
    StructuredQueryType.ENTERPRISE_PROFILE,
    StructuredQueryType.REGISTERED_CAPITAL,
    StructuredQueryType.LEGAL_REPRESENTATIVE,
    StructuredQueryType.BUSINESS_STATUS,
}


class StructuredRepositories:
    """集中创建一次查询所需的各领域只读 Repository。"""

    def __init__(self, connection):
        self.enterprise = EnterpriseRepository(connection)
        self.shareholder = ShareholderRepository(connection)
        self.product = ProductRepository(connection)
        self.finance = FinanceRepository(connection)
        self.risk = RiskRepository(connection)
        self.intellectual_property = IntellectualPropertyRepository(connection)
        self.financial_metric = FinancialMetricRepository(connection)


@dataclass
class QueryPayload:
    """保存结构化结果装配过程中的去重对象和质量信息。"""

    entities: dict[str, Entity] = field(default_factory=dict)
    facts: dict[str, Fact] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    stale_fields: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    partial: bool = False


class StructuredQueryService:
    """按冻结查询类型执行参数化 SQL 并映射统一契约。"""

    def __init__(
        self,
        mysql: CompanyDueDiligenceMySQL = company_due_diligence_mysql,
        repository_factory=StructuredRepositories,
    ) -> None:
        self._mysql = mysql
        self._repository_factory = repository_factory

    async def query(self, request: StructuredQueryRequest) -> StructuredQueryResult:
        """执行单个白名单查询并返回带来源和分页状态的结果。"""
        started_at = time.perf_counter()
        queried_at = datetime.now(UTC)
        payload = QueryPayload()
        rows: list[dict] = []
        coverage: dict[str, int] | None = None

        try:
            async with self._mysql.connection() as connection:
                repositories = self._repository_factory(connection)
                profile = await repositories.enterprise.get_profile(request.eid)
                if profile is None:
                    payload.warnings.append("未找到该 eid 对应的企业基本信息")
                else:
                    _add_company(payload, profile, queried_at)
                    if request.query_type in _PROFILE_QUERY_TYPES:
                        rows = [profile]
                    else:
                        rows, coverage = await self._fetch_rows(
                            repositories,
                            request,
                            _text(profile.get("credit_no")),
                        )
        except MySQLUnavailableError:
            elapsed_ms = _elapsed_ms(started_at)
            return StructuredQueryResult(
                query_type=request.query_type,
                eid=request.eid,
                data_quality=DataQuality(
                    source_status=SourceStatus.UNAVAILABLE,
                    warnings=["企业事实主库当前不可用"],
                ),
                pagination=Pagination(page=request.page, page_size=request.page_size, returned=0),
                latency=LatencyBreakdown(mysql_ms=elapsed_ms, total_ms=elapsed_ms),
            )

        has_more = len(rows) > request.page_size
        rows = rows[: request.page_size]
        if request.query_type in _PROFILE_QUERY_TYPES:
            _map_profile_query(payload, request.query_type, rows, queried_at)
        else:
            _map_domain_rows(payload, request, rows, queried_at)
        if request.query_type == StructuredQueryType.FINANCIAL_METRIC_SERIES:
            _apply_financial_coverage(payload, coverage or {})
        if request.query_type == StructuredQueryType.TRADEMARK_SUMMARY and (request.start_date or request.end_date):
            payload.warnings.append("商标关系表缺少业务日期，时间范围按本地更新时间过滤")
            payload.partial = True

        elapsed_ms = _elapsed_ms(started_at)
        logger.info(
            "企业尽调结构化查询完成: type={}, returned={}, truncated={}, mysql_ms={}",
            request.query_type.value,
            len(rows),
            has_more,
            elapsed_ms,
        )
        return StructuredQueryResult(
            query_type=request.query_type,
            eid=request.eid,
            entities=list(payload.entities.values()),
            facts=list(payload.facts.values()),
            relations=list(payload.relations.values()),
            sources=list(payload.sources.values()),
            data_quality=DataQuality(
                source_status=SourceStatus.PARTIAL if payload.partial else SourceStatus.AVAILABLE,
                missing_fields=list(dict.fromkeys(payload.missing_fields)),
                stale_fields=list(dict.fromkeys(payload.stale_fields)),
                conflicts=list(dict.fromkeys(payload.conflicts)),
                warnings=list(dict.fromkeys(payload.warnings)),
                minimum_confidence=ConfidenceLevel.HIGH if payload.facts or payload.relations else None,
            ),
            pagination=Pagination(
                page=request.page,
                page_size=request.page_size,
                returned=len(rows),
                has_more=has_more,
                truncated=has_more,
            ),
            latency=LatencyBreakdown(mysql_ms=elapsed_ms, total_ms=elapsed_ms),
        )

    async def _fetch_rows(
        self,
        repositories: StructuredRepositories,
        request: StructuredQueryRequest,
        credit_code: str | None,
    ) -> tuple[list[dict], dict[str, int] | None]:
        """按查询类型调用唯一领域 Repository，不接受任意 SQL。"""
        options = {
            "start_date": request.start_date,
            "end_date": request.end_date,
            "limit": request.page_size + 1,
            "offset": (request.page - 1) * request.page_size,
            "sort_order": request.sort_order,
        }
        query_type = request.query_type
        if query_type == StructuredQueryType.CURRENT_SHAREHOLDERS:
            return await repositories.shareholder.list_current(request.eid, **options), None
        if query_type == StructuredQueryType.HISTORICAL_SHAREHOLDERS:
            return await repositories.shareholder.list_historical(request.eid, **options), None
        if query_type == StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS:
            return await repositories.shareholder.list_contributions(request.eid, **options), None
        if query_type == StructuredQueryType.PRODUCTS:
            return await repositories.product.list_products(request.eid, **options), None
        if query_type == StructuredQueryType.FINANCING_HISTORY:
            return await repositories.finance.list_financing_history(request.eid, **options), None
        if query_type == StructuredQueryType.ADMINISTRATIVE_PENALTIES:
            return await repositories.risk.list_administrative_penalties(request.eid, **options), None
        if query_type == StructuredQueryType.ENVIRONMENTAL_PENALTIES:
            return await repositories.risk.list_environmental_penalties(request.eid, **options), None
        if query_type == StructuredQueryType.ABNORMAL_OPERATIONS:
            return await repositories.risk.list_abnormal_operations(request.eid, **options), None
        if query_type == StructuredQueryType.LICENSES:
            return await repositories.risk.list_licenses(request.eid, **options), None
        if query_type == StructuredQueryType.PATENT_SUMMARY:
            return await repositories.intellectual_property.list_patents(request.eid, **options), None
        if query_type == StructuredQueryType.TRADEMARK_SUMMARY:
            return await repositories.intellectual_property.list_trademarks(request.eid, **options), None
        if query_type == StructuredQueryType.COPYRIGHT_SUMMARY:
            return await repositories.intellectual_property.list_copyrights(request.eid, **options), None
        if query_type == StructuredQueryType.FINANCIAL_METRIC_SERIES:
            rows = await repositories.financial_metric.list_series(
                request.eid,
                credit_code=credit_code,
                indicator_name=request.indicator_name,
                **options,
            )
            coverage = await repositories.financial_metric.get_coverage(
                request.eid,
                credit_code,
                request.indicator_name,
            )
            return rows, coverage
        raise ValueError(f"未实现的结构化查询类型: {query_type}")


def _add_company(payload: QueryPayload, row: dict, queried_at: datetime) -> None:
    """把企业基本信息加入统一实体和来源集合。"""
    source_id = _add_source(payload, "t_enterprise", row["eid"], row, "start_date", queried_at)
    attributes = {
        "credit_code": _text(row.get("credit_no")),
        "stock_code": _text(row.get("stock_code")),
        "stock_abbreviation": _text(row.get("stock_abbr")),
    }
    payload.entities[row["eid"]] = Entity(
        entity_id=row["eid"],
        entity_type=EntityType.COMPANY,
        name=_text(row.get("name")) or row["eid"],
        attributes={key: value for key, value in attributes.items() if value is not None},
        source_ids=[source_id],
    )


def _map_profile_query(
    payload: QueryPayload,
    query_type: StructuredQueryType,
    rows: list[dict],
    queried_at: datetime,
) -> None:
    """按四种企业基础查询类型选择确定字段。"""
    if not rows:
        return
    row = rows[0]
    source_id = _add_source(payload, "t_enterprise", row["eid"], row, "start_date", queried_at)
    eid = row["eid"]

    if query_type in {StructuredQueryType.ENTERPRISE_PROFILE, StructuredQueryType.REGISTERED_CAPITAL}:
        registered_capital = _safe_decimal(
            row.get("regist_capi_new") if row.get("regist_capi_new") is not None else row.get("regist_capi"),
            "regist_capi",
            payload,
        )
        _add_fact(
            payload,
            eid,
            "registered_capital",
            registered_capital,
            source_id,
            display=row.get("regist_capi"),
            unit=_text(row.get("currency_unit")),
        )
        _add_fact(
            payload,
            eid,
            "actual_capital",
            _safe_decimal(row.get("actual_capi"), "actual_capi", payload),
            source_id,
            display=row.get("actual_capi"),
            unit=_text(row.get("currency_unit")),
        )

    if query_type in {StructuredQueryType.ENTERPRISE_PROFILE, StructuredQueryType.LEGAL_REPRESENTATIVE}:
        _add_fact(payload, eid, "legal_representative", _text(row.get("oper_name")), source_id)

    if query_type in {StructuredQueryType.ENTERPRISE_PROFILE, StructuredQueryType.BUSINESS_STATUS}:
        _add_fact(payload, eid, "business_status", _text(row.get("status")), source_id)
        _add_fact(
            payload,
            eid,
            "established_date",
            _date_text(row.get("start_date"), "start_date", payload),
            source_id,
            effective_date=_safe_date(row.get("start_date"), "start_date", payload),
        )

    if query_type == StructuredQueryType.ENTERPRISE_PROFILE:
        for fact_type, field_name in (
            ("credit_code", "credit_no"),
            ("stock_code", "stock_code"),
            ("business_scope", "scope"),
            ("province_code", "province_code"),
            ("district_code", "district_code"),
        ):
            _add_fact(payload, eid, fact_type, _text(row.get(field_name)), source_id)


def _map_domain_rows(
    payload: QueryPayload,
    request: StructuredQueryRequest,
    rows: list[dict],
    queried_at: datetime,
) -> None:
    """把领域查询行分派给对应的纯映射函数。"""
    query_type = request.query_type
    if query_type in {
        StructuredQueryType.CURRENT_SHAREHOLDERS,
        StructuredQueryType.HISTORICAL_SHAREHOLDERS,
        StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS,
    }:
        _map_shareholders(payload, request.eid, query_type, rows, queried_at)
    elif query_type == StructuredQueryType.PRODUCTS:
        _map_products(payload, request.eid, rows, queried_at)
    elif query_type == StructuredQueryType.FINANCING_HISTORY:
        _map_financing(payload, request.eid, rows, queried_at)
    elif query_type in {
        StructuredQueryType.ADMINISTRATIVE_PENALTIES,
        StructuredQueryType.ENVIRONMENTAL_PENALTIES,
        StructuredQueryType.ABNORMAL_OPERATIONS,
    }:
        _map_risks(payload, request.eid, query_type, rows, queried_at)
    elif query_type == StructuredQueryType.LICENSES:
        _map_licenses(payload, request.eid, rows, queried_at)
    elif query_type in {
        StructuredQueryType.PATENT_SUMMARY,
        StructuredQueryType.TRADEMARK_SUMMARY,
        StructuredQueryType.COPYRIGHT_SUMMARY,
    }:
        _map_intellectual_property(payload, request.eid, query_type, rows, queried_at)
    elif query_type == StructuredQueryType.FINANCIAL_METRIC_SERIES:
        _map_financial_metrics(payload, request.eid, rows, queried_at)


def _map_shareholders(
    payload: QueryPayload,
    eid: str,
    query_type: StructuredQueryType,
    rows: list[dict],
    queried_at: datetime,
) -> None:
    """映射股东实体、持股关系和认缴实缴事实。"""
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, "t_partners_alls", record_key, row, "start_date", queried_at)
        shareholder_name = _text(row.get("stock_name")) or "未命名股东"
        shareholder_key = _text(row.get("stock_id")) or shareholder_name
        shareholder_id = build_stable_id("shareholder", shareholder_key)
        payload.entities[shareholder_id] = Entity(
            entity_id=shareholder_id,
            entity_type=EntityType.SHAREHOLDER,
            name=shareholder_name,
            attributes={
                key: value
                for key, value in {
                    "stock_id": _text(row.get("stock_id")),
                    "stock_type": _text(row.get("stock_type")),
                }.items()
                if value is not None
            },
            source_ids=[source_id],
        )
        ratio = _safe_percentage(row.get("stock_percent"), "stock_percent", payload)
        subscribed = _safe_decimal(row.get("total_should_capi_new"), "total_should_capi_new", payload)
        paid = _safe_decimal(row.get("total_real_capi_new"), "total_real_capi_new", payload)
        relation_type = "had_shareholder" if bool(row.get("is_history")) else "has_shareholder"
        relation_id = build_stable_id("relation", relation_type, eid, shareholder_id, record_key)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=shareholder_id,
            relation_type=relation_type,
            properties={
                key: value
                for key, value in {
                    "ownership_ratio": ratio,
                    "subscribed_capital": subscribed,
                    "subscribed_currency": _text(row.get("should_currency_unit")),
                    "paid_capital": paid,
                    "paid_currency": _text(row.get("real_currency_unit")),
                }.items()
                if value is not None
            },
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=_safe_date(row.get("start_date"), "start_date", payload),
            effective_end=_safe_date(row.get("end_date"), "end_date", payload),
        )
        _add_fact(payload, shareholder_id, "ownership_ratio", ratio, source_id, display=row.get("stock_percent"))
        _add_fact(
            payload,
            shareholder_id,
            "subscribed_capital_total",
            subscribed,
            source_id,
            display=row.get("total_should_capi_new"),
            unit=_text(row.get("should_currency_unit")),
        )
        _add_fact(
            payload,
            shareholder_id,
            "paid_capital_total",
            paid,
            source_id,
            display=row.get("total_real_capi_new"),
            unit=_text(row.get("real_currency_unit")),
        )
        if query_type == StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS:
            _map_contribution_items(payload, shareholder_id, source_id, row)


def _map_contribution_items(payload: QueryPayload, shareholder_id: str, source_id: str, row: dict) -> None:
    """完整解析源 JSON 中的认缴和实缴出资项。"""
    mappings = (
        ("subscribed_contribution", "should_capi_items", "shoud_capi", "should_capi_date"),
        ("paid_contribution", "real_capi_items", "real_capi", "real_capi_date"),
    )
    for fact_type, field_name, amount_field, date_field in mappings:
        for index, item in enumerate(_parse_json_list(row.get(field_name), field_name, payload)):
            amount, currency = _safe_amount(item.get(amount_field), f"{field_name}.{amount_field}", payload)
            business_date = _safe_date(item.get(date_field), f"{field_name}.{date_field}", payload)
            value = {
                key: value
                for key, value in {
                    "amount": amount,
                    "currency": currency,
                    "invest_type": _text(item.get("invest_type")),
                    "date": business_date.isoformat() if business_date else None,
                }.items()
                if value is not None
            }
            _add_fact(
                payload,
                shareholder_id,
                fact_type,
                value,
                source_id,
                display=_text(item.get(amount_field)),
                effective_date=business_date,
                unique_key=str(index),
            )


def _map_products(payload: QueryPayload, eid: str, rows: list[dict], queried_at: datetime) -> None:
    """映射产品实体及企业产品关系。"""
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, "t_company_products", record_key, row, "create_time", queried_at)
        product_id = build_stable_id("product", row.get("proj_id") or record_key)
        payload.entities[product_id] = Entity(
            entity_id=product_id,
            entity_type=EntityType.PRODUCT,
            name=_text(row.get("pro_name")) or "未命名产品",
            attributes={
                key: value
                for key, value in {
                    "kind": _text(row.get("kind")),
                    "domain": _text(row.get("domain")),
                    "links": _text(row.get("links")),
                    "description": _text(row.get("description")),
                }.items()
                if value is not None
            },
            source_ids=[source_id],
        )
        relation_id = build_stable_id("relation", "offers_product", eid, product_id, record_key)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=product_id,
            relation_type="offers_product",
            properties={},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=_safe_date(row.get("create_time"), "create_time", payload),
        )


def _map_financing(payload: QueryPayload, eid: str, rows: list[dict], queried_at: datetime) -> None:
    """映射融资事件、金额事实和投资人类型。"""
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, "t_company_finance", record_key, row, "round_date", queried_at)
        event_id = build_stable_id("financing", record_key)
        event_date = _safe_date(row.get("round_date"), "round_date", payload)
        payload.entities[event_id] = Entity(
            entity_id=event_id,
            entity_type=EntityType.FINANCING_EVENT,
            name=_text(row.get("round")) or _text(row.get("round_type")) or "融资事件",
            attributes={
                key: value
                for key, value in {
                    "round_type": _text(row.get("round_type")),
                    "precise": _json_scalar(row.get("precise")),
                    "news_title": _text(row.get("newstitle")),
                    "news_link": _text(row.get("newslink")),
                }.items()
                if value is not None
            },
            source_ids=[source_id],
        )
        relation_id = build_stable_id("relation", "has_financing_event", eid, event_id)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=event_id,
            relation_type="has_financing_event",
            properties={},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=event_date,
        )
        for fact_type, field_name in (
            ("financing_amount", "amount"),
            ("estimated_amount", "estimated_amount"),
            ("pre_money_valuation", "pre_money"),
            ("post_money_valuation", "post_money"),
        ):
            _add_fact(
                payload,
                event_id,
                fact_type,
                _safe_decimal(row.get(field_name), field_name, payload),
                source_id,
                display=row.get(field_name),
                unit=_text(row.get("currency")),
                effective_date=event_date,
            )
        for index, investor in enumerate(_parse_json_list(row.get("investors_json"), "investors_json", payload)):
            investor_name = _text(investor.get("text"))
            if investor_name is None:
                payload.warnings.append(f"融资记录 {record_key} 的投资人 JSON 缺少 text")
                payload.partial = True
                continue
            investor_type = _text(investor.get("type"))
            investor_id = build_stable_id("investor", investor_name, investor_type or "")
            payload.entities[investor_id] = Entity(
                entity_id=investor_id,
                entity_type=EntityType.INVESTOR,
                name=investor_name,
                attributes={"investor_type": investor_type} if investor_type else {},
                source_ids=[source_id],
            )
            investor_relation_id = build_stable_id("relation", "financed_by", event_id, investor_id, index)
            payload.relations[investor_relation_id] = Relation(
                relation_id=investor_relation_id,
                source_entity_id=event_id,
                target_entity_id=investor_id,
                relation_type="financed_by",
                properties={"investor_type": investor_type} if investor_type else {},
                confidence=ConfidenceLevel.HIGH,
                source_ids=[source_id],
                effective_start=event_date,
            )


def _map_risks(
    payload: QueryPayload,
    eid: str,
    query_type: StructuredQueryType,
    rows: list[dict],
    queried_at: datetime,
) -> None:
    """映射行政处罚、环保处罚和经营异常摘要。"""
    settings = {
        StructuredQueryType.ADMINISTRATIVE_PENALTIES: (
            "t_administrative_punishment",
            "administrative_penalty",
            "administrative_penalty",
            ("number", "illegal_type", "reason", "department", "status"),
        ),
        StructuredQueryType.ENVIRONMENTAL_PENALTIES: (
            "t_huanbaochufas",
            "environmental_penalty",
            "environmental_penalty",
            (
                "document_no",
                "illegal_type",
                "punishment_title",
                "punishment_reason",
                "punishment_dept",
                "punishment_type",
            ),
        ),
        StructuredQueryType.ABNORMAL_OPERATIONS: (
            "t_abnormal_enterprises",
            "abnormal_operation",
            "abnormal_operation",
            ("judge_department", "overdue_type", "reason", "state", "is_cancel", "is_history"),
        ),
    }
    table, entity_prefix, relation_type, fields = settings[query_type]
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, table, record_key, row, "business_date", queried_at)
        risk_id = build_stable_id(entity_prefix, record_key)
        name = next((_text(row.get(field)) for field in fields if _text(row.get(field))), relation_type)
        payload.entities[risk_id] = Entity(
            entity_id=risk_id,
            entity_type=EntityType.RISK_EVENT,
            name=name,
            attributes={field: value for field in fields if (value := _json_scalar(row.get(field))) is not None},
            source_ids=[source_id],
        )
        business_date = _safe_date(row.get("business_date"), "business_date", payload)
        relation_id = build_stable_id("relation", relation_type, eid, risk_id)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=risk_id,
            relation_type=relation_type,
            properties={},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=business_date,
        )
        if row.get("punish_amnt") is not None:
            _add_fact(
                payload,
                risk_id,
                "penalty_amount",
                _safe_decimal(row.get("punish_amnt"), "punish_amnt", payload),
                source_id,
                display=row.get("punish_amnt"),
                effective_date=business_date,
            )
        if row.get("overdue_amount") is not None:
            _add_fact(
                payload,
                risk_id,
                "overdue_amount",
                _safe_decimal(row.get("overdue_amount"), "overdue_amount", payload),
                source_id,
                display=row.get("overdue_amount"),
                effective_date=business_date,
            )


def _map_licenses(payload: QueryPayload, eid: str, rows: list[dict], queried_at: datetime) -> None:
    """映射许可实体、有效期和企业许可关系。"""
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, "t_license_info", record_key, row, "business_date", queried_at)
        license_id = build_stable_id("license", row.get("u_id") or record_key)
        payload.entities[license_id] = Entity(
            entity_id=license_id,
            entity_type=EntityType.LICENSE,
            name=_text(row.get("license_name")) or _text(row.get("name")) or "行政许可",
            attributes={
                key: value
                for key, value in {
                    "license_no": _text(row.get("license_no")) or _text(row.get("number")),
                    "category": _text(row.get("license_category")),
                    "department": _text(row.get("department")),
                    "source_department": _text(row.get("source_department")),
                    "status": _text(row.get("status")),
                }.items()
                if value is not None
            },
            source_ids=[source_id],
        )
        start_date = _safe_date(row.get("start_date"), "start_date", payload)
        end_date = _safe_date(row.get("end_date"), "end_date", payload)
        relation_id = build_stable_id("relation", "has_license", eid, license_id)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=license_id,
            relation_type="has_license",
            properties={},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=start_date,
            effective_end=end_date,
        )
        _add_fact(payload, license_id, "license_status", _text(row.get("status")), source_id)


def _map_intellectual_property(
    payload: QueryPayload,
    eid: str,
    query_type: StructuredQueryType,
    rows: list[dict],
    queried_at: datetime,
) -> None:
    """映射专利、商标和著作权条目及企业归属关系。"""
    settings = {
        StructuredQueryType.PATENT_SUMMARY: (
            "t_patents_relations_info",
            "patent",
            "owns_patent",
            "patent_name",
            ("type", "type_name", "authorize_tag", "request_num_standard", "last_status", "is_history"),
        ),
        StructuredQueryType.TRADEMARK_SUMMARY: (
            "t_trademark_relation",
            "trademark",
            "owns_trademark",
            "reg_number",
            ("type_num", "applicant"),
        ),
        StructuredQueryType.COPYRIGHT_SUMMARY: (
            "t_copyrights_relations",
            "copyright",
            "owns_copyright",
            "name",
            ("type", "type_name", "number", "short_name", "version", "state", "is_history"),
        ),
    }
    table, prefix, relation_type, name_field, attributes = settings[query_type]
    for row in rows:
        record_key = str(row["id"])
        source_id = _add_source(payload, table, record_key, row, "business_date", queried_at)
        item_id = build_stable_id(prefix, row.get("_id") or row.get("obj_id") or record_key)
        payload.entities[item_id] = Entity(
            entity_id=item_id,
            entity_type=EntityType.INTELLECTUAL_PROPERTY_ITEM,
            name=_text(row.get(name_field)) or f"{prefix}-{record_key}",
            attributes={
                field_name: value
                for field_name in attributes
                if (value := _json_scalar(row.get(field_name))) is not None
            },
            source_ids=[source_id],
        )
        business_date = _safe_date(row.get("business_date"), "business_date", payload)
        relation_id = build_stable_id("relation", relation_type, eid, item_id, record_key)
        payload.relations[relation_id] = Relation(
            relation_id=relation_id,
            source_entity_id=eid,
            target_entity_id=item_id,
            relation_type=relation_type,
            properties={},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
            effective_start=business_date,
        )
        _add_fact(
            payload,
            eid,
            f"{prefix}_item",
            {"item_id": item_id, "name": payload.entities[item_id].name},
            source_id,
            effective_date=business_date,
            unique_key=record_key,
        )


def _map_financial_metrics(payload: QueryPayload, eid: str, rows: list[dict], queried_at: datetime) -> None:
    """映射跨来源财务指标序列并保留报告期展示值。"""
    for row in rows:
        table = row["source_table"]
        record_key = str(row["record_key"])
        period_date = _safe_period(row.get("reporting_period"), "reporting_period", payload)
        source_id = _add_source(
            payload,
            table,
            record_key,
            {**row, "business_date": period_date},
            "business_date",
            queried_at,
        )
        indicator_name = _text(row.get("indicator_name")) or "unknown_metric"
        _add_fact(
            payload,
            eid,
            f"financial_metric:{indicator_name}",
            _safe_number_or_text(row.get("indicator_value"), "indicator_value", payload),
            source_id,
            display=row.get("indicator_value"),
            unit=_text(row.get("indicator_unit")),
            effective_date=period_date,
            unique_key=f"{table}:{record_key}",
        )


def _apply_financial_coverage(payload: QueryPayload, coverage: dict[str, int]) -> None:
    """根据两张财务表的映射行数生成不误导的覆盖警告。"""
    company_count = int(coverage.get("company_indicator_count") or 0)
    wind_count = int(coverage.get("wind_metric_count") or 0)
    if company_count == 0:
        payload.warnings.append("company_indicator 未覆盖该企业或所选指标")
        payload.partial = True
    if wind_count == 0:
        payload.warnings.append("wind_listed_company_metrics 未覆盖该企业或所选指标")
        payload.partial = True
    if company_count == 0 and wind_count == 0:
        payload.warnings.append("财务映射不足，当前空结果不能解释为企业没有财务数据")


def _add_source(
    payload: QueryPayload,
    table: str,
    record_key: object,
    row: dict,
    business_date_field: str,
    queried_at: datetime,
) -> str:
    """创建或复用稳定来源，并规范化来源业务日期。"""
    key = str(record_key)
    source_id = build_source_id(table, key)
    if source_id not in payload.sources:
        business_date = _safe_date(row.get(business_date_field), business_date_field, payload)
        payload.sources[source_id] = Source(
            source_id=source_id,
            table=table,
            record_key=key,
            business_date=business_date,
            queried_at=queried_at,
        )
    return source_id


def _add_fact(
    payload: QueryPayload,
    subject_id: str,
    fact_type: str,
    value: Any,
    source_id: str,
    *,
    display: object = None,
    unit: str | None = None,
    effective_date: date | None = None,
    unique_key: str = "",
) -> None:
    """仅在值存在时添加带稳定 ID 的高置信度事实。"""
    if value is None or value == {}:
        payload.missing_fields.append(fact_type)
        return
    fact_id = build_stable_id("fact", subject_id, fact_type, source_id, unique_key)
    payload.facts[fact_id] = Fact(
        fact_id=fact_id,
        subject_id=subject_id,
        fact_type=fact_type,
        normalized_value=value,
        display_value=_text(display),
        unit=unit,
        effective_date=effective_date,
        confidence=ConfidenceLevel.HIGH,
        source_id=source_id,
    )


def _parse_json_list(value: object, field_name: str, payload: QueryPayload) -> list[dict]:
    """把 JSON 数组解析为字典列表，异常数据转为质量警告。"""
    if value is None or value == "":
        return []
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        payload.warnings.append(f"{field_name} 不是合法 JSON")
        payload.partial = True
        return []
    if not isinstance(decoded, list) or any(not isinstance(item, dict) for item in decoded):
        payload.warnings.append(f"{field_name} 必须是对象数组")
        payload.partial = True
        return []
    return decoded


def _safe_decimal(value: object, field_name: str, payload: QueryPayload) -> str | None:
    """规范化金额，源值异常时记录警告而不阻断其他记录。"""
    try:
        return normalize_decimal(value)
    except ValueError:
        payload.warnings.append(f"{field_name} 无法规范化为金额")
        payload.partial = True
        return None


def _safe_amount(value: object, field_name: str, payload: QueryPayload) -> tuple[str | None, str | None]:
    """规范化带倍率和币种的金额，异常值转为质量警告。"""
    try:
        return normalize_amount(value)
    except ValueError:
        payload.warnings.append(f"{field_name} 无法规范化为金额")
        payload.partial = True
        return None, None


def _safe_percentage(value: object, field_name: str, payload: QueryPayload) -> str | None:
    """规范化比例，源值异常时记录警告而不阻断其他记录。"""
    try:
        return normalize_percentage(value)
    except ValueError:
        payload.warnings.append(f"{field_name} 无法规范化为比例")
        payload.partial = True
        return None


def _safe_date(value: object, field_name: str, payload: QueryPayload) -> date | None:
    """规范化业务日期，源值异常时记录警告。"""
    try:
        return normalize_date(value)
    except ValueError:
        payload.warnings.append(f"{field_name} 无法规范化为日期")
        payload.partial = True
        return None


def _safe_period(value: object, field_name: str, payload: QueryPayload) -> date | None:
    """兼容 YYYY-MM 和完整日期两种财务报告期。"""
    text = _text(value)
    if text and len(text) == 7:
        text = f"{text}-01"
    return _safe_date(text, field_name, payload)


def _safe_number_or_text(value: object, field_name: str, payload: QueryPayload) -> str | None:
    """优先规范化财务数值，非数值标记保留原文并给出警告。"""
    text = _text(value)
    if text is None:
        return None
    try:
        return normalize_decimal(text)
    except ValueError:
        payload.warnings.append(f"{field_name} 包含非数值标记，已保留原文")
        payload.partial = True
        return text


def _date_text(value: object, field_name: str, payload: QueryPayload) -> str | None:
    """把业务日期规范化为 ISO 展示文本。"""
    normalized = _safe_date(value, field_name, payload)
    return normalized.isoformat() if normalized else None


def _text(value: object) -> str | None:
    """把可选源字段转换为去空白文本。"""
    return normalize_optional_text(value)


def _json_scalar(value: object) -> str | int | float | bool | None:
    """把数据库标量转换为 JSON 可序列化值。"""
    if value is None:
        return None
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return normalize_decimal(value)
    return str(value)


def _elapsed_ms(started_at: float) -> float:
    """返回保留三位小数的非负毫秒耗时。"""
    return round((time.perf_counter() - started_at) * 1000, 3)


structured_query_service = StructuredQueryService()

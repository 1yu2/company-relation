from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections.abc import Callable
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yuxi.company_relation.paths import get_default_snapshot_path
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


DEFAULT_COMPANIES = (
    ("000001", "平安银行"),
    ("000002", "万科A"),
    ("000063", "中兴通讯"),
    ("000100", "TCL科技"),
    ("000333", "美的集团"),
    ("000651", "格力电器"),
    ("000725", "京东方A"),
    ("000858", "五粮液"),
    ("002027", "分众传媒"),
    ("002049", "紫光国微"),
    ("002050", "三花智控"),
    ("002129", "TCL中环"),
    ("002179", "中航光电"),
    ("002230", "科大讯飞"),
    ("002241", "歌尔股份"),
    ("002311", "海大集团"),
    ("002352", "顺丰控股"),
    ("002371", "北方华创"),
    ("002415", "海康威视"),
    ("002460", "赣锋锂业"),
    ("002466", "天齐锂业"),
    ("002475", "立讯精密"),
    ("002594", "比亚迪"),
    ("002709", "天赐材料"),
    ("002812", "恩捷股份"),
    ("300014", "亿纬锂能"),
    ("300124", "汇川技术"),
    ("300274", "阳光电源"),
    ("300308", "中际旭创"),
    ("300750", "宁德时代"),
)


def parse_args() -> argparse.Namespace:
    """解析公开数据采集参数。"""
    parser = argparse.ArgumentParser(description="采集企业股权关联公开数据并生成冻结快照。")
    parser.add_argument("--report-date", default="20251231", help="十大股东报告期，格式 YYYYMMDD。")
    parser.add_argument("--announcement-start", default="20250101", help="公告开始日期，格式 YYYYMMDD。")
    parser.add_argument("--announcement-end", default="20251231", help="公告结束日期，格式 YYYYMMDD。")
    parser.add_argument("--announcements-per-company", type=int, default=10)
    parser.add_argument("--output", type=Path, default=get_default_snapshot_path())
    parser.add_argument("--request-interval", type=float, default=0.2)
    parser.add_argument("--company-limit", type=int, default=len(DEFAULT_COMPANIES))
    parser.add_argument("--min-nodes", type=int, default=500)
    parser.add_argument("--min-edges", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    """执行 AkShare 采集、标准化、派生关系和快照校验。"""
    try:
        import akshare as ak
    except ImportError as exc:
        install_hint = (
            "缺少 akshare，请使用 "
            "`uv run --with akshare python scripts/company_relation/collect_public_snapshot.py`"
        )
        raise SystemExit(install_hint) from exc

    args = parse_args()
    payload = collect_snapshot(
        ak,
        report_date=args.report_date,
        announcement_start=args.announcement_start,
        announcement_end=args.announcement_end,
        announcements_per_company=args.announcements_per_company,
        request_interval=args.request_interval,
        companies=DEFAULT_COMPANIES[: args.company_limit],
    )
    snapshot = CompanyRelationSnapshot.from_dict(payload)
    validation = snapshot.validate()
    if not validation.valid:
        raise SystemExit(f"快照校验失败: {json.dumps(validation.to_dict(), ensure_ascii=False)}")
    if validation.node_count < args.min_nodes or validation.edge_count < args.min_edges:
        raise SystemExit(
            f"快照规模不足: nodes={validation.node_count}/{args.min_nodes}, "
            f"edges={validation.edge_count}/{args.min_edges}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**validation.to_dict(), "snapshot_checksum": snapshot.checksum()}, ensure_ascii=False))


def collect_snapshot(
    ak,
    *,
    report_date: str,
    announcement_start: str,
    announcement_end: str,
    announcements_per_company: int,
    request_interval: float,
    companies: tuple[tuple[str, str], ...] = DEFAULT_COMPANIES,
) -> dict[str, Any]:
    """从 AkShare 公开接口采集并标准化企业关系快照。"""
    collected_at = datetime.now(UTC).isoformat()
    sources: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    company_holder_edges: dict[str, list[str]] = defaultdict(list)
    errors = []

    for stock_code, fallback_name in companies:
        company_id = f"company:{stock_code}"
        profile_source_id = f"akshare:stock_profile_cninfo:{stock_code}"
        profile = {}
        try:
            profile_frame = _call_with_retry(ak.stock_profile_cninfo, symbol=stock_code)
            profile = _key_value_frame(profile_frame)
        except Exception as exc:
            errors.append(f"{stock_code}:profile:{exc}")
        _sleep(request_interval)

        company_name = fallback_name
        sources[profile_source_id] = _source_record(
            source_id=profile_source_id,
            provider="AkShare / 巨潮资讯网",
            title=f"{company_name}公司概况",
            url=f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={stock_code}",
            interface="stock_profile_cninfo",
            data_date=report_date,
            collected_at=collected_at,
        )
        _upsert_node(
            nodes,
            node_id=company_id,
            node_type="Company",
            name=company_name,
            properties={
                "stock_code": stock_code,
                "listed": True,
                "disclosed_short_name": _clean_value(profile.get("A股简称") or profile.get("公司简称")),
                "industry": _clean_value(profile.get("所属行业")),
                "registered_address": _clean_value(profile.get("注册地址")),
                "listing_date": _normalize_date(profile.get("上市日期")),
                "legal_representative": _clean_value(profile.get("法人代表")),
            },
            source_ids=[profile_source_id],
        )

        industry_name = _clean_value(profile.get("所属行业"))
        if industry_name:
            industry_id = f"industry:{_stable_token(industry_name)}"
            _upsert_node(
                nodes,
                node_id=industry_id,
                node_type="Industry",
                name=industry_name,
                properties={},
                source_ids=[profile_source_id],
            )
            _upsert_edge(
                edges,
                edge_id=f"belongs:{stock_code}:{_stable_token(industry_name)}",
                edge_type="BELONGS_TO",
                source=company_id,
                target=industry_id,
                properties={},
                source_ids=[profile_source_id],
            )

        legal_representative = _clean_value(profile.get("法人代表"))
        if legal_representative:
            person_id = f"person:{_stable_token(legal_representative)}"
            _upsert_node(
                nodes,
                node_id=person_id,
                node_type="Person",
                name=legal_representative,
                properties={},
                source_ids=[profile_source_id],
            )
            _upsert_edge(
                edges,
                edge_id=f"executive:{person_id}:{stock_code}:legal_representative",
                edge_type="EXECUTIVE_OF",
                source=person_id,
                target=company_id,
                properties={"role": "法定代表人"},
                source_ids=[profile_source_id],
            )

        market_symbol = f"sz{stock_code}" if stock_code.startswith(("0", "3")) else f"sh{stock_code}"
        holder_interfaces = (
            ("stock_gdfx_top_10_em", ak.stock_gdfx_top_10_em, "十大股东"),
            ("stock_gdfx_free_top_10_em", ak.stock_gdfx_free_top_10_em, "十大流通股东"),
        )
        for interface_name, interface, holder_scope in holder_interfaces:
            source_id = f"akshare:{interface_name}:{stock_code}:{report_date}"
            try:
                holder_frame = _call_with_retry(interface, symbol=market_symbol, date=report_date)
                holder_rows = _frame_records(holder_frame)
            except Exception as exc:
                errors.append(f"{stock_code}:{interface_name}:{exc}")
                holder_rows = []
            _sleep(request_interval)

            sources[source_id] = _source_record(
                source_id=source_id,
                provider="AkShare / 东方财富",
                title=f"{company_name}{report_date}{holder_scope}",
                url=f"https://data.eastmoney.com/gdfx/stock/{stock_code}.html",
                interface=interface_name,
                data_date=_display_date(report_date),
                collected_at=collected_at,
            )
            for row in holder_rows:
                holder_name = _pick_value(row, "股东名称", "股东", "机构或基金名称")
                if not holder_name:
                    continue
                holder_type = "Company" if _looks_like_organization(holder_name) else "Person"
                holder_id = (
                    f"company:external:{_stable_token(holder_name)}"
                    if holder_type == "Company"
                    else f"person:{_stable_token(holder_name)}"
                )
                _upsert_node(
                    nodes,
                    node_id=holder_id,
                    node_type=holder_type,
                    name=holder_name,
                    properties={"listed": False} if holder_type == "Company" else {},
                    source_ids=[source_id],
                )
                edge_id = f"holds:{_stable_token(holder_name)}:{stock_code}:{report_date}"
                _upsert_edge(
                    edges,
                    edge_id=edge_id,
                    edge_type="HOLDS",
                    source=holder_id,
                    target=company_id,
                    properties={
                        "share_ratio": _parse_ratio(
                            _pick_value(
                                row,
                                "占总股本持股比例",
                                "占总流通股本持股比例",
                                "占流通股比例",
                                "持股比例",
                                "持股比例(%)",
                            )
                        ),
                        "share_count": _clean_value(_pick_value(row, "持股数", "期末持股-数量")),
                        "scopes": [holder_scope],
                        "report_date": _display_date(report_date),
                    },
                    source_ids=[source_id],
                )
                if edge_id not in company_holder_edges[holder_id]:
                    company_holder_edges[holder_id].append(edge_id)

        announcement_source_id = f"akshare:stock_zh_a_disclosure_report_cninfo:{stock_code}"
        try:
            announcement_frame = _call_with_retry(
                ak.stock_zh_a_disclosure_report_cninfo,
                symbol=stock_code,
                market="沪深京",
                keyword="",
                category="",
                start_date=announcement_start,
                end_date=announcement_end,
            )
            announcement_rows = _frame_records(announcement_frame)[:announcements_per_company]
        except Exception as exc:
            errors.append(f"{stock_code}:announcements:{exc}")
            announcement_rows = []
        _sleep(request_interval)

        for row_index, row in enumerate(announcement_rows, start=1):
            title = _pick_value(row, "公告标题", "公告名称", "标题")
            if not title:
                continue
            published_at = _normalize_date(_pick_value(row, "公告时间", "公告日期", "发布时间"))
            announcement_url = _pick_value(row, "公告链接", "下载网址", "网址") or ""
            announcement_id = f"announcement:{stock_code}:{published_at or row_index}:{_stable_token(title)}"
            source_id = f"{announcement_source_id}:{row_index}"
            sources[source_id] = _source_record(
                source_id=source_id,
                provider="AkShare / 巨潮资讯网",
                title=title,
                url=announcement_url,
                interface="stock_zh_a_disclosure_report_cninfo",
                data_date=published_at,
                collected_at=collected_at,
            )
            _upsert_node(
                nodes,
                node_id=announcement_id,
                node_type="Announcement",
                name=title,
                properties={
                    "published_at": published_at,
                    "announcement_type": _pick_value(row, "公告类型", "分类") or "公开公告",
                    "pdf_url": announcement_url,
                },
                source_ids=[source_id],
            )
            _upsert_edge(
                edges,
                edge_id=f"released:{stock_code}:{_stable_token(announcement_id)}",
                edge_type="RELEASED",
                source=company_id,
                target=announcement_id,
                properties={},
                source_ids=[source_id],
            )

    _derive_related_edges(nodes, edges, company_holder_edges)
    if errors:
        print(json.dumps({"collection_warnings": errors}, ensure_ascii=False))

    return {
        "dataset_id": f"company_relation_public_{report_date}",
        "name": "企业股权关联公开披露冻结快照",
        "as_of_date": _display_date(report_date),
        "collected_at": collected_at,
        "collection": {
            "company_count": len(companies),
            "report_date": report_date,
            "announcement_range": [announcement_start, announcement_end],
            "warnings": errors,
        },
        "sources": sorted(sources.values(), key=lambda item: item["source_id"]),
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges.values(), key=lambda item: item["id"]),
    }


def _derive_related_edges(
    nodes: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    company_holder_edges: dict[str, list[str]],
) -> None:
    """根据共同股东派生公司间 RELATED_TO 关系。"""
    for holder_id, holder_edge_ids in company_holder_edges.items():
        holder_edges = [edges[edge_id] for edge_id in holder_edge_ids]
        company_ids = sorted({edge["target"] for edge in holder_edges})
        if len(company_ids) < 2:
            continue
        for first_index, first_company_id in enumerate(company_ids):
            for second_company_id in company_ids[first_index + 1 :]:
                relevant_edges = [
                    edge
                    for edge in holder_edges
                    if edge["target"] in {first_company_id, second_company_id}
                ]
                source_ids = sorted({source_id for edge in relevant_edges for source_id in edge["source_ids"]})
                holder_name = nodes[holder_id]["name"]
                edge_id = (
                    f"related:{_stable_token(first_company_id)}:{_stable_token(second_company_id)}:"
                    f"{_stable_token(holder_id)}"
                )
                _upsert_edge(
                    edges,
                    edge_id=edge_id,
                    edge_type="RELATED_TO",
                    source=first_company_id,
                    target=second_company_id,
                    properties={
                        "reason": "共同股东",
                        "shared_holder_id": holder_id,
                        "shared_holder_name": holder_name,
                        "path_edge_ids": [edge["id"] for edge in relevant_edges],
                    },
                    source_ids=source_ids,
                )


def _call_with_retry(function: Callable, **kwargs):
    last_error = None
    for attempt in range(3):
        try:
            return function(**kwargs)
        except Exception as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise last_error


def _key_value_frame(frame) -> dict[str, Any]:
    records = _frame_records(frame)
    if len(records) == 1:
        return records[0]
    values = {}
    for row in records:
        row_values = list(row.values())
        if len(row_values) >= 2:
            values[str(row_values[0]).strip()] = row_values[1]
    return values


def _frame_records(frame) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return [
        {str(key): _clean_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _source_record(
    *,
    source_id: str,
    provider: str,
    title: str,
    url: str,
    interface: str,
    data_date: str | None,
    collected_at: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provider": provider,
        "title": title,
        "url": url,
        "interface": interface,
        "data_date": data_date,
        "collected_at": collected_at,
    }


def _upsert_node(
    nodes: dict[str, dict[str, Any]],
    *,
    node_id: str,
    node_type: str,
    name: str,
    properties: dict[str, Any],
    source_ids: list[str],
) -> None:
    existing = nodes.get(node_id)
    if existing is None:
        nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "name": name,
            "properties": {key: value for key, value in properties.items() if value not in (None, "")},
            "source_ids": sorted(set(source_ids)),
        }
        return
    existing["source_ids"] = sorted(set(existing["source_ids"]) | set(source_ids))
    existing["properties"].update(
        {
            key: value
            for key, value in properties.items()
            if value not in (None, "") and key not in existing["properties"]
        }
    )


def _upsert_edge(
    edges: dict[str, dict[str, Any]],
    *,
    edge_id: str,
    edge_type: str,
    source: str,
    target: str,
    properties: dict[str, Any],
    source_ids: list[str],
) -> None:
    existing = edges.get(edge_id)
    if existing is not None:
        existing["source_ids"] = sorted(set(existing["source_ids"]) | set(source_ids))
        if edge_type == "HOLDS":
            existing_scopes = set(existing["properties"].get("scopes") or [])
            existing_scopes.update(properties.get("scopes") or [])
            existing["properties"]["scopes"] = sorted(existing_scopes)
            for key, value in properties.items():
                if key == "scopes" or value in (None, ""):
                    continue
                if key not in existing["properties"] or existing["properties"][key] in (None, ""):
                    existing["properties"][key] = value
        return
    edges[edge_id] = {
        "id": edge_id,
        "type": edge_type,
        "source": source,
        "target": target,
        "properties": {key: value for key, value in properties.items() if value not in (None, "")},
        "source_ids": sorted(set(source_ids)),
    }


def _pick_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _looks_like_organization(name: str) -> bool:
    organization_keywords = ("公司", "集团", "基金", "银行", "证券", "资产", "信托", "委员会", "社保", "结算")
    return any(keyword in name for keyword in organization_keywords)


def _parse_ratio(value: Any) -> float | None:
    cleaned = _clean_value(value)
    if cleaned in (None, ""):
        return None
    try:
        return round(float(str(cleaned).replace("%", "")), 6)
    except ValueError:
        return None


def _normalize_date(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned in (None, ""):
        return None
    digits = "".join(character for character in str(cleaned) if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(cleaned)


def _display_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _stable_token(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


if __name__ == "__main__":
    main()

"""企业尽调 JSON 与 Markdown 报告渲染及 checksum。"""

from __future__ import annotations

import hashlib
import json

from yuxi.company_due_diligence.report.models import DueDiligenceReport


def render_report_json(report: DueDiligenceReport) -> bytes:
    """渲染不包含自引用产物元数据的稳定 JSON 报告。"""
    payload = report.model_dump(mode="json", exclude={"artifacts"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()


def render_report_markdown(report: DueDiligenceReport) -> bytes:
    """只引用 JSON 数据包中的章节答案和来源生成 Markdown。"""
    company_sources = " ".join(f"[{source_id}]" for source_id in report.company.source_ids)
    lines = [
        f"# {report.company.name} 企业尽调报告 {company_sources}",
        "",
        f"- 报告 ID：`{report.report_id}`",
        f"- 报告状态：`{report.status.value}`",
        f"- 代码版本：`{report.versions.code_version}`",
        f"- 图谱版本：`{report.versions.graph_version or '未使用/不可用'}`",
        f"- 证据版本：`{report.versions.evidence_version or '未使用/不可用'}`",
        f"- 数据时点：`{report.data_as_of.isoformat() if report.data_as_of else '未提供'}`",
        "",
    ]
    for section in report.request.sections:
        result = report.sections[section.value]
        lines.extend(
            [
                f"## {result.title}",
                "",
                f"章节状态：`{result.status.value}`",
                "",
                result.summary,
                "",
            ]
        )

    lines.extend(["## 数据质量声明", ""])
    lines.append(f"- 来源状态：`{report.data_quality.source_status.value}`")
    lines.append(f"- Claim Ledger：已校验 {len(report.claim_ledger.claims)} 条结果声明")
    for warning in report.data_quality.warnings:
        lines.append(f"- 警告：{warning}")
    for conflict in report.data_quality.conflicts:
        lines.append(f"- 冲突：{conflict}")
    for stale_field in report.data_quality.stale_fields:
        lines.append(f"- 过期字段：{stale_field}")

    lines.extend(["", "## 来源索引", "", "| source_id | 表 | 记录键 | 业务日期 |", "| --- | --- | --- | --- |"])
    for source in report.sources:
        business_date = source.business_date.isoformat() if source.business_date else ""
        lines.append(f"| `{source.source_id}` | `{source.table}` | `{source.record_key}` | {business_date} |")
    lines.append("")
    return "\n".join(lines).encode()


def content_checksum(content: bytes) -> str:
    """返回报告文件的 SHA256 checksum。"""
    return hashlib.sha256(content).hexdigest()

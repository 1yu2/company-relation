"""生成企业尽调自动回归与人工阻塞项发布门禁报告。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from yuxi.agents.toolkits import get_extra_metadata
from yuxi.company_due_diligence.evaluation.dataset import (
    DatasetSourceKind,
    EvaluationDataset,
    EvaluationDatasetRepository,
)
from yuxi.company_due_diligence.evaluation.metrics import AggregateMetric, QualityMetrics
from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationRunner
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL, company_due_diligence_mysql
from yuxi.company_due_diligence.performance.models import PerformanceRunRequest, PerformanceScenario
from yuxi.company_due_diligence.performance.runner import PerformanceEvaluationRunner

ONLINE_LLM_LIMITATION = "当前报告工作流不调用在线 LLM，report_with_llm 明确标记 unavailable"


async def main() -> None:
    """运行低风险质量/性能基线并合并 AkShare 与人工验收状态。"""
    args = _parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    run_id = f"company-dd-release-{created_at.strftime('%Y%m%dT%H%M%SZ')}"

    datasets = EvaluationDatasetRepository()
    quality_runner = QualityEvaluationRunner()
    quality_dataset = datasets.get(args.quality_dataset)
    quality = await quality_runner.run(
        quality_dataset,
        run_id=f"{run_id}-quality",
        strategy_label="release-smoke",
    )
    human_quality_dataset = _select_human_quality_dataset(datasets, args.human_quality_dataset)
    human_quality = None
    if human_quality_dataset is not None:
        human_quality = await quality_runner.run(
            human_quality_dataset,
            run_id=f"{run_id}-human-quality",
            strategy_label="release-human-golden",
        )
    human_quality_gate = _human_quality_gate(human_quality_dataset, human_quality)

    mysql = CompanyDueDiligenceMySQL()
    try:
        async with mysql.connection() as connection:
            eid = (await connection.execute(text("SELECT eid FROM t_enterprise ORDER BY eid LIMIT 1"))).scalar_one()
        performance = await PerformanceEvaluationRunner(mysql=mysql).run(
            PerformanceRunRequest(
                scenarios=[PerformanceScenario.ENTERPRISE_PROFILE],
                company_refs=[{"eid": eid}],
                concurrency=1,
                request_count=3,
                duration_seconds=15,
                request_timeout_seconds=10,
            ),
            run_id=f"{run_id}-performance",
        )
    finally:
        await mysql.close()

    akshare_path = args.akshare_report or _latest_akshare_report()
    akshare = json.loads(akshare_path.read_text(encoding="utf-8"))
    akshare_passed = bool(akshare.get("regression_gate", {}).get("passed"))
    admin_credentials_configured = bool(
        (os.getenv("E2E_USERNAME") or os.getenv("TEST_USERNAME"))
        and (os.getenv("E2E_PASSWORD") or os.getenv("TEST_PASSWORD"))
    )
    agent_entry_gate = _agent_entry_gate(quality, args.admin_e2e_passed)
    investigation_quality_gate = _investigation_quality_gate(quality.all_metrics)
    automated_passed = bool(
        akshare_passed
        and quality.all_metrics.strict_pass_rate.rate == 1
        and quality.all_metrics.source_integrity.rate == 1
        and agent_entry_gate["passed"]
        and investigation_quality_gate["passed"]
        and performance.regression_gate.passed
        and not performance.pool.leak_detected
    )
    human_golden_ready = human_quality_gate["eligible"] and human_quality_gate["passed"]
    release_ready = automated_passed and human_golden_ready and args.admin_e2e_passed

    report = {
        "run_id": run_id,
        "created_at": created_at.isoformat(),
        "status": "ready" if release_ready else "blocked",
        "release_ready": release_ready,
        "automated_gate_passed": automated_passed,
        "checks": {
            "akshare_public_baseline": {
                "passed": akshare_passed,
                "run_id": akshare.get("run", {}).get("run_id"),
                "snapshot_checksum": akshare.get("inputs", {}).get("snapshot_checksum"),
                "question_checksum": akshare.get("inputs", {}).get("question_checksum"),
            },
            "guangdong_quality_smoke": {
                "passed": quality.all_metrics.strict_pass_rate.rate == 1,
                "dataset_id": quality.dataset_id,
                "dataset_checksum": quality.dataset_checksum,
                "configuration_checksum": quality.configuration_checksum,
            },
            "company_investigation_agent_entry": agent_entry_gate,
            "investigation_quality": investigation_quality_gate,
            "human_verified_quality_gate": human_quality_gate,
            "safe_performance": {
                "passed": performance.regression_gate.passed,
                "code_version": performance.code_version,
                "strategy_version": performance.strategy_version,
                "data_versions": performance.data_versions,
                "scenario": performance.scenarios[0].model_dump(mode="json", exclude={"requests"}),
                "pool": performance.pool.model_dump(mode="json"),
            },
            "admin_http_e2e": {
                "credentials_configured": admin_credentials_configured,
                "passed": args.admin_e2e_passed,
            },
            "online_llm_report": {
                "handled": True,
                "required_for_release": False,
                "status": "unavailable",
                "reason": ONLINE_LLM_LIMITATION,
            },
        },
        "blockers": _blockers(
            human_quality_gate,
            args.admin_e2e_passed,
            automated_passed=automated_passed,
        ),
        "limitations": [ONLINE_LLM_LIMITATION],
    }
    json_path = output_dir / f"{run_id}.json"
    markdown_path = output_dir / f"{run_id}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    output = {"report": str(json_path), "markdown": str(markdown_path), **report}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.enforce and not release_ready:
        raise SystemExit(2)


def _parse_args() -> argparse.Namespace:
    """解析门禁输出、基线和严格执行参数。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-dataset", default="guangdong-development-smoke-v1")
    parser.add_argument("--human-quality-dataset", default=os.getenv("COMPANY_DD_HUMAN_QUALITY_DATASET"))
    parser.add_argument("--akshare-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("saves/company_due_diligence/release"))
    parser.add_argument("--admin-e2e-passed", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    return parser.parse_args()


def _latest_akshare_report() -> Path:
    """返回最新 AkShare 企业关系回归 JSON。"""
    reports = sorted(Path("data/company_relation/results").glob("company-relation-*.json"))
    if not reports:
        raise FileNotFoundError("未找到 AkShare 企业关系回归报告")
    return reports[-1]


def _select_human_quality_dataset(
    repository: EvaluationDatasetRepository,
    requested_id: str | None,
) -> EvaluationDataset | None:
    """选择显式指定或最新冻结的广东人工 Golden 数据集。"""
    if requested_id:
        dataset = repository.get(requested_id)
        if not _is_frozen_human_dataset(dataset):
            raise ValueError("正式质量数据集必须是含 human_verified 样本且已冻结 checksum 的广东企业数据集")
        return dataset

    candidates = [dataset for dataset in repository.list() if _is_frozen_human_dataset(dataset)]
    return max(candidates, key=lambda dataset: (dataset.created_at, dataset.dataset_id), default=None)


def _is_frozen_human_dataset(dataset: EvaluationDataset) -> bool:
    """判断数据集是否具备进入正式人工门禁的最低条件。"""
    return (
        dataset.source_kind == DatasetSourceKind.GUANGDONG_ENTERPRISE
        and dataset.declared_checksum is not None
        and dataset.human_review_rate > 0
    )


def _human_quality_gate(dataset: EvaluationDataset | None, quality) -> dict:
    """返回正式人工 Golden 的冻结信息和严格门禁结果。"""
    if dataset is None or quality is None:
        return {
            "configured": False,
            "eligible": False,
            "passed": False,
            "reasons": ["没有含 human_verified 样本且已冻结 checksum 的广东企业数据集"],
        }
    return {
        "configured": True,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "dataset_checksum": quality.dataset_checksum,
        "declared_checksum": dataset.declared_checksum,
        "configuration_checksum": quality.configuration_checksum,
        "human_review_rate": dataset.human_review_rate,
        "eligible": quality.regression_gate.eligible,
        "passed": quality.regression_gate.passed,
        "reasons": quality.regression_gate.reasons,
    }


def _agent_entry_gate(quality, admin_e2e_passed: bool) -> dict:
    """检查 REST 工作台与系统主智能体是否复用统一领域服务。"""
    tool_metadata = get_extra_metadata("investigate_company")
    investigation_samples = [sample for sample in quality.samples if sample.sample_type == "investigation"]
    quality_passed = bool(
        investigation_samples
        and all(sample.metrics is not None and sample.metrics.strict_passed for sample in investigation_samples)
    )
    tool_registered = bool(tool_metadata and tool_metadata.category == "company_due_diligence")
    return {
        "passed": tool_registered and quality_passed and admin_e2e_passed,
        "rest_endpoint": "/api/company-due-diligence/investigations",
        "main_agent_tool": "investigate_company",
        "shared_domain_service": "CompanyDueDiligenceAgent.investigate",
        "tool_registered": tool_registered,
        "quality_sample_count": len(investigation_samples),
        "quality_samples_passed": quality_passed,
        "admin_http_e2e_passed": admin_e2e_passed,
    }


def _investigation_quality_gate(metrics: QualityMetrics) -> dict:
    """汇总领域智能体意图、工具、展示、Claim 和一致性指标。"""
    checks = {
        "intent": {
            "entity_resolution_accuracy": _metric_gate(metrics.entity_resolution_accuracy),
            "topic_accuracy": _metric_gate(metrics.topic_accuracy),
            "time_scope_accuracy": _metric_gate(metrics.time_scope_accuracy),
            "depth_accuracy": _metric_gate(metrics.depth_accuracy),
            "delivery_accuracy": _metric_gate(metrics.delivery_accuracy),
        },
        "tools": {
            "required_tool_recall": _metric_gate(metrics.tool_recall),
            "forbidden_tool_avoidance": _metric_gate(metrics.forbidden_tool_avoidance),
        },
        "presentation": {
            "presentation_accuracy": _metric_gate(metrics.presentation_accuracy),
        },
        "claims": {
            "claim_ledger_consistency": _metric_gate(metrics.claim_ledger_consistency),
            "source_integrity": _metric_gate(metrics.source_integrity),
        },
        "input_output_consistency": {
            "topic_coverage": _metric_gate(metrics.topic_coverage),
            "irrelevant_topic_avoidance": _metric_gate(metrics.irrelevant_topic_avoidance),
        },
    }
    return {
        "passed": all(metric["passed"] for group in checks.values() for metric in group.values()),
        **checks,
    }


def _metric_gate(metric: AggregateMetric) -> dict:
    """把聚合指标转换为要求有样本且满分的发布检查。"""
    return {
        **metric.model_dump(mode="json"),
        "passed": metric.total > 0 and metric.rate == 1,
    }


def _blockers(
    human_quality_gate: dict,
    admin_e2e_passed: bool,
    *,
    automated_passed: bool = True,
) -> list[str]:
    """返回不得被自动化结果掩盖的发布阻塞项。"""
    blockers = []
    if not automated_passed:
        blockers.append("企业尽调自动回归、智能体入口或性能门禁未通过")
    if not human_quality_gate["configured"]:
        blockers.append("尚无 human_verified 广东企业 Golden，不能冻结正式质量阈值")
    elif not human_quality_gate["eligible"]:
        blockers.append("human_verified 广东企业 Golden 不具备严格门禁资格")
    elif not human_quality_gate["passed"]:
        reasons = "；".join(human_quality_gate["reasons"])
        blockers.append(f"human_verified 广东企业 Golden 未通过正式质量阈值：{reasons}")
    if not admin_e2e_passed:
        blockers.append("管理员部署态 HTTP E2E 尚未通过")
    return blockers


def _render_markdown(report: dict) -> str:
    """渲染发布状态、自动门禁和阻塞项摘要。"""
    checks = report["checks"]
    scenario = checks["safe_performance"]["scenario"]
    human_gate = checks["human_verified_quality_gate"]
    human_gate_status = "通过" if human_gate["passed"] else "未通过"
    if not human_gate["configured"]:
        human_gate_status = "未配置"
    lines = [
        "# 企业尽调发布验收报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 自动门禁：{'通过' if report['automated_gate_passed'] else '未通过'}",
        f"- 发布状态：`{report['status']}`",
        "",
        "## 自动检查",
        "",
        f"- AkShare 公开基线：{'通过' if checks['akshare_public_baseline']['passed'] else '失败'}",
        f"- 广东质量烟测：{'通过' if checks['guangdong_quality_smoke']['passed'] else '失败'}",
        f"- 统一智能体入口：{'通过' if checks['company_investigation_agent_entry']['passed'] else '失败'}",
        f"- 意图与输入输出一致性：{'通过' if checks['investigation_quality']['passed'] else '失败'}",
        f"- 人工 Golden 门禁：{human_gate_status}",
        f"- 安全性能基线：{'通过' if checks['safe_performance']['passed'] else '失败'}",
        f"- 企业基本信息 P95：{scenario['latency']['p95_ms']} ms",
        f"- 连接池泄漏：{checks['safe_performance']['pool']['leak_detected']}",
        f"- 在线 LLM 报告：{checks['online_llm_report']['status']}（非发布阻塞）",
        "",
        "## 发布阻塞项",
        "",
    ]
    lines.extend(f"- {item}" for item in report["blockers"] or ["无"])
    lines.extend(["", "## 当前限制", ""])
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    lines.append("")
    return "\n".join(lines)


async def _run_with_cleanup() -> None:
    """运行发布门禁，并在进程退出前释放共享 MySQL 连接池。"""
    try:
        await main()
    finally:
        await company_due_diligence_mysql.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_with_cleanup())

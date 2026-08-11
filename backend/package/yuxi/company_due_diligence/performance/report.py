"""企业尽调性能评测 JSON 与 Markdown 导出。"""

from __future__ import annotations

import hashlib
import json

from yuxi.company_due_diligence.performance.models import PerformanceEvaluationReport


def render_performance_json(report: PerformanceEvaluationReport) -> bytes:
    """渲染不包含 artifact 自引用的稳定 JSON。"""
    payload = report.model_dump(mode="json", exclude={"artifacts"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()


def render_performance_markdown(report: PerformanceEvaluationReport) -> bytes:
    """渲染运行参数、场景指标、连接池和门禁摘要。"""
    lines = [
        "# 企业尽调性能评测报告",
        "",
        f"- 运行 ID：`{report.run_id}`",
        f"- 安全模式：`{report.request.safety_mode.value}`",
        f"- 并发：{report.request.concurrency}",
        f"- 请求上限：{report.request.request_count}",
        f"- 持续时间上限：{report.request.duration_seconds} 秒",
        f"- 代码版本：`{report.code_version}`",
        f"- 策略版本：`{report.strategy_version}`",
        f"- 门禁：{'通过' if report.regression_gate.passed else '未通过'}",
        "",
        "## 场景结果",
        "",
        "| 场景 | 状态 | 请求 | P50 | P95 | P99 | 最大 | 吞吐 | 错误率 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in report.scenarios:
        lines.append(
            "| "
            f"{scenario.scenario.value} | {scenario.status.value} | {scenario.total_requests} | "
            f"{_milliseconds(scenario.latency.p50_ms)} | {_milliseconds(scenario.latency.p95_ms)} | "
            f"{_milliseconds(scenario.latency.p99_ms)} | {_milliseconds(scenario.latency.max_ms)} | "
            f"{scenario.throughput_rps:.2f} req/s | {scenario.error_rate:.2%} |"
        )
        if scenario.reason:
            lines.append(f"\n> {scenario.scenario.value}：{scenario.reason}\n")
    lines.extend(
        [
            "",
            "## 连接池",
            "",
            f"- 等待增量：{report.pool.wait_ms_delta:.3f} ms",
            f"- 超时增量：{report.pool.timeout_delta}",
            f"- 结束签出数：{report.pool.after.checked_out}",
            f"- 结束溢出数：{report.pool.after.overflow}",
            "",
            "## 门禁说明",
            "",
        ]
    )
    lines.extend(f"- {reason}" for reason in report.regression_gate.reasons)
    if not report.regression_gate.reasons:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines).encode()


def checksum(content: bytes) -> str:
    """返回导出文件 SHA256。"""
    return hashlib.sha256(content).hexdigest()


def _milliseconds(value: float | None) -> str:
    """格式化可能不适用的毫秒值。"""
    return "-" if value is None else f"{value:.2f} ms"

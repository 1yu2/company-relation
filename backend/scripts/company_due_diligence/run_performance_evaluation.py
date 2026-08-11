"""在已启动容器内运行企业尽调低负载或受控性能评测。"""

from __future__ import annotations

import argparse
import asyncio

from yuxi.company_due_diligence.performance.models import (
    REAL_DATABASE_CONFIRMATION,
    PerformanceRunRequest,
    PerformanceSafetyMode,
    PerformanceScenario,
)
from yuxi.company_due_diligence.performance.runner import PerformanceEvaluationRunner


async def main() -> None:
    """解析有限 CLI 参数并输出 JSON 性能报告。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("eid", nargs="+")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[item.value for item in PerformanceScenario],
        default=[PerformanceScenario.ENTERPRISE_PROFILE.value],
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-count", type=int, default=5)
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--controlled-real-database", action="store_true")
    parser.add_argument("--confirm", help=f"受控真实库模式必须传入 {REAL_DATABASE_CONFIRMATION}")
    args = parser.parse_args()
    request = PerformanceRunRequest(
        scenarios=args.scenario,
        company_refs=[{"eid": eid} for eid in args.eid],
        safety_mode=(
            PerformanceSafetyMode.CONTROLLED_REAL_DATABASE
            if args.controlled_real_database
            else PerformanceSafetyMode.SAFE_REGRESSION
        ),
        concurrency=args.concurrency,
        request_count=args.request_count,
        duration_seconds=args.duration_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        real_database_confirmation=args.confirm,
    )
    report = await PerformanceEvaluationRunner().run(request, run_id="cli-performance-evaluation")
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())

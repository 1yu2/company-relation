"""企业尽调性能评测安全参数单元测试。"""

import pytest
from pydantic import ValidationError

from yuxi.company_due_diligence.performance.models import (
    REAL_DATABASE_CONFIRMATION,
    PerformanceRunRequest,
    PerformanceSafetyMode,
    PerformanceScenario,
)


def _request(**overrides) -> PerformanceRunRequest:
    """构造安全模式最小请求。"""
    payload = {"company_refs": [{"eid": "eid-1"}]}
    payload.update(overrides)
    return PerformanceRunRequest(**payload)


def test_safe_mode_accepts_only_low_load_limits():
    """安全回归模式应限制并发、时长和请求数。"""
    request = _request(concurrency=3, duration_seconds=60, request_count=100)
    assert request.safety_mode == PerformanceSafetyMode.SAFE_REGRESSION

    with pytest.raises(ValidationError, match="并发不得超过 3"):
        _request(concurrency=4)
    with pytest.raises(ValidationError, match="持续时间不得超过 60 秒"):
        _request(duration_seconds=61)
    with pytest.raises(ValidationError, match="请求数不得超过 100"):
        _request(request_count=101)


def test_controlled_real_database_requires_exact_confirmation():
    """受控真实库模式必须提供固定确认口令。"""
    with pytest.raises(ValidationError, match=REAL_DATABASE_CONFIRMATION):
        _request(safety_mode=PerformanceSafetyMode.CONTROLLED_REAL_DATABASE, concurrency=10)

    request = _request(
        safety_mode=PerformanceSafetyMode.CONTROLLED_REAL_DATABASE,
        concurrency=10,
        duration_seconds=300,
        request_count=1000,
        real_database_confirmation=REAL_DATABASE_CONFIRMATION,
    )
    assert request.concurrency == 10
    assert request.duration_seconds == 300


def test_scenarios_are_deduplicated_without_reordering():
    """重复场景不能造成重复负载。"""
    request = _request(
        scenarios=[
            PerformanceScenario.ENTERPRISE_PROFILE,
            PerformanceScenario.SHAREHOLDER_LIST,
            PerformanceScenario.ENTERPRISE_PROFILE,
        ]
    )
    assert request.scenarios == [
        PerformanceScenario.ENTERPRISE_PROFILE,
        PerformanceScenario.SHAREHOLDER_LIST,
    ]


def test_global_performance_request_does_not_require_company_refs():
    """性能入口默认由服务层从全局目录自动选样。"""
    request = PerformanceRunRequest()

    assert request.scope == "global"
    assert request.company_refs == []

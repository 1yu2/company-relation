"""企业尽调报告任务取消和身份前置校验单元测试。"""

import pytest

from yuxi.company_due_diligence.models import (
    CompanyResolutionResult,
    DataQuality,
    LatencyBreakdown,
    ResolutionStatus,
)
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest
from yuxi.company_due_diligence.report.service import (
    DueDiligenceReportService,
    ReportNotFoundError,
    ReportValidationError,
)

pytestmark = pytest.mark.asyncio


class UnresolvedResolver:
    """返回企业未找到状态。"""

    async def resolve(self, reference):
        """返回不允许创建报告的解析结果。"""
        return CompanyResolutionResult(
            status=ResolutionStatus.NOT_FOUND,
            reference=reference,
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )


class FakeTasker:
    """模拟报告任务查询和取消。"""

    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        self.cancelled = False

    async def get_task(self, task_id):
        """返回固定任务类型。"""
        return {"id": task_id, "type": self.task_type, "status": "running"}

    async def cancel_task(self, _task_id):
        """记录取消请求。"""
        self.cancelled = True
        return True


async def test_unresolved_company_does_not_create_report_task():
    """身份不唯一时应在入队前拒绝报告。"""
    service = DueDiligenceReportService(resolver=UnresolvedResolver())

    with pytest.raises(ReportValidationError):
        await service.create(DueDiligenceReportRequest(company_ref={"eid": "missing"}), FakeTasker("unused"))


async def test_cancel_only_accepts_report_tasks():
    """取消接口不得操作其他 Tasker 任务类型。"""
    service = DueDiligenceReportService()
    report_tasker = FakeTasker("company_due_diligence_report")
    other_tasker = FakeTasker("other")

    assert await service.cancel("report-1", report_tasker) is True
    assert report_tasker.cancelled is True
    with pytest.raises(ReportNotFoundError):
        await service.cancel("task-2", other_tasker)

"""企业尽调报告任务取消和身份前置校验单元测试。"""

import asyncio
from datetime import timedelta
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yuxi.company_due_diligence.models import (
    CompanyResolutionResult,
    ConfidenceLevel,
    DataQuality,
    LatencyBreakdown,
    MatchMethod,
    ResolutionStatus,
    ResolvedCompany,
)
from yuxi.company_due_diligence.report import service as report_module
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportVersions
from yuxi.company_due_diligence.report.service import (
    DueDiligenceReportService,
    ReportDispatchError,
    ReportNotFoundError,
    ReportTaskContext,
    ReportValidationError,
)
from yuxi.services.task_service import Task

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


class ResolvedResolver:
    """返回固定企业身份，供报告投递测试使用。"""

    def __init__(self, company: ResolvedCompany) -> None:
        self.company = company

    async def resolve(self, reference):
        """返回已确认的企业。"""
        return CompanyResolutionResult(
            status=ResolutionStatus.RESOLVED,
            reference=reference,
            company=self.company,
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


def _report_inputs():
    """构造可在 worker 侧重新解析的报告参数。"""
    company = ResolvedCompany(
        eid="eid-1",
        name="示例企业有限公司",
        normalized_name="示例企业有限公司",
        credit_code="credit-code",
        stock_code="000001.SZ",
        stock_abbreviation="示例",
        match_method=MatchMethod.EXACT_NAME,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[],
    )
    request = DueDiligenceReportRequest(company_ref={"eid": company.eid})
    versions = ReportVersions(code_version="test", config_version="test")
    return company, request, versions


async def test_report_submission_persists_before_dispatch(monkeypatch):
    """报告必须先持久化完整执行参数，再向独立 worker 投递。"""
    company, request, versions = _report_inputs()
    service = DueDiligenceReportService(resolver=ResolvedResolver(company))
    monkeypatch.setattr(service, "_lock_versions", AsyncMock(return_value=versions))
    order = []
    captured_payload = {}

    async def create_persisted(**kwargs):
        """记录数据库任务创建顺序。"""
        order.append("persist")
        captured_payload.update(kwargs["payload"])
        return Task(id="report-1", name=kwargs["name"], type=kwargs["task_type"])

    async def enqueue_report(task_id):
        """记录队列投递顺序。"""
        assert task_id == "report-1"
        order.append("enqueue")

    monkeypatch.setattr(service, "_enqueue_report", enqueue_report)
    task = await service.create(request, SimpleNamespace(create_persisted=create_persisted))

    assert task.id == "report-1"
    assert order == ["persist", "enqueue"]
    assert captured_payload["company"]["eid"] == company.eid
    assert captured_payload["request"]["company_ref"]["eid"] == company.eid
    assert captured_payload["versions"]["code_version"] == "test"
    assert captured_payload["created_at"]


async def test_report_dispatch_failure_marks_persisted_task_failed(monkeypatch):
    """队列不可用时持久化任务应明确失败，不能一直 pending。"""
    company, request, versions = _report_inputs()
    service = DueDiligenceReportService(resolver=ResolvedResolver(company))
    monkeypatch.setattr(service, "_lock_versions", AsyncMock(return_value=versions))
    monkeypatch.setattr(service, "_enqueue_report", AsyncMock(side_effect=ConnectionError("redis unavailable")))
    repository = SimpleNamespace(update_status=AsyncMock(return_value=True))
    monkeypatch.setattr(report_module, "TaskRepository", lambda: repository)
    task = Task(id="report-2", name="报告", type="company_due_diligence_report")
    tasker = SimpleNamespace(create_persisted=AsyncMock(return_value=task))

    with pytest.raises(ReportDispatchError):
        await service.create(request, tasker)

    assert repository.update_status.await_args.kwargs["expected_status"] == "pending"
    assert repository.update_status.await_args.kwargs["values"]["status"] == "failed"


async def test_report_worker_claims_task_once_and_publishes(monkeypatch):
    """重复投递同一任务时只执行一次报告并记录终态。"""
    company, request, versions = _report_inputs()
    payload = {
        "company": company.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "versions": versions.model_dump(mode="json"),
        "identity_sources": [],
        "created_at": datetime.now(UTC).isoformat(),
    }
    record = SimpleNamespace(id="report-3", type="company_due_diligence_report", status="running", payload=payload)
    record.cancel_requested = False
    repository = SimpleNamespace(
        claim_pending=AsyncMock(side_effect=[record, None]),
        get_by_id=AsyncMock(return_value=record),
        update_status=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(report_module, "TaskRepository", lambda: repository)
    report = SimpleNamespace(model_dump=lambda **kwargs: {"report_id": "report-3"})
    workflow = SimpleNamespace(run=AsyncMock(return_value=report))
    service = DueDiligenceReportService(workflow=workflow)
    monkeypatch.setattr(service, "_publish_report", AsyncMock(return_value=report))

    await service.execute_task("report-3")
    record.status = "success"
    await service.execute_task("report-3")

    workflow.run.assert_awaited_once()
    service._publish_report.assert_awaited_once()
    assert repository.update_status.await_args.kwargs["values"]["status"] == "success"


@pytest.mark.parametrize(
    ("cancel_requested", "expected_status"),
    [(True, "cancelled"), (False, "failed")],
)
async def test_report_worker_records_cancellation_or_interruption(monkeypatch, cancel_requested, expected_status):
    """执行中收到取消或被中断时，持久化任务不能停留在 running。"""
    company, request, versions = _report_inputs()
    record = SimpleNamespace(
        id="report-interrupted",
        type="company_due_diligence_report",
        status="running",
        cancel_requested=False,
        payload={
            "company": company.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "versions": versions.model_dump(mode="json"),
            "identity_sources": [],
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=record),
        get_by_id=AsyncMock(side_effect=[record, SimpleNamespace(cancel_requested=cancel_requested)]),
        update_status=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(report_module, "TaskRepository", lambda: repository)
    workflow = SimpleNamespace(run=AsyncMock(side_effect=asyncio.CancelledError))
    service = DueDiligenceReportService(workflow=workflow)

    with pytest.raises(asyncio.CancelledError):
        await service.execute_task(record.id)

    assert repository.update_status.await_args.kwargs["values"]["status"] == expected_status


async def test_report_context_observes_other_process_cancellation():
    """报告章节边界必须看到数据库中的取消请求。"""
    repository = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(cancel_requested=True)))
    context = ReportTaskContext("report-4", repository)

    with pytest.raises(asyncio.CancelledError):
        await context.raise_if_cancelled()


async def test_worker_restart_keeps_active_report_and_retries_stale_report_once(monkeypatch):
    """worker 重启不应中断活跃报告，只接管明确过期且未超次数的报告。"""
    now = report_module.utc_now_naive()
    records = [
        SimpleNamespace(id="pending-report", status="pending", updated_at=now, payload=None),
        SimpleNamespace(id="active-report", status="running", updated_at=now, payload={}),
        SimpleNamespace(
            id="stale-report",
            status="running",
            updated_at=now - timedelta(seconds=report_module.REPORT_RECOVERY_STALE_SECONDS + 1),
            payload={},
        ),
    ]
    repository = SimpleNamespace(
        list_by_type=AsyncMock(return_value=records),
        update_status=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(report_module, "TaskRepository", lambda: repository)
    service = DueDiligenceReportService()
    enqueue = AsyncMock()
    monkeypatch.setattr(service, "_enqueue_report", enqueue)

    await service.recover_pending_tasks()

    assert [call.args[0] for call in enqueue.await_args_list] == ["pending-report", "stale-report"]
    assert repository.update_status.await_count == 1
    update = repository.update_status.await_args
    assert update.kwargs["expected_status"] == "running"
    assert update.kwargs["values"]["status"] == "pending"
    assert update.kwargs["values"]["payload"]["_recovery_attempts"] == 1

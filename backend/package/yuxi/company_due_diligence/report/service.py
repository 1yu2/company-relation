"""企业尽调报告 Tasker 编排、MinIO 产物和查询服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime

from yuxi.company_due_diligence.entity_resolution import EntityResolutionService, entity_resolution_service
from yuxi.company_due_diligence.evidence.indexer import EvidenceIndexUnavailableError
from yuxi.company_due_diligence.evidence.service import EvidenceIndexService, evidence_index_service
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.service import GraphProjectionService, graph_projection_service
from yuxi.company_due_diligence.models import ResolutionStatus
from yuxi.company_due_diligence.report.graph import DueDiligenceReportWorkflow, _assemble_report
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReport,
    DueDiligenceReportRequest,
    ReportArtifact,
    ReportFormat,
    ReportSection,
    ReportVersions,
)
from yuxi.company_due_diligence.report.renderers import (
    content_checksum,
    render_report_json,
    render_report_markdown,
)
from yuxi.config import config
from yuxi.services.task_service import TaskContext, Tasker
from yuxi.storage.minio import MinIOClient, StorageError, get_minio_client

REPORT_TASK_TYPE = "company_due_diligence_report"
REPORT_SECTION_RETRY_TASK_TYPE = "company_due_diligence_report_section_retry"
REPORT_BUCKET = os.getenv("COMPANY_DD_REPORT_BUCKET", "company-due-diligence")


class ReportNotFoundError(LookupError):
    """表示报告任务或持久化报告不存在。"""


class ReportValidationError(ValueError):
    """表示报告企业身份或状态不满足操作前提。"""


class DueDiligenceReportService:
    """协调身份锁定、Tasker、章节工作流和 MinIO 文件。"""

    def __init__(
        self,
        *,
        resolver: EntityResolutionService | None = None,
        workflow: DueDiligenceReportWorkflow | None = None,
        graph_service: GraphProjectionService | None = None,
        evidence_service: EvidenceIndexService | None = None,
        minio_client: MinIOClient | None = None,
        bucket_name: str = REPORT_BUCKET,
    ) -> None:
        self._resolver = resolver or entity_resolution_service
        self._workflow = workflow or DueDiligenceReportWorkflow()
        self._graph_service = graph_service or graph_projection_service
        self._evidence_service = evidence_service or evidence_index_service
        self._minio = minio_client or get_minio_client()
        self._bucket_name = bucket_name
        self._report_locks: dict[str, asyncio.Lock] = {}

    async def create(self, request: DueDiligenceReportRequest, tasker: Tasker):
        """先解析并锁定唯一企业身份，再提交报告后台任务。"""
        resolution = await self._resolver.resolve(request.company_ref)
        if resolution.status != ResolutionStatus.RESOLVED or resolution.company is None:
            raise ReportValidationError("企业无法唯一解析，未创建报告任务")
        versions = await self._lock_versions(request)
        company = resolution.company
        created_at = datetime.now(UTC)

        async def run_report(context: TaskContext):
            """执行单个完整报告任务并发布双格式产物。"""
            await context.set_progress(5, "企业身份和派生版本已锁定")

            async def report_progress(progress: float, message: str) -> None:
                """转发章节进度并检查取消信号。"""
                await context.raise_if_cancelled()
                await context.set_progress(progress, message)

            report = await self._workflow.run(
                report_id=context.task_id,
                request=request,
                company=company,
                identity_sources=resolution.sources,
                versions=versions,
                created_at=created_at,
                progress=report_progress,
            )
            await context.raise_if_cancelled()
            await context.set_progress(92, "开始发布 JSON 和 Markdown 报告")
            report = await self._publish_report(report)
            result = report.model_dump(mode="json")
            await context.set_result(result)
            return result

        return await tasker.enqueue(
            name=f"企业尽调报告：{company.name}",
            task_type=REPORT_TASK_TYPE,
            payload={
                "company": company.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                "versions": versions.model_dump(mode="json"),
            },
            coroutine=run_report,
        )

    async def generate_inline(
        self,
        request: DueDiligenceReportRequest,
        *,
        report_id: str,
    ) -> DueDiligenceReport:
        """供评测和低负载性能场景直接生成不落盘报告数据包。"""
        resolution = await self._resolver.resolve(request.company_ref)
        if resolution.status != ResolutionStatus.RESOLVED or resolution.company is None:
            raise ReportValidationError("企业无法唯一解析，无法生成报告数据包")
        versions = await self._lock_versions(request)
        return await self._workflow.run(
            report_id=report_id,
            request=request,
            company=resolution.company,
            identity_sources=resolution.sources,
            versions=versions,
            created_at=datetime.now(UTC),
        )

    async def list_reports(self, tasker: Tasker, limit: int = 100) -> list[dict]:
        """返回 Tasker 中的报告任务摘要。"""
        result = await tasker.list_tasks(limit=max(limit, 100))
        reports = [task for task in result["tasks"] if task["type"] == REPORT_TASK_TYPE]
        return reports[:limit]

    async def get_report(self, report_id: str, tasker: Tasker) -> dict:
        """返回报告任务状态和已发布报告记录。"""
        task = await tasker.get_task(report_id)
        if task is None or task["type"] != REPORT_TASK_TYPE:
            raise ReportNotFoundError("企业尽调报告不存在")
        report = await self._read_report(report_id)
        return {"task": task, "report": report.model_dump(mode="json") if report else None}

    async def cancel(self, report_id: str, tasker: Tasker) -> bool:
        """仅取消属于企业尽调报告类型的非终态任务。"""
        task = await tasker.get_task(report_id)
        if task is None or task["type"] != REPORT_TASK_TYPE:
            raise ReportNotFoundError("企业尽调报告不存在")
        return await tasker.cancel_task(report_id)

    async def retry_section(self, report_id: str, section: ReportSection, tasker: Tasker):
        """提交单章节重试任务并在完成后重新发布原报告。"""
        report = await self._read_report(report_id)
        if report is None:
            raise ReportNotFoundError("企业尽调报告尚未生成或不存在")

        async def run_retry(context: TaskContext):
            """重跑单章节并更新原报告记录与文件。"""
            await context.set_progress(10, f"开始重试章节：{section.value}")
            if section == ReportSection.DATA_QUALITY:
                from yuxi.company_due_diligence.report.graph import _data_quality_section

                result = _data_quality_section(
                    item for key, item in report.sections.items() if key != ReportSection.DATA_QUALITY.value
                )
            else:
                result = await self._workflow.run_section(section, report.request, report.company, report.versions)
            lock = self._report_locks.setdefault(report_id, asyncio.Lock())
            async with lock:
                latest = await self._read_report(report_id)
                if latest is None:
                    raise ReportNotFoundError("原报告记录不存在")
                latest.sections[section.value] = result
                updated = _assemble_report(
                    report_id=latest.report_id,
                    request=latest.request,
                    company=latest.company,
                    versions=latest.versions,
                    created_at=latest.created_at,
                    sections=latest.sections,
                    base_sources=latest.sources,
                )
                updated = await self._publish_report(updated)
            await context.set_progress(100, f"章节重试完成：{section.value}")
            return updated.model_dump(mode="json")

        return await tasker.enqueue(
            name=f"企业尽调报告章节重试：{section.value}",
            task_type=REPORT_SECTION_RETRY_TASK_TYPE,
            payload={"report_id": report_id, "section": section.value},
            coroutine=run_retry,
        )

    async def download(self, report_id: str, report_format: ReportFormat) -> tuple[bytes, ReportArtifact]:
        """读取已发布报告文件并重新校验 checksum。"""
        report = await self._read_report(report_id)
        if report is None:
            raise ReportNotFoundError("企业尽调报告尚未生成或不存在")
        artifact = next((item for item in report.artifacts if item.format == report_format), None)
        if artifact is None:
            raise ReportNotFoundError("请求格式未生成")
        try:
            content = await self._minio.adownload_file(artifact.bucket, artifact.object_name)
        except StorageError as exc:
            raise ReportNotFoundError("报告文件不存在") from exc
        if content_checksum(content) != artifact.checksum:
            raise RuntimeError("报告文件 checksum 校验失败")
        return content, artifact

    async def _lock_versions(self, request: DueDiligenceReportRequest) -> ReportVersions:
        """在入队前记录代码、配置、图谱和证据当前版本。"""
        graph_version = None
        evidence_version = None
        try:
            info = await self._graph_service.get_info()
            graph_version = info.current.projection_version if info.current else None
        except GraphUnavailableError:
            pass
        try:
            info = await self._evidence_service.get_info()
            evidence_version = info.current.projection_version if info.current else None
        except EvidenceIndexUnavailableError:
            pass
        config_payload = json.dumps(
            {
                "default_model": config.default_model,
                "embed_model": config.embed_model,
                "request": request.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        return ReportVersions(
            code_version=os.getenv("YUXI_VERSION", "0.7.1"),
            config_version=hashlib.sha256(config_payload).hexdigest()[:16],
            graph_version=graph_version,
            evidence_version=evidence_version,
        )

    async def _publish_report(self, report: DueDiligenceReport) -> DueDiligenceReport:
        """上传请求格式文件，最后写 record.json 作为报告原子指针。"""
        artifacts = []
        for report_format in report.request.output_formats:
            if report_format == ReportFormat.JSON:
                content = render_report_json(report)
                suffix = "json"
                content_type = "application/json"
            else:
                content = render_report_markdown(report)
                suffix = "md"
                content_type = "text/markdown; charset=utf-8"
            object_name = f"reports/{report.report_id}/report.{suffix}"
            await self._minio.aupload_file(self._bucket_name, object_name, content, content_type)
            artifacts.append(
                ReportArtifact(
                    format=report_format,
                    bucket=self._bucket_name,
                    object_name=object_name,
                    checksum=content_checksum(content),
                    size_bytes=len(content),
                )
            )
        report.artifacts = artifacts
        await self._minio.aupload_file(
            self._bucket_name,
            f"reports/{report.report_id}/record.json",
            report.model_dump_json(indent=2).encode(),
            "application/json",
        )
        return report

    async def _read_report(self, report_id: str) -> DueDiligenceReport | None:
        """读取持久化报告记录；bucket 或对象不存在时返回空。"""
        try:
            bucket_exists = await asyncio.to_thread(self._minio.client.bucket_exists, self._bucket_name)
            if not bucket_exists:
                return None
            object_name = f"reports/{report_id}/record.json"
            if await self._minio.astat_file(self._bucket_name, object_name) is None:
                return None
            payload = await self._minio.adownload_file(self._bucket_name, object_name)
            return DueDiligenceReport.model_validate_json(payload)
        except StorageError:
            return None


due_diligence_report_service = DueDiligenceReportService()

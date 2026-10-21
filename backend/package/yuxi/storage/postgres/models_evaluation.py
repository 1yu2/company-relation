"""平台级评测持久化模型。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_knowledge import JSON_VALUE
from yuxi.utils.datetime_utils import utc_now_naive


class PlatformEvaluationDataset(Base):
    """平台评测数据集。"""

    __tablename__ = "platform_eval_datasets"

    dataset_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    source_kind = Column(String(32), nullable=False, default="fixture", index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class PlatformEvaluationDatasetVersion(Base):
    """不可变的平台评测数据集版本。"""

    __tablename__ = "platform_eval_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="uq_platform_eval_dataset_versions_number"),
        UniqueConstraint("checksum", name="uq_platform_eval_dataset_versions_checksum"),
        Index("ix_platform_eval_dataset_versions_dataset", "dataset_id", "created_at"),
    )

    version_id = Column(String(64), primary_key=True)
    dataset_id = Column(String(64), ForeignKey("platform_eval_datasets.dataset_id", ondelete="CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=False)
    source = Column(JSON_VALUE, nullable=False, default=dict)
    status = Column(String(16), nullable=False, default="draft", index=True)
    published_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationTask(Base):
    """数据集版本中的单道评测任务。"""

    __tablename__ = "platform_eval_tasks"
    __table_args__ = (UniqueConstraint("version_id", "task_key", name="uq_platform_eval_tasks_key"),)

    task_id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64),
        ForeignKey("platform_eval_dataset_versions.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_key = Column(String(255), nullable=False)
    input = Column(JSON_VALUE, nullable=False, default=dict)
    expected = Column(JSON_VALUE, nullable=False, default=dict)
    protocol = Column(String(64), nullable=False, default="agent_tool_final_v1")
    tags = Column(JSON_VALUE, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationExperiment(Base):
    """一次评测实验及其配置快照。"""

    __tablename__ = "platform_eval_experiments"

    experiment_id = Column(String(64), primary_key=True)
    dataset_version_id = Column(
        String(64),
        ForeignKey("platform_eval_dataset_versions.version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_kind = Column(String(32), nullable=False, default="fixture", index=True)
    agent_slug = Column(String(255))
    runner = Column(String(64), nullable=False, default="fixture")
    # 多协议数据集按协议拼接各评分版本，长度不固定；各协议明细见 config_snapshot.captured.evaluators。
    evaluator_version = Column(Text, nullable=False, default="deterministic-v1")
    config_snapshot = Column(JSON_VALUE, nullable=False, default=dict)
    concurrency = Column(Integer, nullable=False, default=1)
    timeout_seconds = Column(Integer, nullable=False, default=30)
    max_attempts = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)


class PlatformEvaluationRollout(Base):
    """单题的一次尝试结果。"""

    __tablename__ = "platform_eval_rollouts"
    __table_args__ = (
        UniqueConstraint("experiment_id", "task_id", "attempt", name="uq_platform_eval_rollouts_attempt"),
        UniqueConstraint("idempotency_key", name="uq_platform_eval_rollouts_idempotency"),
        Index("ix_platform_eval_rollouts_experiment_status", "experiment_id", "status"),
    )

    rollout_id = Column(String(64), primary_key=True)
    experiment_id = Column(
        String(64), ForeignKey("platform_eval_experiments.experiment_id", ondelete="CASCADE"), nullable=False
    )
    task_id = Column(String(64), ForeignKey("platform_eval_tasks.task_id", ondelete="RESTRICT"), nullable=False)
    attempt = Column(Integer, nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    actual_output = Column(JSON_VALUE)
    output_summary = Column(JSON_VALUE)
    source_ref = Column(JSON_VALUE)
    failure_type = Column(String(64))
    reason_code = Column(String(128))
    error_message = Column(Text)
    duration_ms = Column(Float)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationTraceEvent(Base):
    """Rollout 的有序、脱敏执行事件。"""

    __tablename__ = "platform_eval_trace_events"
    __table_args__ = (UniqueConstraint("rollout_id", "sequence", name="uq_platform_eval_trace_sequence"),)

    event_id = Column(String(64), primary_key=True)
    rollout_id = Column(
        String(64), ForeignKey("platform_eval_rollouts.rollout_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)
    parent_id = Column(String(64))
    component = Column(String(64), nullable=False)
    operation = Column(String(128), nullable=False)
    input_summary = Column(JSON_VALUE)
    output_summary = Column(JSON_VALUE)
    checksum = Column(String(64), nullable=False)
    reason_code = Column(String(128))
    occurred_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationMetricResult(Base):
    """单个 Rollout 的确定性指标结果。"""

    __tablename__ = "platform_eval_metric_results"
    __table_args__ = (
        UniqueConstraint("rollout_id", "metric_name", "metric_version", name="uq_platform_eval_metric_result"),
    )

    metric_id = Column(String(64), primary_key=True)
    rollout_id = Column(
        String(64), ForeignKey("platform_eval_rollouts.rollout_id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name = Column(String(128), nullable=False)
    metric_version = Column(String(64), nullable=False)
    value = Column(Float)
    status = Column(String(24), nullable=False)
    reason_code = Column(String(128))
    input_checksum = Column(String(64))
    details = Column(JSON_VALUE)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationFailure(Base):
    """可排查的 Rollout 或指标失败原因。"""

    __tablename__ = "platform_eval_failures"

    failure_id = Column(String(64), primary_key=True)
    rollout_id = Column(
        String(64), ForeignKey("platform_eval_rollouts.rollout_id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_id = Column(String(64), ForeignKey("platform_eval_metric_results.metric_id", ondelete="SET NULL"))
    trace_event_id = Column(String(64), ForeignKey("platform_eval_trace_events.event_id", ondelete="SET NULL"))
    failure_type = Column(String(64), nullable=False)
    reason_code = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)
    retryable = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class PlatformEvaluationOnlineFinding(Base):
    """线上抽样命中通用规则的对话 Run；只保存 Run 标识、命中规则和计数，不复制对话内容。"""

    __tablename__ = "platform_eval_online_findings"

    finding_id = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    agent_slug = Column(String(255), nullable=False)
    run_status = Column(String(32), nullable=False)
    rule_hits = Column(JSON_VALUE, nullable=False, default=list)
    summary = Column(JSON_VALUE, nullable=False, default=dict)
    feedback = Column(JSON_VALUE, nullable=True)
    # pending_review：待人工复核；promoted：已生成草稿题
    status = Column(String(32), nullable=False, default="pending_review", index=True)
    promoted_version_id = Column(String(64), ForeignKey("platform_eval_dataset_versions.version_id"), nullable=True)
    run_finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

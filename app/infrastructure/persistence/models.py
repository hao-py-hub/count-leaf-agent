"""资产、分析任务及表型领域投影的数据表定义。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """持久化模型的基类。"""


class AgentAssetModel(Base):
    __tablename__ = "agent_assets"

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="agent_upload")
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AnalysisJobModel(Base):
    __tablename__ = "analysis_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_assets.asset_id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), index=True)


class AnalysisBatchModel(Base):
    __tablename__ = "analysis_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)


class AnalysisBatchJobModel(Base):
    __tablename__ = "analysis_batch_jobs"

    batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_batches.batch_id"), primary_key=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_jobs.job_id"), primary_key=True
    )


class PlantSampleModel(Base):
    __tablename__ = "plant_samples"

    sample_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(36), index=True)


class ResultVersionModel(Base):
    __tablename__ = "result_versions"

    version_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(200), ForeignKey("plant_samples.sample_id"), nullable=False, index=True)
    analysis_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("analysis_jobs.job_id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="automatic")
    algorithm_version: Mapped[str | None] = mapped_column(String(128))
    policy_version: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class PlantPartModel(Base):
    __tablename__ = "plant_parts"

    part_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(80), ForeignKey("result_versions.version_id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_part_id: Mapped[str | None] = mapped_column(String(64))


class PodObservationModel(Base):
    __tablename__ = "pod_observations"

    observation_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    part_id: Mapped[str] = mapped_column(String(80), ForeignKey("plant_parts.part_id"), nullable=False, index=True)
    pod_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="cv")


class QualityFindingModel(Base):
    __tablename__ = "quality_findings"

    finding_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(200), ForeignKey("plant_samples.sample_id"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(36), index=True)

"""将分析结果投影为可查询的表型领域记录。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.infrastructure.persistence.models import (
    PlantPartModel, PlantSampleModel, PodObservationModel, QualityFindingModel,
    ResultVersionModel,
)


class PhenotypeDomainProjection:
    """幂等的本地投影，不会改写权威的 CV 分析结果。"""

    def __init__(self, session_factory: Callable[[], Session], *, policy_version: str = "phenotype-policy-v1") -> None:
        self._session_factory = session_factory
        self._policy_version = policy_version

    def project_completed_analysis(self, job: Any) -> str:
        return self._project_result(job, job.result or {}, source="automatic", revision=0)

    def project_manual_revision(self, job: Any, result: dict) -> str:
        return self._project_result(job, result, source="manual", revision=int(result.get("manual_revision") or 0))

    def _project_result(self, job: Any, result: dict, *, source: str, revision: int) -> str:
        sample_id = f"{job.project_id}:{job.asset_id}"
        version_id = f"{job.job_id}:{source}:{revision}"
        provenance = result.get("provenance") or {}
        with self._session_factory.begin() as session:
            sample = session.get(PlantSampleModel, sample_id)
            if sample is None:
                session.add(PlantSampleModel(sample_id=sample_id, project_id=job.project_id, asset_id=str(job.asset_id), source_run_id=job.run_id, trace_id=job.trace_id))
            version = session.get(ResultVersionModel, version_id)
            if version is None:
                session.add(ResultVersionModel(version_id=version_id, sample_id=sample_id, analysis_job_id=str(job.job_id), revision=revision, source=source, algorithm_version=provenance.get("algorithm_version"), policy_version=self._policy_version, trace_id=job.trace_id, result_json=result))
                for index, part in enumerate(result.get("plants") or []):
                    part_id = f"{version_id}:part:{index}"
                    count = max(0, int(part.get("pod_count") or 0))
                    session.add(PlantPartModel(part_id=part_id, version_id=version_id, label=str(part.get("label") or "unknown"), observation_count=count, source_part_id=str(part.get("id")) if part.get("id") is not None else None))
                    session.add(PodObservationModel(observation_id=f"{part_id}:pods", part_id=part_id, pod_count=count, confidence=str(part.get("confidence")) if part.get("confidence") is not None else None, source="cv"))
        return version_id

    def record_quality_findings(self, job: Any, reasons: list[str], codes: list[str]) -> None:
        sample_id = f"{job.project_id}:{job.asset_id}"
        with self._session_factory.begin() as session:
            for index, code in enumerate(codes):
                finding_id = f"{job.job_id}:quality:{code}"
                if session.get(QualityFindingModel, finding_id) is None:
                    session.add(QualityFindingModel(finding_id=finding_id, sample_id=sample_id, reason_code=code, detail=reasons[index] if index < len(reasons) else code, policy_version=self._policy_version, trace_id=job.trace_id))

    def list_samples(self, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            samples = session.scalars(select(PlantSampleModel).where(PlantSampleModel.project_id == project_id).limit(limit)).all()
            payload = []
            for sample in samples:
                versions = session.scalars(select(ResultVersionModel).where(ResultVersionModel.sample_id == sample.sample_id)).all()
                version_ids = [version.version_id for version in versions]
                parts = session.scalars(select(PlantPartModel).where(PlantPartModel.version_id.in_(version_ids))).all() if version_ids else []
                findings = session.scalars(select(QualityFindingModel).where(QualityFindingModel.sample_id == sample.sample_id)).all()
                payload.append({"sample_id": sample.sample_id, "source_run_id": sample.source_run_id, "trace_id": sample.trace_id, "versions": [{"version_id": version.version_id, "source": version.source, "revision": version.revision, "algorithm_version": version.algorithm_version} for version in versions], "parts": [{"label": part.label, "pod_count": part.observation_count} for part in parts], "quality_findings": [{"reason_code": finding.reason_code, "detail": finding.detail} for finding in findings]})
            return payload

"""表型分析持久化任务的 Celery 注册入口。"""

from uuid import UUID

from app.bootstrap import build_enterprise_dependencies
from app.config import AUDIT_LEDGER_DB_PATH, HISTORY_DB_PATH, REVIEW_DB_PATH
from app.history import HistoryStore
from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.image_quality_service import ImageQualityService
from app.services.phenotype_analysis_service import PhenotypeAnalysisService
from app.services.review_service import ReviewService
from app.services.review_reasoning import review_reason_details
from app.services.audit_ledger import AuditLedger
from app.services.phenotype_domain_projection import PhenotypeDomainProjection

from .celery_app import celery_app


@celery_app.task(name="phenotype.execute_analysis_job")
def execute_analysis_job(job_id: str) -> dict[str, str]:
    """仅依赖持久化数据库状态执行由 API 创建的任务。"""
    dependencies = build_enterprise_dependencies()
    try:
        assets = AssetStore(dependencies.assets)
        review_service = ReviewService(REVIEW_DB_PATH)
        audit_ledger = AuditLedger(AUDIT_LEDGER_DB_PATH)
        domain_projection = PhenotypeDomainProjection(dependencies.database.session_factory)

        def enqueue_low_quality_review(job) -> bool:
            domain_projection.project_completed_analysis(job)
            quality = ImageQualityService().inspect(assets.get(job.asset_id).file_path)
            result = job.result or {}
            reasons, codes = review_reason_details(quality, result)
            domain_projection.record_quality_findings(job, reasons, codes)
            if reasons:
                review = review_service.create(
                    job.job_id, "；".join(reasons), trace_id=job.trace_id,
                    reason_code=",".join(codes), project_id=job.project_id,
                )
                audit_ledger.event(
                    event_type="review_created", source="review_trigger",
                    trace_id=job.trace_id, resource_id=str(job.job_id), status="open",
                    metadata={"review_id": review["review_id"], "reason_code": review["reason_code"]},
                )
                return True
            return False

        service = AnalysisJobService(
            assets,
            PhenotypeAnalysisService(HistoryStore(HISTORY_DB_PATH)),
            repository=dependencies.analysis_jobs,
            on_completed=enqueue_low_quality_review,
            audit_event=audit_ledger.event,
            record_model_call=audit_ledger.model_call,
        )
        service.execute_persisted(UUID(job_id))
        job = service.get(UUID(job_id))
        return {"job_id": str(job.job_id), "status": job.status}
    finally:
        dependencies.database.engine.dispose()

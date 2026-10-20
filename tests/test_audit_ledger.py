from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from uuid import uuid4

from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.audit_ledger import AuditLedger


class SuccessfulRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {"type": "result", "run_id": "run-ledger", "model_usage": {"input_tokens": 17, "output_tokens": 9}}


class MultiCallRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {
            "type": "result", "run_id": "run-multi",
            "model_calls": [
                {"source": "label", "input_tokens": 10, "output_tokens": 2},
                {"source": "pod_verify", "input_tokens": 4, "output_tokens": 1, "error_code": "parse_error"},
            ],
        }


class AuditLedgerTests(unittest.TestCase):
    def test_event_and_model_call_are_trace_filterable_and_redacted(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            trace_id = str(uuid4())
            ledger.event(event_type="analysis_submitted", source="job_service", trace_id=trace_id, metadata={"job_id": "job-1"})
            ledger.model_call(source="phenotype_analysis", trace_id=trace_id, model_version="model-v1", input_tokens=None, output_tokens=None, duration_ms=12)
            event = ledger.list_events(trace_id=trace_id)[0]
            call = ledger.list_model_calls(trace_id=trace_id)[0]
            self.assertEqual(event["event_type"], "analysis_submitted")
            self.assertEqual(event["metadata"], {"job_id": "job-1"})
            self.assertEqual(call["model_version"], "model-v1")
            self.assertIsNone(call["input_tokens"])
            self.assertNotIn("prompt", str(event))

    def test_job_service_records_submission_completion_and_pipeline_cost(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image_path = root / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            ledger = AuditLedger(root / "ledger.sqlite3")
            trace_id = str(uuid4())
            service = AnalysisJobService(
                asset_store, SuccessfulRunner(), audit_event=ledger.event,
                record_model_call=ledger.model_call,
                cache_context={"input_cost_per_million": "2", "output_cost_per_million": "4"},
            )
            job_id = service.submit(asset.asset_id, trace_id=trace_id)
            deadline = time.monotonic() + 2
            while service.get(job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            event_types = {event["event_type"] for event in ledger.list_events(trace_id=trace_id)}
            calls = ledger.list_model_calls(trace_id=trace_id)
            self.assertEqual(service.get(job_id).status, "completed")
            self.assertEqual(event_types, {"analysis_cache_miss", "analysis_submitted", "analysis_completed"})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["result_reference"], "run-ledger")
            self.assertEqual(calls[0]["model_endpoint"], service._cache_context["model_endpoint"])
            self.assertEqual(calls[0]["input_tokens"], 17)
            self.assertEqual(calls[0]["output_tokens"], 9)
            self.assertEqual(calls[0]["estimated_cost"], 0.00007)

    def test_metrics_calculate_cache_and_review_rates(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="analysis_cache_miss", source="job_service")
            ledger.event(event_type="analysis_cache_hit", source="job_service", metadata={"source_run_id": "run-1"})
            ledger.event(event_type="analysis_completed", source="worker")
            ledger.event(event_type="review_created", source="review_api")
            ledger.model_call(source="phenotype_analysis", input_tokens=10, output_tokens=5, estimated_cost=0.25, result_reference="run-1")
            metrics = ledger.metrics()
            self.assertEqual(metrics["cache"]["request_count"], 2)
            self.assertEqual(metrics["cache"]["hit_rate"], 0.5)
            self.assertEqual(metrics["cache"]["estimated_model_calls_saved"], 1)
            self.assertEqual(metrics["cache"]["estimated_cost_saved"], 0.25)
            self.assertEqual(metrics["analysis"]["review_rate"], 1.0)
            self.assertEqual(metrics["model_calls"]["estimated_cost"], 0.25)

    def test_job_service_records_each_pipeline_model_call(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image_path = root / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            ledger = AuditLedger(root / "ledger.sqlite3")
            service = AnalysisJobService(
                asset_store, MultiCallRunner(), record_model_call=ledger.model_call,
                cache_context={"input_cost_per_million": "1", "output_cost_per_million": "2"},
            )
            job_id = service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            while service.get(job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            calls = ledger.list_model_calls()
            self.assertEqual([call["source"] for call in reversed(calls)], ["phenotype_analysis.label", "phenotype_analysis.pod_verify"])
            self.assertEqual(calls[0]["error_code"], "parse_error")
            self.assertEqual(calls[1]["estimated_cost"], 0.000014)

    def test_metrics_include_citation_coverage_and_budget_rejections(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="knowledge_answer", source="agent")
            ledger.event(event_type="knowledge_answer_cited", source="agent")
            ledger.event(event_type="knowledge_answer", source="agent")
            ledger.event(event_type="budget_rejected", source="agent")
            metrics = ledger.metrics()
            self.assertEqual(metrics["knowledge"]["answer_count"], 2)
            self.assertEqual(metrics["knowledge"]["citation_coverage"], 0.5)
            self.assertEqual(metrics["budget"]["rejection_count"], 1)

    def test_metrics_support_time_window_filter(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="analysis_completed", source="worker")
            metrics = ledger.metrics(since="9999-01-01T00:00:00+00:00")
            self.assertEqual(metrics["analysis"]["completed_count"], 0)

    def test_metrics_and_model_cost_are_project_scoped(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="analysis_cache_hit", source="job_service", metadata={"source_run_id": "run-a"}, project_id="project-a")
            ledger.event(event_type="analysis_cache_miss", source="job_service", project_id="project-b")
            ledger.model_call(source="phenotype_analysis", result_reference="run-a", estimated_cost=0.5, project_id="project-a")
            ledger.model_call(source="phenotype_analysis", result_reference="run-a", estimated_cost=5.0, project_id="project-b")

            metrics = ledger.metrics(project_id="project-a")
            self.assertEqual(metrics["project_id"], "project-a")
            self.assertEqual(metrics["cache"]["hit_count"], 1)
            self.assertEqual(metrics["cache"]["miss_count"], 0)
            self.assertEqual(metrics["cache"]["estimated_cost_saved"], 0.5)

    def test_metrics_group_review_reason_codes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="review_created", source="review_trigger", metadata={"reason_code": "image_quality,model_conflict"})
            metrics = ledger.metrics()
            self.assertEqual(metrics["analysis"]["review_reason_counts"], {"image_quality": 1, "model_conflict": 1})

    def test_metrics_include_batch_lifecycle_counts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="batch_submitted", source="rest")
            ledger.event(event_type="batch_cancelled", source="rest")
            ledger.event(event_type="batch_retried", source="rest")
            batch = ledger.metrics()["batch"]
            self.assertEqual(batch, {"submitted_count": 1, "cancelled_count": 1, "retried_count": 1})

    def test_metrics_include_report_and_export_operations(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="report_submitted", source="rest")
            ledger.event(event_type="report_completed", source="report_worker")
            ledger.event(event_type="report_failed", source="report_worker")
            ledger.event(event_type="history_exported", source="rest")
            self.assertEqual(ledger.metrics()["operations"], {"report_submitted_count": 1, "report_completed_count": 1, "report_failed_count": 1, "history_exported_count": 1})

    def test_metrics_report_zero_unconfirmed_executed_writes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
            ledger.event(event_type="analysis_submitted", source="job_service")
            ledger.event(event_type="review_resolved", source="review_api")
            self.assertEqual(ledger.metrics()["write_operations"], {
                "executed_count": 2,
                "unconfirmed_executed_count": 0,
                "unconfirmed_execution_rate": 0.0,
            })


if __name__ == "__main__":
    unittest.main()

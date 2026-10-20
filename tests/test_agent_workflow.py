import importlib.util
import unittest
from uuid import uuid4


LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(LANGGRAPH_AVAILABLE, "langgraph is not installed in this environment")
class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        from app.agent.models import AgentChatRequest
        from app.agent.workflow import AgentWorkflow

        self.request_model = AgentChatRequest
        self.workflow = AgentWorkflow()

    def test_phenotyping_request_without_asset_requires_asset(self):
        response = self.workflow.invoke(
            self.request_model(message="请分析这张油菜图片")
        )

        self.assertEqual(response.status, "needs_asset")
        self.assertEqual(response.requested_tool, "submit_analysis")
        self.assertEqual(response.skill_name, "phenotype-analysis")
        self.assertEqual(response.skill_version, "v0.1")
        self.assertIsNotNone(response.trace_id)

    def test_phenotyping_request_with_asset_does_not_fabricate_job(self):
        response = self.workflow.invoke(
            self.request_model(message="分析角果", asset_id=uuid4(), confirm_submission=True)
        )

        self.assertEqual(response.status, "integration_pending")
        self.assertIn("不会创建虚假的任务记录", response.message)

    def test_phenotyping_submission_requires_explicit_confirmation(self):
        from app.agent.workflow import AgentWorkflow

        called = []
        workflow = AgentWorkflow(submit_analysis=lambda asset_id: called.append(asset_id))
        response = workflow.invoke(self.request_model(message="分析角果", asset_id=uuid4()))

        self.assertEqual(response.status, "confirmation_required")
        self.assertEqual(response.requested_tool, "submit_analysis")
        self.assertEqual(called, [])

    def test_injected_submit_tool_returns_real_job_identifier(self):
        from app.agent.workflow import AgentWorkflow

        expected_job_id = uuid4()
        workflow = AgentWorkflow(submit_analysis=lambda asset_id: expected_job_id)
        response = workflow.invoke(
            self.request_model(message="分析角果", asset_id=uuid4(), confirm_submission=True)
        )

        self.assertEqual(response.status, "submitted")
        self.assertEqual(response.job_id, expected_job_id)

    def test_analysis_result_uses_authoritative_tool_output(self):
        from app.agent.workflow import AgentWorkflow

        job_id = uuid4()
        expected = {"total_pods": 42, "algorithm": "stalk"}
        workflow = AgentWorkflow(get_analysis_result=lambda requested_id: expected)
        response = workflow.invoke(
            self.request_model(message="查询这次分析结果", analysis_job_id=job_id)
        )

        self.assertEqual(response.status, "result_retrieved")
        self.assertEqual(response.requested_tool, "get_analysis_result")
        self.assertEqual(response.job_id, job_id)
        self.assertEqual(response.data, expected)

    def test_batch_submission_requires_confirmation_then_returns_real_batch(self):
        from app.agent.workflow import AgentWorkflow

        asset_ids = [uuid4(), uuid4()]
        workflow = AgentWorkflow(
            submit_batch_analysis=lambda requested_ids: {
                "batch_id": "batch-001", "status": "running", "job_ids": [str(asset_id) for asset_id in requested_ids]
            }
        )
        request = self.request_model(message="批量分析图片", asset_ids=asset_ids)
        blocked = workflow.invoke(request)
        submitted = workflow.invoke(request.model_copy(update={"confirm_batch_submission": True}))

        self.assertEqual(blocked.status, "confirmation_required")
        self.assertEqual(submitted.status, "batch_submitted")
        self.assertEqual(submitted.requested_tool, "submit_batch_analysis")
        self.assertEqual(submitted.batch["job_ids"], [str(asset_id) for asset_id in asset_ids])

    def test_batch_submission_passes_project_scope_to_trace_callback(self):
        from app.agent.workflow import AgentWorkflow

        received = {}
        workflow = AgentWorkflow(
            submit_batch_analysis_with_trace=lambda asset_ids, trace_id, project_id: received.update(project_id=project_id) or {"batch_id": "batch-001", "status": "running", "job_ids": [str(asset_id) for asset_id in asset_ids]}
        )
        request = self.request_model(message="批量分析图片", asset_ids=[uuid4()], project_id="trial-a", confirm_batch_submission=True)
        workflow.invoke(request)
        self.assertEqual(received["project_id"], "trial-a")

    def test_batch_progress_uses_read_only_batch_tool(self):
        from app.agent.workflow import AgentWorkflow

        batch_id = uuid4()
        workflow = AgentWorkflow(
            get_batch_progress=lambda requested_id: {"batch_id": str(requested_id), "status": "running", "total": 2}
        )
        response = workflow.invoke(self.request_model(message="查询批量进度", batch_id=batch_id))

        self.assertEqual(response.status, "batch_retrieved")
        self.assertEqual(response.requested_tool, "get_batch_progress")
        self.assertEqual(response.batch["batch_id"], str(batch_id))

    def test_unsupported_request_is_not_routed_to_a_tool(self):
        response = self.workflow.invoke(self.request_model(message="你好"))

        self.assertEqual(response.status, "unsupported")
        self.assertIsNone(response.requested_tool)

    def test_knowledge_request_uses_injected_cited_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(answer_knowledge=lambda message: ("资料中的回答", ["doc:chunk"]))
        response = workflow.invoke(self.request_model(message="角果数量有什么用？"))

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.requested_tool, "retrieve_knowledge")
        self.assertEqual(response.citations, ["doc:chunk"])
        self.assertEqual(response.skill_name, "knowledge-grounded-answer")

    def test_photography_requirement_question_routes_to_knowledge(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(answer_knowledge=lambda message: ("拍摄规范说明", ["sop:background"]))
        response = workflow.invoke(self.request_model(message="黑色背景拍摄有哪些要求？"))

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.requested_tool, "retrieve_knowledge")

    def test_review_request_creates_review_for_supplied_job(self):
        from app.agent.workflow import AgentWorkflow

        expected_review_id = uuid4()
        workflow = AgentWorkflow(create_review=lambda job_id, reason: expected_review_id)
        response = workflow.invoke(self.request_model(message="请人工复核这个结果", analysis_job_id=uuid4(), confirm_review=True))

        self.assertEqual(response.status, "review_created")
        self.assertEqual(response.requested_tool, "create_review")
        self.assertEqual(response.review_id, expected_review_id)

    def test_review_request_reports_unavailable_when_job_is_not_completed(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(create_review=lambda job_id, reason: (_ for _ in ()).throw(ValueError("复核只能针对已完成的分析任务创建。")))
        response = workflow.invoke(self.request_model(message="请人工复核", analysis_job_id=uuid4(), confirm_review=True))

        self.assertEqual(response.status, "review_unavailable")

    def test_review_status_uses_read_only_review_tool(self):
        from app.agent.workflow import AgentWorkflow

        review_id = uuid4()
        workflow = AgentWorkflow(get_review=lambda requested_id: {"review_id": str(requested_id), "status": "open"})
        response = workflow.invoke(self.request_model(message="查询复核状态", review_id=review_id))

        self.assertEqual(response.status, "review_retrieved")
        self.assertEqual(response.requested_tool, "get_review")
        self.assertEqual(response.review_id, review_id)
        self.assertEqual(response.data["status"], "open")

    def test_review_resolution_requires_confirmation_then_uses_versioned_service(self):
        from app.agent.workflow import AgentWorkflow

        review_id = uuid4()
        calls = []
        workflow = AgentWorkflow(
            resolve_review=lambda requested_id, resolution, payload: calls.append(
                (requested_id, resolution, payload)
            ) or {"status": "resolved", "history_revision": 1}
        )
        request = self.request_model(
            message="提交复核结论",
            review_id=review_id,
            review_resolution="人工确认并修订",
            revision_payload={"plants": []},
        )
        blocked = workflow.invoke(request)
        resolved = workflow.invoke(request.model_copy(update={"confirm_review_resolution": True}))

        self.assertEqual(blocked.status, "confirmation_required")
        self.assertEqual(calls, [(review_id, "人工确认并修订", {"plants": []})])
        self.assertEqual(resolved.status, "review_resolved")
        self.assertEqual(resolved.requested_tool, "resolve_review")

    def test_review_resolution_prefers_trace_aware_service(self):
        from app.agent.workflow import AgentWorkflow

        review_id = uuid4(); calls = []
        workflow = AgentWorkflow(
            resolve_review_with_trace=lambda requested_id, resolution, payload, trace_id: calls.append(
                (requested_id, resolution, payload, trace_id)
            ) or {"status": "resolved"}
        )
        request = self.request_model(
            message="提交复核结论", review_id=review_id, review_resolution="人工确认",
            revision_payload={"plants": []}, confirm_review_resolution=True,
        )
        response = workflow.invoke(request)

        self.assertEqual(response.status, "review_resolved")
        self.assertEqual(calls, [(review_id, "人工确认", {"plants": []}, request.trace_id)])

    def test_report_request_uses_injected_report_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(generate_report=lambda job_id, question: "基于结果的报告")
        job_id = uuid4()
        response = workflow.invoke(self.request_model(message="生成报告", analysis_job_id=job_id, confirm_report=True))

        self.assertEqual(response.status, "report_generated")
        self.assertEqual(response.requested_tool, "create_report")

    def test_report_request_prefers_trace_aware_async_submission(self):
        from app.agent.workflow import AgentWorkflow

        report_job_id = uuid4(); calls = []
        workflow = AgentWorkflow(
            submit_report_with_trace=lambda job_id, question, trace_id: calls.append(
                (job_id, question, trace_id)
            ) or {"report_job_id": str(report_job_id), "status": "queued"}
        )
        request = self.request_model(message="生成报告", analysis_job_id=uuid4(), confirm_report=True)
        response = workflow.invoke(request)

        self.assertEqual(response.status, "report_submitted")
        self.assertEqual(response.report_job_id, report_job_id)
        self.assertEqual(calls, [(request.analysis_job_id, "生成报告", request.trace_id)])

    def test_report_status_uses_read_only_report_tool(self):
        from app.agent.workflow import AgentWorkflow

        report_job_id = uuid4()
        workflow = AgentWorkflow(get_report=lambda requested_id: {"report_job_id": str(requested_id), "status": "completed"})
        response = workflow.invoke(self.request_model(message="查询报告状态", report_job_id=report_job_id))

        self.assertEqual(response.status, "report_retrieved")
        self.assertEqual(response.requested_tool, "get_report")
        self.assertEqual(response.report_job_id, report_job_id)
        self.assertEqual(response.data["status"], "completed")

    def test_natural_language_analysis_report_is_routed_to_report_agent(self):
        workflow = self.workflow

        response = workflow.invoke(self.request_model(message="请生成本次分析报告"))

        self.assertEqual(response.intent, "report")
        self.assertEqual(response.status, "needs_job")
        self.assertEqual(response.requested_tool, "create_report")
        self.assertIsNone(response.job_id)

    def test_quality_request_uses_authorized_asset_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(check_quality=lambda asset_id: {"recommended_to_retake": False, "issues": []})
        response = workflow.invoke(self.request_model(message="检查图片质量", asset_id=uuid4()))

        self.assertEqual(response.status, "quality_reported")
        self.assertEqual(response.requested_tool, "quality_check")

    def test_quality_request_can_use_analysis_job_reference(self):
        from app.agent.workflow import AgentWorkflow

        job_id = uuid4()
        workflow = AgentWorkflow(
            check_job_quality=lambda requested_id: {"recommended_to_retake": False, "issues": []}
        )
        response = workflow.invoke(
            self.request_model(message="检查任务图片质量", analysis_job_id=job_id)
        )

        self.assertEqual(response.status, "quality_reported")
        self.assertEqual(response.requested_tool, "quality_check")
        self.assertEqual(response.job_id, job_id)

    def test_poor_quality_blocks_submission_until_confirmed(self):
        from app.agent.workflow import AgentWorkflow

        expected_job_id = uuid4()
        workflow = AgentWorkflow(
            submit_analysis=lambda asset_id: expected_job_id,
            check_quality=lambda asset_id: {"recommended_to_retake": True, "issues": ["图片偏暗"]},
        )
        asset_id = uuid4()
        blocked = workflow.invoke(self.request_model(message="分析图片", asset_id=asset_id, confirm_submission=True))
        confirmed = workflow.invoke(
            self.request_model(message="分析图片", asset_id=asset_id, confirm_quality_warning=True, confirm_submission=True)
        )

        self.assertEqual(blocked.status, "quality_warning")
        self.assertEqual(confirmed.status, "submitted")

    def test_history_request_uses_read_only_data_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(search_history=lambda query: {"count": 2, "items": []})
        response = workflow.invoke(self.request_model(message="查询历史记录", history_query="N0"))

        self.assertEqual(response.status, "data_retrieved")
        self.assertEqual(response.requested_tool, "search_history")
        self.assertEqual(response.data["count"], 2)

    def test_history_summary_uses_aggregate_metrics_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(summarize_history=lambda _request: {"sample_count": 3, "completed_count": 2})
        response = workflow.invoke(self.request_model(message="统计历史记录", data_mode="summary"))

        self.assertEqual(response.status, "data_summarized")
        self.assertEqual(response.requested_tool, "aggregate_metrics")

    def test_statistics_request_routes_to_read_only_data_tool(self):
        from app.agent.workflow import AgentWorkflow

        workflow = AgentWorkflow(summarize_history=lambda _request: {"sample_count": 1, "completed_count": 1})
        response = workflow.invoke(self.request_model(message="统计最近样本"))

        self.assertEqual(response.status, "data_summarized")
        self.assertEqual(response.requested_tool, "aggregate_metrics")


if __name__ == "__main__":
    unittest.main()

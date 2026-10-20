"""The first bounded LangGraph workflow for phenotyping requests."""

from collections.abc import Callable
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from .models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentIntent,
    AgentResponseStatus,
    AgentRole,
    ToolName,
)
from .policy import ToolPolicy
from .skills import SkillRegistry


class AgentWorkflowState(TypedDict, total=False):
    """Internal state passed between the supervisor and phenotyping nodes."""

    request: AgentChatRequest
    route: str
    response: AgentChatResponse


SubmitAnalysis = Callable[[UUID], UUID]
SubmitAnalysisWithTrace = Callable[[UUID, UUID], UUID]
AnswerKnowledge = Callable[[str], tuple[str, list[str]]]
CreateReview = Callable[[UUID, str], UUID]
CreateReviewWithTrace = Callable[[UUID, str, UUID], UUID]
GenerateReport = Callable[[UUID, str], str]
SubmitReportWithTrace = Callable[[UUID, str, UUID], dict]
CheckQuality = Callable[[UUID], dict]
CheckJobQuality = Callable[[UUID], dict]
SearchHistory = Callable[[str], dict]
SummarizeHistory = Callable[[AgentChatRequest], dict]
GetAnalysisResult = Callable[[UUID], dict]
GetAnalysisEvidence = Callable[[UUID, str], dict]
GetReview = Callable[[UUID], dict]
GetReport = Callable[[UUID], dict]
ResolveReview = Callable[[UUID, str, dict], dict]
ResolveReviewWithTrace = Callable[[UUID, str, dict, UUID], dict]
SubmitBatchAnalysis = Callable[[list[UUID]], dict]
SubmitBatchAnalysisWithTrace = Callable[[list[UUID], UUID, str], dict]
GetBatchProgress = Callable[[UUID], dict]


class AgentWorkflow:
    """Routes the first v0.1 Agent intent without exposing unrestricted tools."""

    def __init__(self, submit_analysis: SubmitAnalysis | None = None, answer_knowledge: AnswerKnowledge | None = None, create_review: CreateReview | None = None, generate_report: GenerateReport | None = None, check_quality: CheckQuality | None = None, search_history: SearchHistory | None = None, summarize_history: SummarizeHistory | None = None, get_analysis_result: GetAnalysisResult | None = None, get_review: GetReview | None = None, get_report: GetReport | None = None, resolve_review: ResolveReview | None = None, check_job_quality: CheckJobQuality | None = None, submit_batch_analysis: SubmitBatchAnalysis | None = None, get_batch_progress: GetBatchProgress | None = None, submit_analysis_with_trace: SubmitAnalysisWithTrace | None = None, submit_batch_analysis_with_trace: SubmitBatchAnalysisWithTrace | None = None, create_review_with_trace: CreateReviewWithTrace | None = None, resolve_review_with_trace: ResolveReviewWithTrace | None = None, submit_report_with_trace: SubmitReportWithTrace | None = None, get_analysis_evidence: GetAnalysisEvidence | None = None):
        self._submit_analysis = submit_analysis
        self._submit_analysis_with_trace = submit_analysis_with_trace
        self._answer_knowledge = answer_knowledge
        self._create_review = create_review
        self._create_review_with_trace = create_review_with_trace
        self._generate_report = generate_report
        self._submit_report_with_trace = submit_report_with_trace
        self._check_quality = check_quality
        self._search_history = search_history
        self._summarize_history = summarize_history
        self._get_analysis_result = get_analysis_result
        self._get_analysis_evidence = get_analysis_evidence
        self._get_review = get_review
        self._get_report = get_report
        self._resolve_review = resolve_review
        self._resolve_review_with_trace = resolve_review_with_trace
        self._check_job_quality = check_job_quality
        self._submit_batch_analysis = submit_batch_analysis
        self._submit_batch_analysis_with_trace = submit_batch_analysis_with_trace
        self._get_batch_progress = get_batch_progress
        self._graph = self._build_graph()

    def invoke(self, request: AgentChatRequest) -> AgentChatResponse:
        """Run the bounded workflow and return its schema-validated response."""
        result = self._graph.invoke({"request": request})
        response = result["response"]
        skill = SkillRegistry.for_intent(response.intent)
        return response.model_copy(
            update={
                "trace_id": request.trace_id,
                "skill_name": skill.name if skill else None,
                "skill_version": skill.version if skill else None,
            }
        )

    def _build_graph(self):
        graph = StateGraph(AgentWorkflowState)
        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("phenotyping", self._phenotyping_node)
        graph.add_node("analysis_result", self._analysis_result_node)
        graph.add_node("batch", self._batch_node)
        graph.add_node("knowledge", self._knowledge_node)
        graph.add_node("review", self._review_node)
        graph.add_node("report", self._report_node)
        graph.add_node("quality", self._quality_node)
        graph.add_node("data", self._data_node)
        graph.add_node("respond", self._respond_node)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {"phenotyping": "phenotyping", "analysis_result": "analysis_result", "batch": "batch", "knowledge": "knowledge", "review": "review", "report": "report", "quality": "quality", "data": "data", "respond": "respond"},
        )
        graph.add_edge("phenotyping", END)
        graph.add_edge("analysis_result", END)
        graph.add_edge("batch", END)
        graph.add_edge("knowledge", END)
        graph.add_edge("review", END)
        graph.add_edge("report", END)
        graph.add_edge("quality", END)
        graph.add_edge("data", END)
        graph.add_edge("respond", END)
        return graph.compile()

    @staticmethod
    def _supervisor_node(state: AgentWorkflowState) -> AgentWorkflowState:
        request = state["request"]
        intent = request.intent or AgentWorkflow._classify_intent(request.message)

        if intent is AgentIntent.PHENOTYPING and request.analysis_job_id is not None:
            return {"route": "analysis_result"}

        if intent is AgentIntent.BATCH_ANALYSIS:
            if request.batch_id is not None or request.asset_ids:
                return {"route": "batch"}
            return {"route": "respond", "response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.NEEDS_ASSET, message="请提供需要批量分析的 asset_ids，或提供 batch_id 查询进度。", requested_tool=ToolName.SUBMIT_BATCH_ANALYSIS)}

        if intent is AgentIntent.PHENOTYPING and request.asset_id is None:
            return {
                "route": "respond",
                "response": AgentChatResponse(
                    intent=AgentIntent.PHENOTYPING,
                    status=AgentResponseStatus.NEEDS_ASSET,
                    message="请提供已上传且已授权的图片 asset_id 后再提交分析。",
                    requested_tool=ToolName.SUBMIT_ANALYSIS,
                ),
            }

        if intent is AgentIntent.PHENOTYPING:
            return {"route": "phenotyping"}

        if intent is AgentIntent.KNOWLEDGE_QA:
            return {"route": "knowledge"}

        if intent is AgentIntent.REVIEW and request.review_id is None and request.analysis_job_id is None:
            return {"route": "respond", "response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.NEEDS_JOB, message="请提供需要复核的 analysis_job_id。", requested_tool=ToolName.CREATE_REVIEW)}

        if intent is AgentIntent.REVIEW:
            return {"route": "review"}

        if intent is AgentIntent.REPORT and request.report_job_id is not None:
            return {"route": "report"}

        if intent is AgentIntent.REPORT and request.analysis_job_id is None:
            return {"route": "respond", "response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.NEEDS_JOB, message="请提供需要生成报告的 analysis_job_id。", requested_tool=ToolName.CREATE_REPORT)}

        if intent is AgentIntent.REPORT:
            return {"route": "report"}

        if intent is AgentIntent.QUALITY_CHECK and request.asset_id is None and request.analysis_job_id is None:
            return {"route": "respond", "response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.NEEDS_ASSET, message="请提供待检查图片的 asset_id。", requested_tool=ToolName.QUALITY_CHECK)}

        if intent is AgentIntent.QUALITY_CHECK:
            return {"route": "quality"}

        if intent is AgentIntent.DATA_ANALYSIS:
            return {"route": "data"}

        return {
            "route": "respond",
            "response": AgentChatResponse(
                intent=AgentIntent.UNSUPPORTED,
                status=AgentResponseStatus.UNSUPPORTED,
                message="v0.1 当前仅支持受控的图片表型分析请求。",
            ),
        }

    def _phenotyping_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        ToolPolicy.assert_allowed(AgentRole.PHENOTYPING, ToolName.SUBMIT_ANALYSIS)
        asset_id = state["request"].asset_id
        if asset_id is None:
            raise ValueError("Phenotyping node requires an asset_id.")

        if not state["request"].confirm_submission:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.PHENOTYPING,
                    status=AgentResponseStatus.CONFIRMATION_REQUIRED,
                    message="分析任务会调用视觉模型并产生任务记录。请将 confirm_submission 设为 true 后提交。",
                    requested_tool=ToolName.SUBMIT_ANALYSIS,
                )
            }

        if self._check_quality is not None:
            quality = self._check_quality(asset_id)
            if quality["recommended_to_retake"] and not state["request"].confirm_quality_warning:
                return {
                    "response": AgentChatResponse(
                        intent=AgentIntent.PHENOTYPING,
                        status=AgentResponseStatus.QUALITY_WARNING,
                        message="图片质量可能影响角果计数。请重拍，或将 confirm_quality_warning 设为 true 后明确继续分析。",
                        requested_tool=ToolName.QUALITY_CHECK,
                        quality=quality,
                    )
                }

        if self._submit_analysis is None:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.PHENOTYPING,
                    status=AgentResponseStatus.INTEGRATION_PENDING,
                    message=(
                        "表型分析工作流已完成参数校验；异步分析任务适配器尚未接入，"
                        "因此本次不会创建虚假的任务记录。"
                    ),
                    requested_tool=ToolName.SUBMIT_ANALYSIS,
                )
            }

        job_id = (self._submit_analysis_with_trace(asset_id, state["request"].trace_id, state["request"].project_id)
                  if self._submit_analysis_with_trace is not None
                  else self._submit_analysis(asset_id))
        return {
            "response": AgentChatResponse(
                intent=AgentIntent.PHENOTYPING,
                status=AgentResponseStatus.SUBMITTED,
                message=f"表型分析任务已提交：{job_id}",
                requested_tool=ToolName.SUBMIT_ANALYSIS,
                job_id=job_id,
            )
        }

    def _knowledge_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        ToolPolicy.assert_allowed(AgentRole.KNOWLEDGE, ToolName.RETRIEVE_KNOWLEDGE)
        if self._answer_knowledge is None:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.KNOWLEDGE_QA,
                    status=AgentResponseStatus.INTEGRATION_PENDING,
                    message="知识问答已配置，但语言模型调用尚未启用。",
                    requested_tool=ToolName.RETRIEVE_KNOWLEDGE,
                )
            }
        try:
            answer, citations = self._answer_knowledge(state["request"].message, state["request"].project_id)
        except TypeError as error:
            if "positional" not in str(error):
                raise
            answer, citations = self._answer_knowledge(state["request"].message)
        return {
            "response": AgentChatResponse(
                intent=AgentIntent.KNOWLEDGE_QA,
                status=AgentResponseStatus.ANSWERED,
                message=answer,
                requested_tool=ToolName.RETRIEVE_KNOWLEDGE,
                citations=citations,
            )
        }

    def _analysis_result_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        """读取权威分析结果，禁止由 Agent 自行生成计数事实。"""
        ToolPolicy.assert_allowed(AgentRole.PHENOTYPING, ToolName.GET_ANALYSIS_RESULT)
        job_id = state["request"].analysis_job_id
        if job_id is None:
            raise ValueError("Analysis result node requires an analysis_job_id.")
        if self._get_analysis_result is None:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.PHENOTYPING,
                    status=AgentResponseStatus.INTEGRATION_PENDING,
                    message="分析结果读取服务尚未接入。",
                    requested_tool=ToolName.GET_ANALYSIS_RESULT,
                )
            }
        try:
            try:
                result = self._get_analysis_result(job_id, state["request"].project_id)
            except TypeError:
                result = self._get_analysis_result(job_id)
        except (KeyError, ValueError) as error:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.PHENOTYPING,
                    status=AgentResponseStatus.NEEDS_JOB,
                    message=str(error),
                    requested_tool=ToolName.GET_ANALYSIS_RESULT,
                )
            }
        return {
            "response": AgentChatResponse(
                intent=AgentIntent.PHENOTYPING,
                status=AgentResponseStatus.RESULT_RETRIEVED,
                message="已读取该任务的权威分析结果。",
                requested_tool=ToolName.GET_ANALYSIS_RESULT,
                job_id=job_id,
                data=self._evidence_or_result(job_id, state["request"].project_id, result),
            )
        }

    def _evidence_or_result(self, job_id: UUID, project_id: str, result: dict) -> dict:
        """优先返回经过项目范围校验的证据包，旧接线退化为原始结果。"""
        if self._get_analysis_evidence is None:
            return result
        return self._get_analysis_evidence(job_id, project_id)

    def _batch_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        """提交已确认批量任务或读取现有批量任务进度。"""
        request = state["request"]
        if request.batch_id is not None:
            ToolPolicy.assert_allowed(AgentRole.PHENOTYPING, ToolName.GET_BATCH_PROGRESS)
            if self._get_batch_progress is None:
                return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.INTEGRATION_PENDING, message="批量进度读取服务尚未接入。", requested_tool=ToolName.GET_BATCH_PROGRESS)}
            try:
                batch = self._get_batch_progress(request.batch_id)
            except KeyError:
                return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.NEEDS_JOB, message="批量任务不存在。", requested_tool=ToolName.GET_BATCH_PROGRESS)}
            return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.BATCH_RETRIEVED, message="已读取批量分析任务进度。", requested_tool=ToolName.GET_BATCH_PROGRESS, batch=batch)}

        ToolPolicy.assert_allowed(AgentRole.PHENOTYPING, ToolName.SUBMIT_BATCH_ANALYSIS)
        if not request.confirm_batch_submission:
            return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.CONFIRMATION_REQUIRED, message="批量分析会创建多个任务并消耗模型预算。请将 confirm_batch_submission 设为 true 后提交。", requested_tool=ToolName.SUBMIT_BATCH_ANALYSIS)}
        if self._submit_batch_analysis is None and self._submit_batch_analysis_with_trace is None:
            return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.INTEGRATION_PENDING, message="批量分析服务尚未接入。", requested_tool=ToolName.SUBMIT_BATCH_ANALYSIS)}
        try:
            batch = (self._submit_batch_analysis_with_trace(request.asset_ids, request.trace_id, request.project_id)
                     if self._submit_batch_analysis_with_trace is not None
                     else self._submit_batch_analysis(request.asset_ids))
        except (KeyError, ValueError) as error:
            return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.NEEDS_ASSET, message=str(error), requested_tool=ToolName.SUBMIT_BATCH_ANALYSIS)}
        return {"response": AgentChatResponse(intent=AgentIntent.BATCH_ANALYSIS, status=AgentResponseStatus.BATCH_SUBMITTED, message="批量分析任务已提交。", requested_tool=ToolName.SUBMIT_BATCH_ANALYSIS, batch=batch)}

    def _review_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        review_id = state["request"].review_id
        request = state["request"]
        if review_id is not None and request.review_resolution is not None:
            ToolPolicy.assert_allowed(AgentRole.REVIEW, ToolName.RESOLVE_REVIEW)
            if request.revision_payload is None:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_UNAVAILABLE, message="提交复核结论必须同时提供 revision_payload。", requested_tool=ToolName.RESOLVE_REVIEW)}
            if not request.confirm_review_resolution:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.CONFIRMATION_REQUIRED, message="复核结案会写入新的结果版本。请将 confirm_review_resolution 设为 true 后提交。", requested_tool=ToolName.RESOLVE_REVIEW)}
            if self._resolve_review is None and self._resolve_review_with_trace is None:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.INTEGRATION_PENDING, message="复核结案服务尚未接入。", requested_tool=ToolName.RESOLVE_REVIEW)}
            try:
                if self._resolve_review_with_trace is not None:
                    try:
                        resolved = self._resolve_review_with_trace(review_id, request.review_resolution, request.revision_payload, request.trace_id, request.project_id)
                    except TypeError:
                        resolved = self._resolve_review_with_trace(review_id, request.review_resolution, request.revision_payload, request.trace_id)
                else:
                    resolved = self._resolve_review(review_id, request.review_resolution, request.revision_payload)
            except (KeyError, ValueError) as error:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_UNAVAILABLE, message=str(error), requested_tool=ToolName.RESOLVE_REVIEW)}
            return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_RESOLVED, message="复核结论已写入新的结果版本并结案。", requested_tool=ToolName.RESOLVE_REVIEW, review_id=review_id, data=resolved)}

        if review_id is not None:
            ToolPolicy.assert_allowed(AgentRole.REVIEW, ToolName.GET_REVIEW)
            if self._get_review is None:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.INTEGRATION_PENDING, message="复核状态读取服务尚未接入。", requested_tool=ToolName.GET_REVIEW)}
            try:
                review = self._get_review(review_id)
            except KeyError:
                return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_UNAVAILABLE, message="复核任务不存在。", requested_tool=ToolName.GET_REVIEW)}
            return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_RETRIEVED, message="已读取复核任务状态。", requested_tool=ToolName.GET_REVIEW, review_id=review_id, data=review)}

        ToolPolicy.assert_allowed(AgentRole.REVIEW, ToolName.CREATE_REVIEW)
        job_id = state["request"].analysis_job_id
        if job_id is None:
            raise ValueError("Review node requires an analysis_job_id.")
        if not state["request"].confirm_review:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.REVIEW,
                    status=AgentResponseStatus.CONFIRMATION_REQUIRED,
                    message="创建人工复核任务会写入复核队列。请将 confirm_review 设为 true 后提交。",
                    requested_tool=ToolName.CREATE_REVIEW,
                )
            }
        if self._create_review is None:
            return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.INTEGRATION_PENDING, message="复核工作流已就绪，但复核任务服务尚未接入。", requested_tool=ToolName.CREATE_REVIEW)}
        try:
            review_id = (self._create_review_with_trace(job_id, state["request"].message, state["request"].trace_id)
                         if self._create_review_with_trace is not None
                         else self._create_review(job_id, state["request"].message))
        except ValueError as error:
            return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_UNAVAILABLE, message=str(error), requested_tool=ToolName.CREATE_REVIEW)}
        return {"response": AgentChatResponse(intent=AgentIntent.REVIEW, status=AgentResponseStatus.REVIEW_CREATED, message=f"复核任务已创建：{review_id}", requested_tool=ToolName.CREATE_REVIEW, review_id=review_id)}

    def _report_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        report_job_id = state["request"].report_job_id
        if report_job_id is not None:
            ToolPolicy.assert_allowed(AgentRole.REPORT, ToolName.GET_REPORT)
            if self._get_report is None:
                return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.INTEGRATION_PENDING, message="报告状态读取服务尚未接入。", requested_tool=ToolName.GET_REPORT)}
            try:
                report = self._get_report(report_job_id)
            except KeyError:
                return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.REPORT_UNAVAILABLE, message="报告任务不存在。", requested_tool=ToolName.GET_REPORT)}
            return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.REPORT_RETRIEVED, message="已读取报告任务状态。", requested_tool=ToolName.GET_REPORT, report_job_id=report_job_id, data=report)}

        ToolPolicy.assert_allowed(AgentRole.REPORT, ToolName.CREATE_REPORT)
        job_id = state["request"].analysis_job_id
        if job_id is None:
            raise ValueError("Report node requires an analysis_job_id.")
        if not state["request"].confirm_report:
            return {
                "response": AgentChatResponse(
                    intent=AgentIntent.REPORT,
                    status=AgentResponseStatus.CONFIRMATION_REQUIRED,
                    message="创建报告会启动导出任务并调用模型。请将 confirm_report 设为 true 后提交。",
                    requested_tool=ToolName.CREATE_REPORT,
                )
            }
        if self._submit_report_with_trace is not None:
            report_job = self._submit_report_with_trace(job_id, state["request"].message, state["request"].trace_id)
            return {"response": AgentChatResponse(
                intent=AgentIntent.REPORT, status=AgentResponseStatus.REPORT_SUBMITTED,
                message="报告任务已提交，可通过 report_job_id 查询状态。",
                requested_tool=ToolName.CREATE_REPORT, job_id=job_id,
                report_job_id=UUID(str(report_job["report_job_id"])), data=report_job,
            )}
        if self._generate_report is None:
            return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.INTEGRATION_PENDING, message="报告能力已配置，但语言模型调用尚未启用。", requested_tool=ToolName.CREATE_REPORT)}
        try:
            report = self._generate_report(job_id, state["request"].message)
        except ValueError as error:
            return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.REPORT_UNAVAILABLE, message=str(error), requested_tool=ToolName.CREATE_REPORT)}
        return {"response": AgentChatResponse(intent=AgentIntent.REPORT, status=AgentResponseStatus.REPORT_GENERATED, message=report, requested_tool=ToolName.CREATE_REPORT, job_id=job_id)}

    def _quality_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        ToolPolicy.assert_allowed(AgentRole.PHENOTYPING, ToolName.QUALITY_CHECK)
        request = state["request"]
        if request.analysis_job_id is not None:
            if self._check_job_quality is None:
                return {"response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.INTEGRATION_PENDING, message="任务级图像质量检查服务尚未接入。", requested_tool=ToolName.QUALITY_CHECK)}
            try:
                quality = self._check_job_quality(request.analysis_job_id)
            except (KeyError, ValueError) as error:
                return {"response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.NEEDS_JOB, message=str(error), requested_tool=ToolName.QUALITY_CHECK)}
            message = "建议重拍后再分析。" if quality["recommended_to_retake"] else "图片质量满足分析建议。"
            return {"response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.QUALITY_REPORTED, message=message, requested_tool=ToolName.QUALITY_CHECK, job_id=request.analysis_job_id, quality=quality)}

        asset_id = request.asset_id
        if asset_id is None:
            raise ValueError("Quality node requires an asset_id or analysis_job_id.")
        if self._check_quality is None:
            return {"response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.INTEGRATION_PENDING, message="图像质量检查服务尚未接入。", requested_tool=ToolName.QUALITY_CHECK)}
        quality = self._check_quality(asset_id)
        message = "建议重拍后再分析。" if quality["recommended_to_retake"] else "图片质量满足分析建议。"
        return {"response": AgentChatResponse(intent=AgentIntent.QUALITY_CHECK, status=AgentResponseStatus.QUALITY_REPORTED, message=message, requested_tool=ToolName.QUALITY_CHECK, quality=quality)}

    def _data_node(self, state: AgentWorkflowState) -> AgentWorkflowState:
        request = state["request"]
        summary_keywords = ("统计", "汇总", "平均", "均值", "样本数")
        requires_summary = request.data_mode == "summary" or any(
            keyword in request.message.lower() for keyword in summary_keywords
        )
        if requires_summary:
            ToolPolicy.assert_allowed(AgentRole.DATA_ANALYST, ToolName.AGGREGATE_METRICS)
            if self._summarize_history is None:
                return {"response": AgentChatResponse(intent=AgentIntent.DATA_ANALYSIS, status=AgentResponseStatus.INTEGRATION_PENDING, message="历史统计服务尚未接入。", requested_tool=ToolName.AGGREGATE_METRICS)}
            try:
                data = self._summarize_history(request.history_query, request.project_id)
            except TypeError:
                data = self._summarize_history(request)
            scope = f"文件名匹配“{request.history_query}”的记录" if request.history_query else "全部本地历史记录"
            return {"response": AgentChatResponse(intent=AgentIntent.DATA_ANALYSIS, status=AgentResponseStatus.DATA_SUMMARIZED, message=f"统计范围为{scope}：共有 {data['sample_count']} 条记录，其中完成 {data['completed_count']} 条。", requested_tool=ToolName.AGGREGATE_METRICS, data=data)}

        ToolPolicy.assert_allowed(AgentRole.DATA_ANALYST, ToolName.SEARCH_HISTORY)
        if self._search_history is None:
            return {"response": AgentChatResponse(intent=AgentIntent.DATA_ANALYSIS, status=AgentResponseStatus.INTEGRATION_PENDING, message="历史查询服务尚未接入。", requested_tool=ToolName.SEARCH_HISTORY)}
        try:
            data = self._search_history(request.history_query, request.project_id)
        except TypeError:
            data = self._search_history(request.history_query)
        return {"response": AgentChatResponse(intent=AgentIntent.DATA_ANALYSIS, status=AgentResponseStatus.DATA_RETRIEVED, message=f"已查询到 {data.get('count', 0)} 条历史记录。", requested_tool=ToolName.SEARCH_HISTORY, data=data)}

    @staticmethod
    def _respond_node(state: AgentWorkflowState) -> AgentWorkflowState:
        return state

    @staticmethod
    def _route_after_supervisor(state: AgentWorkflowState) -> str:
        return state["route"]

    @staticmethod
    def _classify_intent(message: str) -> AgentIntent:
        normalized_message = message.lower()
        phenotyping_keywords = ("分析", "识别", "计数", "表型", "analyze")
        knowledge_keywords = (
            "什么", "如何", "为什么", "说明", "知识", "资料", "sop", "规范",
            "拍摄", "背景", "标尺", "角果", "主枝", "主干", "枯枝",
        )
        review_keywords = ("复核", "确认结果", "人工确认", "审核")
        report_keywords = ("报告", "解读结果", "总结结果")
        quality_keywords = ("质量检查", "图片质量", "是否清晰", "是否需要重拍")
        data_keywords = ("历史", "最近分析", "查询记录", "历史记录", "统计", "汇总", "平均", "均值", "样本数")
        batch_keywords = ("批量", "一批", "批次")
        question_markers = ("什么", "如何", "为什么", "多少", "吗", "？", "?", "要求", "规范")
        if any(keyword in normalized_message for keyword in knowledge_keywords) and any(marker in normalized_message for marker in question_markers):
            return AgentIntent.KNOWLEDGE_QA
        if any(keyword in normalized_message for keyword in report_keywords):
            return AgentIntent.REPORT
        if any(keyword in normalized_message for keyword in quality_keywords):
            return AgentIntent.QUALITY_CHECK
        if any(keyword in normalized_message for keyword in batch_keywords):
            return AgentIntent.BATCH_ANALYSIS
        if any(keyword in normalized_message for keyword in data_keywords):
            return AgentIntent.DATA_ANALYSIS
        if any(keyword in normalized_message for keyword in review_keywords):
            return AgentIntent.REVIEW
        if any(keyword in normalized_message for keyword in phenotyping_keywords):
            return AgentIntent.PHENOTYPING
        if any(keyword in normalized_message for keyword in knowledge_keywords):
            return AgentIntent.KNOWLEDGE_QA
        return AgentIntent.UNSUPPORTED

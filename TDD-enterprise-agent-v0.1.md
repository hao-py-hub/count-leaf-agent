# 油菜智能考种 Agent 平台技术设计文档

**版本：** v0.1  
**状态：** v0.1 本地单用户实现完成，企业化能力规划中  
**对应产品文档：** `PRD-enterprise-agent.md`  
**目标分支：** `codex/agent-backend-redesign`

> **实现状态说明（2026-08-19）：** 本文同时记录当前 v0.1 实现与企业目标态。除非明确标为“后续企业化”，均以当前本地单用户实现为准：SQLite 持久化、SQLite FTS5 RAG、进程内异步执行器、LangGraph 固定路由、REST/Function Calling 与本地 stdio MCP 已完成。OIDC/RBAC、多租户、Qdrant、对象存储、生产 Redis/Celery 集群与 OTel/Prometheus 尚未实施。

## 1. 目标与范围

本设计将现有“豆包视觉模型 + OpenCV 果柄计数”的固定分析流水线升级为**单用户、可审计的研究助手**。v0.1 的核心是：让受控工作流处理意图理解、任务编排、知识问答、质量复核和报告；让既有表型分析服务继续作为唯一的图像计数权威。

### 1.1 v0.1 必须实现

- 保留现有图像分析 API、SSE 步骤图、SQLite 历史和人工修订兼容性。
- 提供一个受控的能力节点编排图，并可通过 REST 调用。
- 将现有分析流水线封装成异步、可查询、可审计的业务工具。
- 接入 RAG，检索考种 SOP、拍摄规范、指标定义、项目规则和常见错误处理文档。
- 提供本地数据边界、隐私友好审计记录、任务预算和人工复核闭环；RBAC 延后。
- 允许企业上层 Agent 通过 OpenAI Function Calling 风格 REST 工具协议接入；MCP Server 作为适配层实现。

### 1.2 v0.1 不做

- 不重训视觉模型，不更换 `stalk` 果柄计数法。
- 不允许 Agent 自行根据描述生成角果数量或修改科研原始结果。
- 不建设无限制的自治多 Agent 对话、任意代码执行、通用网络搜索能力。
- 不在首期引入跨组织数据分析、自动育种推荐或自动发布科研结论。

## 2. 总体设计原则

1. **工具优先。** 数值结论必须来自可追溯工具输出，而非 LLM 文本生成。
2. **受控能力节点。** 采用固定有向图和明确职责，禁止自由 Agent 互聊、无限循环或共享密钥。
3. **检索有证据。** RAG 的回答必须带文档来源、版本和段落引用；检索不到时明确说明。
4. **单用户默认。** 本地数据仅由当前项目所有者操作；后续多人模式才由服务端可信身份上下文决定范围。
5. **异步长任务。** 图像/批量分析不在 HTTP 请求线程中完成，统一由 Worker 执行。
6. **人工可接管。** 低置信度、异常统计、写入性操作必须进入确认或复核流程。
7. **可回放。** 每一条 Agent 回复应关联会话、状态版本、工具调用、检索证据、模型和算法版本。

## 3. 推荐技术栈

| 层级 | v0.1 推荐 | 说明 |
| --- | --- | --- |
| Web/API | FastAPI + Uvicorn | 复用现有服务与 SSE 能力 |
| Agent 编排 | LangGraph + `langchain-core` | 用显式状态图实现主管/专业 Agent 路由、检查点与人工中断 |
| 模型调用 | OpenAI Python SDK | 继续使用豆包方舟 OpenAI 兼容 API；模型 ID/Endpoint 环境化 |
| 工具协议 | Pydantic JSON Schema + REST；MCP Server 适配 | 内部工具一次定义，多入口复用 |
| RAG 编排 | 自研轻薄 Retrieval Service | 当前使用 SQLite FTS5；提供文本分块、关键词检索、引用组装与本地术语扩展 |
| 向量数据库 | 后续可选 Qdrant | 当前不部署；待真实评测集证明 FTS5 召回不足后再引入 metadata 过滤、向量检索与重排 |
| 主业务数据库 | PostgreSQL + SQLAlchemy 2.x + Alembic | 企业部署主选；SQLite 保留为本地开发回退 |
| 异步任务 | Redis + Celery | 可靠任务、重试、取消、定时与可观测性成熟 |
| 文件存储 | 本地卷（开发）/ S3 兼容对象存储（生产） | 原图、步骤图、报告和知识文件分离 |
| 身份认证 | OIDC/OAuth2 JWT | 可对接企业 IdP 或 API Gateway |
| 观测 | OpenTelemetry + Prometheus + 结构化日志 | Trace 贯穿 HTTP、Agent、工具和 Worker |
| 前端 | 现有原生单页逐步扩展 | 新增聊天、任务中心、复核队列；无需首期重写框架 |

> LangGraph 是推荐框架而非业务依赖前提。所有 Agent、状态、工具 schema 和提示词必须封装在 `app/agent/`，算法层 `app/pipeline/` 不依赖 LangGraph。

## 4. 目标架构

```text
企业门户 / 当前 Web 前端 / 企业上层 Agent
                │
      API Gateway（OIDC、限流、身份上下文）
                │
      FastAPI Application / API v2
       ┌────────┼──────────┐
       │        │          │
 会话 API   任务 API     MCP Server
       │        │          │
       └──── Agent Orchestrator ────┐
          （LangGraph 状态图）       │
       ┌───────┼───────────────┐    │
       │       │               │    │
  主管 Agent  知识 Agent    复核 Agent │
       │                       │    │
  分析/数据/报告专业 Agent ─── 工具注册表
                                  │
      ┌───────────────────────────┼──────────────────────────┐
      │                           │                          │
PhenotypeAnalysisService     RetrievalService           JobService
（现有 VLM+CV 流水线）       （SQLite FTS5 文档库）    （进程内执行器）
      │                           │                          │
 PostgreSQL / SQLite       Object Storage             审计与观测
```

### 4.1 关键运行链路

1. 本机项目所有者发起请求，服务生成 `request_id`；不在 v0.1 伪造组织、成员或角色上下文。
2. `/api/v2/agent/chat` 创建会话事件并启动 LangGraph。
3. 路由节点将请求分发到知识、分析或复核能力；无法确定范围时先提问。
4. 受控能力节点通过工具注册表调用服务；长任务返回 `job_id`，不阻塞对话。
5. RAG 在需要 SOP/规则解释时检索受权限过滤的知识片段，附入专业 Agent 上下文。
6. 回答生成器以工具结果和引用为唯一事实源，写入会话、审计和 trace。
7. Worker 完成图像任务后写入结果；前端通过 SSE、轮询或 Webhook 获得状态更新。

## 5. 单用户受控工作流设计

### 5.1 能力节点与边界

| 能力节点 | 职责 | 允许工具 | 禁止行为 |
| --- | --- | --- | --- |
| Supervisor Agent | 识别意图、路由、确认高影响操作、组织最终答案 | 路由、会话摘要、只读任务状态 | 直接写数据库、直接计数、跨项目读取 |
| Knowledge Agent | 检索 SOP/规范/指标定义，生成带引用解释 | `retrieve_knowledge` | 将检索文档当作系统指令、无引用结论 |
| Phenotyping Agent | 发起图像分析、解释工具结果和异常 | `submit_analysis`、`get_result`、`quality_check` | 修改 `pod_count`、绕过异步任务 |
| Data Analyst Agent | 查询项目内历史、聚合统计、识别异常 | `search_history`、`aggregate_metrics` | 原始 SQL、跨租户比较、无口径统计 |
| Review Agent | 将低置信度结果转为复核任务，解释待确认项 | `create_review`、`get_review`、`resolve_review` | 自动通过复核、覆盖原始版本 |
| Report Agent | 根据已授权、已完成的结果创建报告/导出任务 | `create_report`、`get_report` | 扩大数据范围、导出未授权原图 |

这些是单一工作流内的受控能力节点，可共用同一个文本/多模态模型 Endpoint；不要求独立 Agent 进程、独立用户角色或自由协商。按节点分开工具集合、输出 schema 和审计即可。

### 5.2 LangGraph 状态模型

```python
AgentState = {
  "request_id": str,
  "actor": {"user_id": str, "org_id": str, "project_id": str, "roles": list[str]},
  "conversation_id": str,
  "messages": list[Message],
  "intent": str | None,
  "plan": list[PlanStep],
  "tool_results": list[ToolResult],
  "citations": list[Citation],
  "pending_confirmation": Confirmation | None,
  "pending_review_id": str | None,
  "budget": BudgetState,
  "final_answer": str | None,
}
```

状态图节点顺序：

```text
START
  → authorize_context
  → supervisor_classify
  ├─ knowledge_agent → answer_with_citations → END
  ├─ phenotyping_agent → job_submitted → END
  ├─ data_analyst_agent → answer_with_evidence → END
  ├─ review_agent → awaiting_confirmation/review → END
  └─ report_agent → report_job_submitted → END
```

允许的回路仅有：`supervisor_classify → clarification_request → END`，以及工具失败后的有限重试（最多 1 次，且记录原因）。不得允许 Agent 在无新用户输入的情况下自由循环。

### 5.3 模型路由与预算

- **视觉分析工具：**使用配置的多模态模型 Endpoint；该调用沿用现有 `vlm_counter.py` 与 `pod_verify_vlm.py`。
- **Supervisor/Knowledge/Data/Report Agent：**默认使用成本较低的文本或多模态 Turbo 模型；若用户附图或需视觉解释再升级。
- **JSON 结构化输出：**优先使用模型原生 JSON Schema/Structured Output；无此能力时使用 Pydantic 验证 + 最多一次修复请求。
- **预算拦截：**每次调用记录输入/输出 token、图片数与预估费用；项目达到阈值后只允许只读查询和人工批准后的分析。

环境变量建议：

```dotenv
AGENT_MODEL_ENDPOINT=ep-...
VISION_MODEL_ENDPOINT=ep-...
EMBEDDING_MODEL_ENDPOINT=ep-...
RERANK_MODEL_ENDPOINT=ep-... # 可选
AGENT_DAILY_BUDGET_CNY=200
PROJECT_MAX_CONCURRENT_JOBS=4
```

## 6. 工具层设计

### 6.1 工具通用约束

- 所有工具输入/输出使用 Pydantic 模型，自动生成 JSON Schema。
- 工具执行前由 `ToolPolicy` 验证角色、组织、项目、状态、预算和参数范围。
- 工具层使用服务对象，不让 Agent 接触 ORM session、原始 SQL、文件系统路径、API Key 或 Celery internals。
- 每个调用写入 `tool_call` 审计事件，保存参数脱敏摘要、耗时、结果引用和错误码。
- `tool_result` 只返回 Agent 需要的最小字段；原图/步骤图使用授权 URL 或资源 ID。

### 6.2 核心工具 Schema（示例）

```python
class SubmitAnalysisInput(BaseModel):
    asset_id: UUID
    requested_outputs: list[Literal["pods", "stem_length", "steps"]] = ["pods", "stem_length"]

class SubmitAnalysisOutput(BaseModel):
    job_id: UUID
    status: Literal["queued"]
    estimated_wait_seconds: int | None

class GetAnalysisResultInput(BaseModel):
    run_id: UUID

class GetAnalysisResultOutput(BaseModel):
    status: str
    total_pods: int | None
    main_inflorescence_pod_count: int | None
    branch_pod_count: int | None
    branch_count: int | None
    main_stem_length_cm: float | None
    quality_flags: list[str]
    result_version: int
    evidence_urls: list[AuthorizedResource]
```

### 6.3 现有流水线适配

现有 `analyze_plant_image_stream()` 产生生成器事件。新增适配器：

```text
Celery Task
  → AnalysisRunService.create_run()
  → 遍历 analyze_plant_image_stream()
  → 将 step/result/error 映射为 JobEvent + AnalysisRun
  → 持久化步骤资源与算法元数据
  → 发布任务状态事件
```

现有 `/api/analyze` 可在 v0.1 保留兼容路径，但内部应调用 `JobService.submit_analysis()`；新 API 只返回任务 ID，步骤由 `/api/v2/jobs/{job_id}/events` 推送。

### 6.4 v0.1 Worker 落地

- 默认 `JOB_EXECUTOR_MODE=in_process` 使用受限线程池，适合单机开发和既有工作台兼容路径。
- 设置 `JOB_EXECUTOR_MODE=celery` 后，API 仅创建 SQLite/PostgreSQL 持久化任务并投递 `phenotype.execute_analysis_job`；Celery Worker 按 `job_id` 重建服务、执行既有流水线并持久化状态和结果。
- Worker 与 API 必须共享 `DATABASE_URL`、上传/结果/数据卷和 Celery Broker 配置；完成任务后仍执行图片质量检查，并按规则创建人工复核任务。
- API 和 Worker 均不直接修改 `pod_count`；人工修订继续经由历史版本和复核结案接口产生新版本。

## 7. RAG 设计

### 7.1 知识范围

- 油菜考种 SOP、拍摄规范、标尺说明、样品标签规则。
- 角果数、分枝数、主干长度等指标定义和统计口径。
- 常见错误：黑底不足、植株重叠、标尺缺失、枯枝误报、裁剪不完整。
- 企业内部项目规则、品种/处理说明、审核流程。

不写入 RAG 的内容：原始大规模图片、未经脱敏的跨项目结果、API Key、数据库备份、Agent 提示词中的系统权限规则。

### 7.2 当前数据管道

```text
UTF-8 文本 / Markdown
  → 文件大小限制与 UTF-8 格式校验
  → 标题与正文校验
  → 固定长度分段（500 字符，80 overlap）
  → SQLite FTS5 `knowledge_chunks`
```

当前每个 chunk 保存 `document_id`、`chunk_id`、标题、正文与顺序号。下列 metadata 是后续企业化目标，不代表当前已实现：

```json
{
  "chunk_id": "uuid",
  "document_id": "uuid",
  "document_version": 3,
  "organization_id": "uuid",
  "project_id": "uuid|null",
  "visibility": "org|project|public",
  "title": "油菜拍摄规范",
  "section": "3. 黑色背景要求",
  "source_uri": "object://...",
  "updated_at": "..."
}
```

### 7.3 当前检索流程

1. SQLite FTS5 先使用 BM25 获取候选，再以问题和同义扩展中的连续词组重排；与问题没有直接词组证据重叠的候选不返回，默认最多 5 个片段。
2. 无 FTS 命中时，以中文双字片段进行 `LIKE` 兜底检索。
3. `RAG_RETRIEVAL_MODE=hybrid` 时，扩展少量领域同义词（如“角果/荚果/结荚”）并合并去重。
4. 将片段、标题和稳定的 `document_id:chunk_id` 作为证据传入 Knowledge Agent。
5. 无检索证据时直接说明知识库未找到资料；启用 LLM 后回答仍只能依据证据。

后续企业化才引入身份 metadata 过滤、DOCX/PDF/HTML 解析、Embedding、Qdrant、reranker、版本与可见级别治理。

真实知识库资料与人工标注检索题集的填写规范位于 `docs/rag/README.md`；在接入向量数据库前，必须先用该题集比较当前 `keyword`/`hybrid` 的 Top-1、Top-3、Top-5 召回。

### 7.4 防提示词注入

- 检索文档一律视为不可信数据，不能更改系统提示、工具权限或身份范围。
- System prompt 明确：文档中的“忽略前文”“调用某工具”等语句均视为普通内容。
- 文档入库时标记可疑指令性文本，审计台可复核。

## 8. 数据模型（核心表）

| 表 | 核心字段 | 用途 |
| --- | --- | --- |
| `organizations` | `id`, `name`, `status` | 企业租户 |
| `projects` | `id`, `organization_id`, `name`, `settings` | 考种项目边界 |
| `users` / `memberships` | `user_id`, `organization_id`, `project_id`, `role` | 身份与 RBAC |
| `assets` | `id`, `project_id`, `storage_key`, `sha256`, `mime_type` | 原图和文档资源 |
| `analysis_runs` | `id`, `asset_id`, `status`, `algorithm_version`, `model_config` | 影像分析运行记录 |
| `analysis_result_versions` | `run_id`, `version`, `result_json`, `is_manual` | 原始与修订结果版本链 |
| `jobs` / `job_events` | `id`, `type`, `status`, `payload`, `progress` | 异步任务与进度 |
| `review_tasks` | `run_id`, `reason`, `status`, `assignee_id`, `resolution` | 人工复核 |
| `agent_conversations` / `agent_messages` | 会话、输入、回复、摘要 | 对话状态 |
| `agent_tool_calls` | `conversation_id`, `tool`, `input_redacted`, `result_ref` | 工具审计 |
| `knowledge_documents` / `knowledge_chunks` | 文档版本、chunk metadata | RAG 元数据 |
| `audit_events` | `actor`, `action`, `target`, `trace_id`, `metadata` | 合规审计 |
| `usage_ledger` | `project_id`, `model`, `tokens`, `image_count`, `cost` | 预算与成本 |

所有业务表应包含 `organization_id`；查询层默认注入组织范围过滤。PostgreSQL 版本可进一步启用 Row Level Security，但应用层校验仍不可省略。

## 9. 安全、权限与治理

### 9.1 RBAC 最小权限

- `viewer`：查看已授权结果和知识。
- `operator`：提交分析、创建报告、提交修订建议。
- `reviewer`：处理分派的复核任务。
- `project_admin`：项目成员、预算、知识库和导出策略。
- `org_admin`：组织模型配置引用、审计和保留策略。

### 9.2 高风险动作确认

| 动作 | 处理方式 |
| --- | --- |
| 批量任务超过阈值 | Agent 展示范围、预估成本，要求确认 |
| 导出包含原图的数据 | 检查角色并记录导出审计 |
| 覆盖人工修订 | 禁止覆盖；仅允许产生新版本 |
| 删除资产/项目 | 只能由 API/UI 显式操作，Agent 只可创建待确认请求 |
| 模型预算超限 | 工具层硬拦截，管理员批准后解除 |

### 9.3 密钥管理

- 本地开发使用 `.env`；生产使用企业 Secret Manager/Kubernetes Secret 引用。
- Agent、日志、RAG 文档、前端 API 和审计表均不得出现原始 Key。
- VLM 请求日志只保存请求 ID、模型 Endpoint、耗时、token/图片计量和已脱敏错误信息。

## 10. API 与 MCP 适配

### 10.1 REST

- `POST /api/v2/agent/chat`：非流式回复或返回 `conversation_id` + `job_id`。
- `GET /api/v2/agent/conversations/{id}/events`：SSE 推送 Agent、工具和任务事件。
- `POST /api/v2/jobs/analysis`、`GET /api/v2/jobs/{id}`：任务 API。
- `POST /api/v2/reviews`、`POST /api/v2/reviews/{id}/resolve`：复核 API。
- `POST /api/v2/knowledge/documents`：受管理员权限控制的知识入库。
- `GET /api/health`、`GET /api/ready`：分别用于进程存活和非敏感依赖就绪探针。

### 10.2 MCP Server

本地 v0.1 MCP `stdio` 服务仅暴露低风险、结构化的只读工具：

```text
get_analysis_result
quality_check
search_history
aggregate_metrics
get_review
retrieve_knowledge
```

本机 v0.1 为当前项目所有者的受控接入，不伪造企业身份。后续企业部署必须将 MCP 请求映射到可信身份上下文；禁止匿名跨用户连接、任意路径读写、SQL、Shell、环境变量或原始数据库访问工具。提交分析、人工修订、复核结案和报告创建均不是 MCP 工具，必须经过现有 REST/UI 的显式确认。

MCP 与 REST Function Calling 的已执行调用均写入统一的脱敏工具审计，并在本机工作台“审计记录”中按 `mcp`、`function_call` 或 `agent_workflow` 来源展示；审计仅含参数哈希、状态、结果引用、耗时和 `trace_id`。

企业接入方必须先发现 Schema，再按 Schema 调用；不得把 MCP 的只读工具扩展为提交分析、修改结果、结案复核或任意文件/命令工具。所有计数事实只接受 `get_analysis_result` 返回值。

## 11. 可观测性与评测

### 11.1 Trace 与日志

一个 `trace_id` 必须贯穿：HTTP 请求 → LangGraph 节点 → RAG 检索 → 工具调用 → Celery 任务 → VLM/CV 分析 → 数据库写入。

关键指标：

- Agent 路由正确率、工具成功率、澄清率、工具重试率。
- RAG 命中率、引用覆盖率、无依据回答率、越权检索拦截数。
- 分析任务耗时、队列积压、VLM 失败率、人工复核率、算法回归误差。
- 每项目 token、图片调用数、成本、预算拒绝次数。

### 11.2 离线评测集

建立四类回归集：

1. **意图集：**单图、批量、历史、质量、报告、越权与模糊请求。
2. **RAG 集：**SOP 问答及要求正确来源的题目。
3. **工具集：**schema 校验、权限过滤、超预算、任务失败和重试。
4. **算法集：**现有带真值图片，验证 Agent 改造不影响 CV 计数。

上线门槛：所有高风险工具的未经确认写入率为 0；越权数据返回率为 0；带知识结论的引用覆盖率 ≥ 95%。

## 12. 部署拓扑

### 12.1 开发环境

Docker Compose：

```text
api + worker + redis + qdrant + postgres（可选）
```

保留当前 `cv-agent` 服务；新增依赖按 profile 启动，避免影响仅做图像分析的本地用户。

### 12.2 企业生产环境

```text
Ingress/API Gateway
  → API Pods（FastAPI）
  → Worker Pods（CPU 密集型 CV 与模型调用）
  → Redis HA
  → PostgreSQL HA
  → Qdrant Cluster
  → S3/对象存储
  → OTel Collector / Prometheus / 日志平台
```

Worker 按 CPU 与内存独立扩缩容。VLM API 调用使用并发/速率限制器，防止一次大批量任务触发供应商限流或预算失控。

## 13. 实施计划

| 迭代 | 重点 | 可验收产物 |
| --- | --- | --- |
| v0.1.0 | 已完成 | `PhenotypeAnalysisService`、SQLite 任务状态、现有 API 回归通过 |
| v0.1.1 | 已完成 | LangGraph 路由 + 表型能力节点、工具 schema、对话与审计 |
| v0.1.2 | 已完成 | SQLite FTS、文本/Markdown 入库、带引用问答、`keyword/hybrid` 模式与离线召回基线 |
| v0.1.3 | 已完成基础闭环 | 复核队列、报告解读、预算确认、图片质量检查与 Agent 离线质量评估；真实知识库/算法评测集待补 |
| 后续企业化 | 多人协作 | OIDC、RBAC、MCP Server、PostgreSQL/Qdrant 与可观测性流水线 |

## 14. 关键风险与决策

| 决策/风险 | v0.1 处理 |
| --- | --- |
| 多 Agent 复杂度 | 固定图、角色白名单、有限重试；不使用自由协商模式 |
| RAG 幻觉/注入 | 引用强制、权限先过滤、检索内容不可信、无命中即拒答 |
| 现有分析为同步 Generator | 用 Adapter 包装进 Celery Worker，暂不重写算法内部 |
| 单模型成本 | Agent 与视觉模型分离配置；预算账本与缓存 |
| SQLite 不适合多租户并发 | 本地保留，企业部署切 PostgreSQL；迁移由 Alembic 管理 |
| 模型供应商变化 | OpenAI 兼容 Client + Endpoint 配置化，业务工具不依赖特定供应商 |

## 15. v0.1 完成定义（Definition of Done）

当前本地实现已满足以下功能定义；“任一项目成员”“权限控制”等表述在企业化前均限定为当前本机项目所有者。

- 任一项目成员可在对话中提交已授权图片，Agent 返回真实 `job_id`，而非虚构结果。
- Worker 完成后可读取相同的既有分析结果、步骤图和人工修订版本。
- 用户可询问 SOP/拍摄规范，回答附带受权限控制的 RAG 来源。
- 用户可询问项目统计，Data Agent 只能读取其项目范围内数据并说明样本口径。
- 低置信度结果自动创建复核任务；Agent 不可自行批准。
- 审计能够按 `trace_id` 追溯对话、检索、工具调用与任务执行。
- 现有测试通过，基准图像的默认 `stalk` 统计结果无回归。

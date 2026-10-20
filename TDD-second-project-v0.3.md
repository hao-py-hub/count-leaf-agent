# 第二项目技术设计 v0.4：Agent、缓存与 MCP

**适用项目：**油菜考种图像分析工作台。本文不描述模拟电路教材检索、Qwen-VL Embedding、ngspice 或实验报告评审能力；这些属于另一个项目。

## 1. 架构

```text
Web / 企业 Agent
       │ REST / Function Calling / MCP stdio
       ▼
FastAPI → Agent Orchestrator → ToolPolicy → Domain Services
                                  ├─ JobService → in-process/Celery Worker
                                  ├─ DomainModel + PolicyEngine
                                  ├─ CacheService
                                  ├─ KnowledgeService (SQLite FTS5)
                                  ├─ EventPublisher / Projections
                                  └─ PhenotypeAnalysisService (VLM + CV)
                                             │
                                   SQLite/PostgreSQL + 文件卷
```

## 1.1 企业化边界

所有请求先解析为 `project_id`、`actor_id`、`trace_id` 和策略版本，再进入 Agent 编排。领域服务只接受已解析的项目范围；ToolPolicy 负责能力授权，PolicyEngine 负责业务规则，二者不能由 Agent 输入覆盖。当前实现仍可使用单用户默认项目，但接口和持久化记录必须保留项目字段。

核心领域对象及最小引用关系：

```text
PlantSample 1──N AnalysisRun 1──N ResultVersion
ResultVersion 1──N PlantPart 1──N PodObservation
AnalysisRun 1──N QualityFinding / ReviewCase
ReviewCase 1──N ResultVersion
```

每个对象至少保存 `project_id`、来源运行/版本、策略版本和 `trace_id`；派生统计只能读取已授权的对象集合。

## 2. 缓存设计

缓存键由资源 ID、`sha256(image)`、`algorithm_version`、`model_endpoint/model_version`、分析参数和提示词版本组成。只有 `completed` 且未撤销的结果可命中；同一资源的 `running` 任务通过幂等键合并，失败任务不写入成功缓存。

- **结果缓存：**保存结构化结果、步骤资源引用、质量检查和版本元数据。
- **工具 Schema 缓存：**企业侧缓存 `tools/list` 或 Function Schema；Schema 版本变化时失效。
- **RAG 查询缓存：**按规范化问题、检索模式、知识库版本和项目范围缓存候选片段；不缓存密钥或未授权正文。
- **会话摘要缓存：**以会话 ID、规范化筛选词和历史版本为键，仅保存脱敏聚合摘要和最多 20 个运行 ID 引用；历史记录变更后自动失效，不把摘要当作事实源。
- **失效策略：**算法/模型/提示词/知识库版本变更、原图哈希变化或安全撤销立即失效。人工修订不会删除或覆盖自动分析缓存：它创建新的人工结果版本并标记其来源运行；只有显式“撤销自动结果”才会使该自动结果不可命中。

缓存命中仍返回 `cache_hit`、来源运行 ID、版本和 `trace_id`，便于审计；Agent 不得自行拼接缓存结果。

## 2.1 油菜分析适配

`PhenotypeAnalysisService` 保留现有权威计算链：VLM 识别主干/主枝/分枝和标尺，OpenCV 果柄法完成角果拓扑计数，随后执行候选复核和图片质量检查。Agent 只读取服务输出，不能自行推算 `pod_count`。缓存复用的是完整分析版本及步骤资源引用，而不是只缓存一段自然语言回答。

## 3. Skill 设计

Skill 是可版本化的领域流程包，不是自由执行代码。首期 Skill：

| Skill | 职责 | 可调用工具 |
|---|---|---|
| `phenotype-analysis` | 组织图片分析和结果解释 | `submit_analysis`, `get_analysis_result` |
| `image-quality-review` | 判断亮度、分辨率、遮挡、标尺、模型冲突和统计异常 | `quality_check`, `create_review_task` |
| `knowledge-grounded-answer` | 按 SOP/指标定义回答并给引用 | `retrieve_knowledge` |
| `project-statistics` | 按明确口径聚合历史结果 | `search_history`, `aggregate_metrics` |

每个 Skill 声明输入/输出 Schema、允许工具、确认点、预算上限和版本。`create_review_task`、重试、人工修订和导出均为写操作，REST/UI 必须先返回待确认摘要，再由调用方在相同范围的请求中显式传入 `confirm: true` 与 `trace_id`；缺少确认时服务拒绝执行并返回 `confirmation_required`。Skill 只能编排服务，不能绕过 ToolPolicy、缓存校验或人工复核。

Skill 与模拟电路项目的教材检索、单图教材回显和实验评审流程隔离；本项目 Skill 的领域对象是植株图片、分析运行、角果计数、质量标记和复核版本。

## 4. MCP 设计

本地 MCP `stdio` 仅开放低风险只读工具：`get_analysis_result`、`quality_check`、`search_history`、`aggregate_metrics`、`get_review`、`retrieve_knowledge`。工具输入使用 Pydantic Schema，输出只含结构化结果、资源 ID 和引用。

MCP 禁止任意文件/SQL/Shell/环境变量访问，也不开放提交分析、修改结果、结案复核和报告导出。所有调用写入统一审计，记录来源 `mcp`、参数哈希、结果引用、耗时和 `trace_id`。

统计导出属于 REST/UI 的已确认写操作，不属于 MCP 范围；导出内容仅为调用方已获授权的聚合指标和结果版本，不包含原图路径或密钥。RAG 查询缓存按规范化问题、检索模式和 `knowledge_version` 隔离；新知识导入会提升版本并使旧候选缓存失效，仅缓存文档/分块引用，不缓存未授权正文。

## 5. 任务与一致性

分析提交先生成幂等键并检查缓存；未命中则创建 `queued` 任务。Worker 执行既有 VLM+CV 流水线，写入原始结果版本，随后运行质量检查。人工修改只能创建新版本；重试创建新任务并复用已完成阶段的缓存。

### 5.1 复核与确认状态机

质量检查命中低分辨率、亮度异常、遮挡、缺少标尺、VLM 与 CV 冲突或统计异常时，系统自动创建 `open` 复核任务，自动结果保持只读且不得自动改写计数。复核人员可提交 `resolved` 的人工版本，或经确认以 `dismissed` 保留自动版本。当前触发原因包含图片质量问题、结果总数与部件合计不一致、VLM 复核改变候选计数；每一次状态迁移记录原因、前后版本、确认结果与 `trace_id`。复核记录同时保存稳定 `reason_code`，支持按原因码查询和统计；自动触发可组合多个原因码，以逗号分隔。组合原因筛选按单个原因码匹配，指标的 `analysis.review_reason_counts` 按原因码分别聚合自动复核事件。服务端维护原因码 allowlist：`manual`、`image_quality`、`count_mismatch`、`model_conflict`；未知码拒绝写入，历史旧记录保持可读。

### 5.2 状态与幂等约束

任务状态为 `queued` → `running` → `completed`、`review_required` 或 `failed`。自动复核创建成功后，任务由 `completed` 迁移为 `review_required`；该状态保留只读结果、报告和人工结案能力，但不得作为分析缓存命中。相同幂等键的 `queued`/`running` 请求返回既有 `job_id`；仅已完成且未撤销的自动版本可作为分析缓存命中。重试必须创建新的任务和运行版本，并通过 `retry_of` 关联原任务。

## 6. 安全与观测

服务端注入项目边界和预算，Agent 输入不能扩大范围。日志不得写入 API Key、原图路径或完整用户问题。Trace 必须贯穿 HTTP、Agent、Skill、MCP、RAG、in-process/Celery Worker 和模型调用；两类 Worker 均写入同一审计账本。

每次模型调用记录 `trace_id`、调用方、模型端点与版本、提示词版本、输入/输出 token（主识别、裁剪复核、角果复核及启用时的粘连检测均逐调用透传；供应商未返回时保持 `NULL`，API 展示为 `unknown`）、计费金额或估算金额、缓存命中状态、耗时、结果版本与错误码。估算金额仅在配置 `VLM_INPUT_COST_PER_MILLION` 或 `VLM_OUTPUT_COST_PER_MILLION` 且取得 token 后计算；审计事件保留参数哈希、确认结果、资源/版本引用，不保留敏感正文。

指标口径如下：缓存命中率 = 命中完成结果的分析请求数 / 可缓存分析请求数；重复调用节省量 = 缓存命中本应触发的模型调用数及其估算成本（按来源运行 ID 回查，未知成本按 0 计）；未确认写操作率 = 被实际执行且请求未显式确认的写操作数 / 已执行写操作数（目标为 0）；MCP 越权率 = 被允许执行但不在 MCP allowlist 的调用数 / MCP 调用数（目标为 0）；引用覆盖率 = 含至少一个知识引用的知识型回答数 / 知识型回答数。上述指标按时间窗口和项目范围聚合。

## 7. v0.4 分阶段实施顺序

1. 固化领域对象、`project_id` 边界和策略版本字段。
2. 增加 PolicyEngine，统一质量、复核、预算和统计口径判断。
3. 将关键状态迁移发布为领域事件，并建立审计/指标投影对账测试。
4. 在现有 Skill、ToolPolicy、MCP 和异步任务上接入项目范围校验。
5. 增加项目隔离、策略版本、事件对账和运营指标的回归测试。

v0.4 不在本阶段实现 OIDC、真实多租户、PostgreSQL、Redis/Celery 或远程 MCP 网关；这些属于后续生产部署阶段，避免在单机 MVP 中堆叠未验证基础设施。

当前指标查询由 `GET /api/v2/agent/metrics` 提供，可选 `trace_id` 做单链路过滤；返回缓存命中/未命中、分析完成与复核创建数量、模型调用次数、token、估算成本和 `write_operations` 的已执行写操作数/未确认执行率。账本不保存提示词正文、原图路径或密钥。

指标支持可选 `since`/`until` ISO 时间窗口过滤，服务端拒绝逆序时间范围。

指标 API 使用版本化的 Pydantic 响应模型，避免客户端依赖未声明的动态字段。

指标响应的 `batch` 分组统一统计批次提交、取消和重试生命周期事件。

指标响应的 `operations` 分组统一统计报告提交与历史导出写操作。

指标响应的 `mcp` 分组统一统计 MCP 调用数、越权拒绝数和越权率，拒绝调用同样写入审计账本。

失败重试通过 `retry_of` 关联父任务；安全撤销通过确认后的 `POST /api/v2/jobs/{job_id}/cache-revocation` 写入 `cache_revoked` 事件，后续相同缓存键不可命中。

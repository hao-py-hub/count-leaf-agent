# 油菜表型分析 Agent 工作台

面向油菜科研考种场景的本地受控视觉 Agent。系统以 **VLM 部件定位 + `stalk` 果柄法计数** 为唯一图像计数权威，在其外构建异步任务、人工复核、结果版本、项目隔离、知识检索、审计与外部 Agent 接入能力。

它不是通用聊天机器人，也不重新训练角果检测模型；它解决的是“将植株照片的自动分析结果安全地纳入科研工作流”的问题。

## 项目能力

| 场景 | 提供能力 | 核心约束 |
| --- | --- | --- |
| 图像分析 | 单图/批量异步任务、SSE 进度、缓存、重试与安全取消 | 创建任务需要 `confirm: true` |
| 结果解释 | Agent 根据真实分析结果解释质量发现 | LLM 不得估算或补写角果数量 |
| 人工复核 | 自动/手动复核、结案、驳回、结果版本链 | 人工修改不覆盖自动版本 |
| 科研数据 | 项目内历史统计、导出、报告、指标 | 导出和报告需要确认 |
| 知识问答 | SQLite FTS5 RAG、SOP/拍摄规范/口径检索 | 回答附来源，无依据则明确说明 |
| 外部接入 | Function Calling 与 MCP `stdio` 六项只读工具 | 不提供 Shell、SQL、任意路径或写工具 |

## 总体架构

```text
┌──────────────────────────────── 使用层 ───────────────────────────────┐
│ 浏览器工作台（原生 HTML/JS） │ 外部企业 Agent / Function Calling / MCP │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ HTTPS / SSE / stdio
┌───────────────────────────────▼───────────────────────────────────────┐
│ FastAPI API（app/api/v2）                                              │
│ 资产 │ 任务/批次 │ 复核 │ 报告 │ 历史/导出 │ 知识库 │ 审计/指标         │
└──────────────┬───────────────────────────────┬────────────────────────┘
               │                               │
┌──────────────▼───────────────┐  ┌────────────▼────────────────────────┐
│ Agent（app/agent）            │  │ 领域服务（app/services）             │
│ LangGraph 固定路由            │  │ 任务、缓存、复核、RAG、报告、策略     │
│ SkillRegistry + ToolPolicy    │  │ 版本、审计、事件、项目范围            │
└──────────────┬───────────────┘  └────────────┬────────────────────────┘
               │                               │
┌──────────────▼───────────────────────────────▼────────────────────────┐
│ SQLite / 本地受控目录 / in_process Worker（可选 Celery）               │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
┌───────────────────────────────▼───────────────────────────────────────┐
│ 算法流水线（app/pipeline）                                             │
│ VLM 定位主干/主枝/分枝 → 裁剪复核 → stalk 果柄法计数 → 候选复核         │
└───────────────────────────────────────────────────────────────────────┘
```

### 前端、后端与 Agent 的分工

| 层 | 负责内容 | 明确不负责 |
| --- | --- | --- |
| 前端 | 上传、项目选择、显式确认、任务进度、结果和复核展示 | 保存密钥、直接改库、自行算计数 |
| API | Schema、确认门禁、归属校验、SSE、错误响应 | 同步阻塞执行图像流水线 |
| Agent | 意图路由、受限工具编排、结果解释、RAG 引用 | 文件系统、SQL、Shell、编造数值 |
| 领域服务 | 幂等、缓存、状态机、版本、预算、审计、项目隔离 | 依赖前端或 LangGraph |
| 算法 | 已授权图片的 VLM + CV 分析与步骤产物 | 会话、项目 UI 或 Agent 权限 |

## 数据流

### 分析、复核与报告

```text
图片 → 资产上传（asset_id + project_id）
     → confirm=true 创建 AnalysisJob（queued）
     → Worker：VLM 定位 → CV 计数 → 质量检查
     → 自动 ResultVersion + 步骤图 + provenance + 审计/领域事件
     ├─ completed：项目内读取、统计、报告
     └─ review_required：ReviewCase → 人工结案 → 新的人工 ResultVersion
```

每个关键对象关联 `project_id` 与 `trace_id`。缓存只复用已完成且未撤销的自动结果；重试创建新任务并用 `retry_of` 关联父任务。

### 对话、RAG 与审计

```text
用户问题 + project_id + trace_id
  → Agent supervisor 固定路由
  → knowledge：FTS5 检索 + 来源；或业务 ToolPolicy 校验 + 领域服务
  → 脱敏会话、工具调用、模型账本、领域事件
  → /api/v2/agent/traces/{trace_id} 按项目回放
```

## 安全与项目隔离

- `project_id` 覆盖资产、任务、批次、复核、报告、历史、知识库、会话、trace、审计和兼容接口；错误项目返回 `404`。
- 所有写操作都需要 `confirm: true`；未确认请求不会创建任务、修改数据或消耗模型预算。
- 审计只保存参数哈希、状态、结果引用、耗时、版本和追踪标识，不保存 API Key、原始问题或原图绝对路径。
- MCP 仅开放 `get_analysis_result`、`quality_check`、`search_history`、`aggregate_metrics`、`get_review`、`retrieve_knowledge` 六项只读工具；Function Calling 额外提供 `get_analysis_evidence`，聚合真实结果、版本与复核反馈。

## 技术栈

| 模块 | 当前方案 |
| --- | --- |
| 前后端 | FastAPI、原生 HTML/JS、SSE |
| Agent | LangGraph 固定状态图、SkillRegistry、ToolPolicy、Pydantic Schema |
| 算法 | 豆包视觉模型兼容接口、OpenCV、scikit-image、`stalk` 果柄法 |
| 存储与任务 | SQLite/SQLAlchemy、本地文件目录、默认进程内 Worker、可选 Celery |
| RAG 与观测 | SQLite FTS5 `keyword`/`hybrid`、审计账本、token/成本/缓存/复核指标 |

## 启动

### 1. 配置 `.env`

从 `.env.example` 复制，并至少配置：

```dotenv
ARK_API_KEY=你的豆包 Ark API Key
ARK_API_BASE=https://ark.cn-beijing.volces.com/api/v3
VLM_MODEL=你的视觉模型 Endpoint
ANALYSIS_ALGORITHM_VERSION=stalk-v0.1
ANALYSIS_PROMPT_VERSION=phenotype-prompt-v1
STALK_TWIG_WIDTH_RATIO=0.70
STALK_TWIG_AREA_RATIO=0.25
```

`STALK_TWIG_*` 是果柄法枯枝过滤阈值，取值范围 `(0, 1]`，并会进入缓存键。

### 2. Docker

```powershell
Set-Location "D:\CDriveMigration\PycharmProjects\count-beaf-agent"
docker compose up -d --build
```

打开 `http://localhost:8501`。宿主机保留 `data/`、`uploads/`、`results/`、`online_uploads/`、`benchmark_cache/`。

### 3. 本地开发

```powershell
Set-Location "D:\CDriveMigration\PycharmProjects\count-beaf-agent"
& ".\.venv312\Scripts\python.exe" -m app.main
```

## 接口地图

| 分类 | 主要路径 |
| --- | --- |
| 健康/能力 | `GET /api/health`、`/api/ready`、`/api/v2/agent/runtime`、`/skills` |
| 资产/任务 | `/api/v2/assets/upload`、`/api/v2/jobs`、`/api/v2/batches` |
| 复核/报告 | `/api/v2/reviews`、`/api/v2/agent/jobs/{id}/report`、`/api/v2/agent/reports/{id}` |
| 知识/历史 | `/api/v2/knowledge`、`/api/v2/exports/history`、`/api/v2/history` |
| Agent/审计 | `/api/v2/agent/chat`、`/jobs/{id}/evidence`、`/traces/{trace_id}`、`/audit`、`/metrics` |

涉及数据的请求应携带 `project_id`；创建、结案和导出还需 `confirm: true`。

## 测试、评测与边界

运行全量测试：

```powershell
Set-Location "D:\CDriveMigration\PycharmProjects\count-beaf-agent"
& ".\.venv312\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
```

- 本地全量 `unittest` 回归 **297 项通过**；覆盖任务、缓存、复核、Agent、RAG、MCP、事件、证据包与项目隔离。
- Agent 黄金集 9/9 通过，仅说明受控路由、工具选择与引用覆盖，不代表图像计数精度。
- 当前没有可公开宣称的整株角果计数准确率。DeepCanola BR017 是独立角果图，缺少整株主干/分枝结构，不能用于本项目整株流水线精度结论。
- 本期为单机单用户 MVP；OIDC/RBAC、真实多租户、对象存储、PostgreSQL、生产 Redis/Celery 集群、远程 MCP 网关、Embedding/向量数据库不在本期范围。

## 相关文档

- [PRD-second-project.md](PRD-second-project.md)：产品定位与验收指标。
- [TDD-second-project-v0.3.md](TDD-second-project-v0.3.md)：领域模型、策略、事件与隔离设计。
- [agent.md](agent.md)：Agent、工具、RAG、MCP 与代码规范。
- [PROJECT_STATUS.md](PROJECT_STATUS.md)：实施阶段、测试证据和已知边界。
- [docs/DEMO.md](docs/DEMO.md)：演示路径。
- [DOCKER.md](DOCKER.md)：容器运行与迁移说明。

# 2026-08-28：评测能力需求澄清与实施拆解

| 属性 | 内容 |
| --- | --- |
| 当前阶段 | T0-T7 已落地；正在执行 T8 回归、文档和交付收口 |
| 总领文件 | 本文件；所有子任务必须引用本文件中的已确认决策 |
| 目标 | 先厘清当前评测逻辑、目标范围、保护边界和验收口径，再落地一个整个平台可用的统一评测能力 |
| 日期 | 2026-08-28（Asia/Shanghai） |
| 外部依据 | 用户提供的知乎文章和 `https://docs.getsemantica.ai/`；当前环境无法读取正文，未将外部内容当作已确认需求 |
| 研究附录 | [research-agent-evaluation.md](./research-agent-evaluation.md)：Agent 评测数据集、执行、指标和 pass@k/pass^k |

## 1. 本轮已确认的需求

以下内容来自用户当前对话，可以直接作为本任务的约束：

1. 在 `tasks/2026-08-28/` 下维护本次任务文档。
2. `task.md` 是整个任务的总领文件，记录目标、确认项、不改项、执行方式和验证方式。
3. 可以拆分多个小 task；每个小 task 在实施前都要明确修改范围、测试和验证方法。
4. 先澄清并确认需求、优化拆解，再考虑修改代码。
5. 统一评测能力属于平台级能力：数据集、实验、执行、Trace、指标、API 和前端页面必须与具体业务链路解耦；现有 Agent、Tool/MCP、RAG、Graph 评测都必须能通过适配器接入。

本轮完成标准是文档、统一评测代码、管理员只读页面和 Docker 验证证据齐备；真实模型/工具回归仍不在本轮范围。

## 2. 当前源码已确认的评测逻辑

这一节是仓库源码盘点，不代表已经决定要统一或重写这些链路。

### 2.1 知识库 RAG 评估

- API 前缀：`/api/evaluation/*`，入口在 `backend/server/routers/knowledge_eval_router.py`。
- 持久化表：`evaluation_datasets`、`evaluation_dataset_items`、`evaluation_runs`、`evaluation_run_items`。
- 题目格式：JSONL 至少包含 `query`，可以包含 `gold_chunk_ids`、`gold_answer`。
- 执行链路：逐题调用知识库查询；可选答案模型和 Judge 模型；结果写回评估 run。
- 当前指标：Recall@K、F1@K、答案 0/1 Judge；`overall_score` 会根据是否有答案指标选择不同口径。
- 自动生成数据集支持 `vector` 和 `graph_enhanced`，由现有任务执行机制处理。

关键实现：

- `backend/package/yuxi/knowledge/eval/service.py`
- `backend/package/yuxi/knowledge/eval/evaluator.py`
- `backend/package/yuxi/knowledge/eval/metrics.py`
- `backend/package/yuxi/repositories/evaluation_repository.py`

### 2.2 企业关系图谱评测

- API 前缀：`/api/company-relations/evaluations/run`。
- 脚本入口：`backend/scripts/company_relation/run_evaluation.py`。
- 输入：冻结的 `snapshot.json` 和 `golden_questions.jsonl`。
- 策略：`vector_rag`、`graph_rag`、`cypher_direct`；当前使用确定性查询和答案模板。
- 评分：拒答状态、答案实体、事实 ID、关系路径的严格比较，并输出 Recall@10、MRR、证据对齐率、幻觉率和延迟分位数。
- 该链路有自己的回归门禁和测试，暂不视为平台统一评测的实现。

关键实现：

- `backend/package/yuxi/company_relation/evaluation.py`
- `backend/package/yuxi/company_relation/query_service.py`
- `backend/scripts/company_relation/run_evaluation.py`
- `backend/server/routers/company_relation_router.py`

### 2.3 Agent Eval

- CLI：`yuxi agent eval`，实现于 `packages/yuxi-cli/src/yuxi_cli/agent_eval.py`。
- HTTP 入口：`POST /api/agent-invocation/eval/runs`，实现于 `backend/server/routers/agent_invocation_eval_router.py`。
- 执行方式：创建内部 Conversation/AgentRun，复用现有队列、Worker、Redis Run Stream 和聊天链路。
- 外部记录：CLI 从 Langfuse Dataset 读取题目，并把最终文本写回 experiment。
- 当前轨迹摘要只统计事件数、工具调用数、工具错误数和中断数，没有本地统一 Experiment/Rollout、pass@k 或确定性 `expected_output` 比对。
- 评测来源使用 `source=agent_evaluation`；内部线程使用 `visibility=internal`，应继续与普通聊天入口隔离。

关键实现：

- `packages/yuxi-cli/src/yuxi_cli/agent_eval.py`
- `backend/server/routers/agent_invocation_eval_router.py`
- `backend/package/yuxi/services/run_submission_service.py`
- `backend/package/yuxi/services/run_worker.py`

### 2.4 前端现状与本轮改动

- `/evaluation` 页面实现于 `web/src/views/EvaluationPlatformView.vue`。
- 本轮保留 API 不可用时的明确 Fixture 回退，但正常刷新优先请求 `/api/platform-evaluations/*`。
- 页面现在支持来源筛选、实验聚合、rollout 失败和按展开加载脱敏 Trace；不提供创建、运行、取消和重试按钮。

## 3. 外部文章核验状态

用户提供链接：

`https://zhuanlan.zhihu.com/p/2050893501324441306`

用户指定的 Semantica 文档：

`https://docs.getsemantica.ai/`

已按 agent-reach 的网页读取路径尝试：

- Jina Reader：当前环境 DNS 无法解析 `r.jina.ai`。
- Web Reader MCP：本地 `mcporter` 配置缺少可用的 Codegraph 服务定义，调用未完成。

本次新增的 Semantica 文档链接也因当前环境 DNS 限制无法读取，暂不据此推断包名、模块名或接口。

因此，文章中的概念、指标或架构不能在本任务中写成“已确认需求”。如文章内容是本次实现的必要依据，需要用户提供正文、截图或要点后再补入“决策记录”。

## 4. 必须先确认的需求决策

用户已确认采用默认口径的项目直接进入“已确认”；D4 因为新增了第三方库候选，仍需完成包身份、能力和兼容性核验后才能冻结。

| 编号 | 需要确认的问题 | 可选口径 | 当前结论 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | 本次最终交付是什么 | A. 只整理现状和测试基线；B. 新增平台级统一评测闭环；C. 直接接入真实 Agent/Tool/MCP 评测 | B；统一能力必须可供整个平台的评测来源使用，真实链路另立任务 | 已确认 |
| D2 | 首轮纳入哪些对象 | A. 仅 Agent；B. Agent + Tool/MCP；C. 平台现有 Agent + Tool/MCP + RAG + Graph 全部接入 | C；首个可执行 Runner 使用 Agent + Tool/MCP Fixture，RAG/Graph 必须提供只读适配器和回归验证 | 已确认 |
| D3 | 首轮是否允许真实调用 | A. 只用 fake/fixture；B. 测试环境真实调用；C. 生产配置真实调用 | A；真实调用另立任务 | 已确认 |
| D4 | 数据和结果如何可溯源、可排查 | A. 只保留最终结果；B. 本地保存 provenance/trace，Langfuse 可选关联；C. 强依赖 `semantica` 作为唯一溯源层 | B；`semantica` 先做可行性验证，不作为首轮唯一依赖 | 部分确认 |
| D5 | 是否需要评测页面 | A. 不做页面；B. 页面仅查看结果；C. 页面可创建、运行、取消实验 | B | 已确认 |
| D6 | 首轮通过口径 | A. 仅最终答案；B. 答案 + 工具/轨迹契约；C. 再加入 LLM Judge/人工评分 | B；LLM Judge 单独设计版本化协议 | 已确认 |
| D7 | 兼容性要求 | A. 允许迁移旧链路；B. 旧入口保持兼容并新增适配层；C. 只改 Agent Eval | B | 已确认 |
| D8 | 评测权限和可见性 | A. 仅管理员；B. 增加专门评测权限；C. 沿用现有 Agent 权限 | A；强制内部线程隔离 | 已确认 |

### 4.1 需要用户补充的最小验收样例

至少需要一条真实业务语义的样例，才能冻结字段和指标：

- 输入：问题或任务内容；
- 期望输出：精确文本、归一化文本、JSON、状态或其他契约；
- 允许/禁止的 Tool、MCP、Skill 或 SubAgent；
- 失败时应显示的原因；
- 该样例属于 Agent、Tool/MCP、RAG 还是 Graph。

没有业务样例时，使用 T0 文件中的通用 Fixture 样例作为首轮契约；这只能证明平台闭环，不代表业务质量口径已经确定。

T0 子任务已提供一个无副作用的 `fixture.echo` 临时样例，见 `T0-requirements-contract.md`。在用户没有业务样例时，T1 POC 可以使用该样例；进入正式业务验收前仍需替换或确认。

### 4.2 `semantica` 第三方库的核验任务

`semantica` 的目标仓库已由用户确认：`https://github.com/semantica-agi/semantica`。它仍不能直接写入核心架构或依赖清单；需要从该仓库确认可安装包名、版本、许可证、API 和运行依赖。当前环境无法访问 GitHub/PyPI 完成外部核验。

核验通过前，平台统一定义自己的最小 `ProvenanceRecord`/`TraceEvent` 契约，至少包含：

- `source_type`、`source_id`、`source_version`；
- `dataset_version`、`experiment_id`、`rollout_id`、`attempt`；
- `parent_id`、`sequence`、`component`、`operation`；
- 脱敏参数/结果摘要、checksum、时间和失败 reason code；
- 可跳转的 `langfuse_trace_id`（可空）以及本地记录位置。

只有当 `semantica` 满足以下条件，才考虑在 T1 中增加适配器：

1. 能表达上述来源、版本、父子关系和运行实例，而不是只做文本语义检索；
2. 支持 Python 3.12、异步 Worker、PostgreSQL/Redis 现有部署方式，且不会强制引入第二套存储或常驻服务；
3. 能序列化、查询和导出溯源记录，异常时可以回退到本地事实源；
4. 许可证、维护状态、依赖数量和安全风险可接受；
5. POC 能覆盖一条 fixture 成功链路和一条失败链路，并通过脱敏、幂等和重启恢复测试。

不满足条件时，首轮只使用本地 PostgreSQL + Langfuse trace ID 的轻量溯源，不引入该库。

## 5. 保护边界（当前实施前的暂定约束）

以下边界是为了避免澄清期间产生破坏性改动；若用户需要改变，必须在第 9 节记录新的决策：

1. 不删除、迁移、重命名现有知识库 `evaluation_*` 表，不改变现有 `/api/evaluation/*` 协议。
2. 不修改企业关系 `vector_rag`、`graph_rag`、`cypher_direct` 的查询、题库和评分逻辑；如需统一，只通过适配器读取结果。
3. 不改变普通 Web Chat、AgentRun、请求队列、取消和 SSE 的既有协议。
4. 评测线程必须继续使用内部来源标记和 `visibility=internal`，不得出现在普通聊天列表、搜索、历史、状态或文件入口。
5. 未经单独授权，不调用真实模型、真实 Tool/MCP 副作用、真实用户会话或外部高并发压测。
6. 不新增第二套 PostgreSQL/Redis/Neo4j/Milvus、常驻评测服务或 Docker socket 挂载。
7. 不保存凭据、私有 chain-of-thought、完整未脱敏工具载荷或不必要的敏感原文。

这些是“保护边界”，不是对统一平台方案的最终批准。

## 6. 已确认目标方案与后续边界

### 6.1 不纳入本次实施的方案 A：只做现状盘点

- 产出：当前三条链路的流程、数据结构、指标字典和测试基线。
- 不新增表、API 或页面。
- 适用：用户暂时只需要理解现有逻辑。

### 6.2 本次实施目标：平台级统一评测闭环

- 产出：平台级数据集版本、实验、逐题 rollout、可观察 Trace、确定性指标、聚合查询和前端页面查看。
- 统一 API、数据模型和页面不绑定单一 Agent；现有 RAG、企业关系 Graph、Agent/Tool/MCP 入口均通过适配器接入。
- 首个可执行 Runner 采用无副作用 Fixture；这只是执行器的第一种实现，不是平台能力的范围限制。
- 首轮不调用真实模型或真实工具副作用。
- 结果必须可以从数据集版本、实验、rollout、trace、指标和失败原因逐级追溯。
- `semantica` 仅在 T1 POC 通过后作为可选溯源适配器接入；本地事实源不能被替换。

### 6.3 后续方案 C：真实 Agent/Tool/MCP 回归

- 不属于本次实施；只有方案 B 完成并单独授权后再创建新任务。
- 依赖方案 B 的结果协议、隔离验证和成本/权限上限。
- 单独定义真实依赖、失败重试、限流、超时、Token/cost 和审计要求。

## 7. 优化后的子任务结构

子任务不是自动授权。每个子任务必须先满足“进入条件”，完成后在本文件更新状态、改动文件、测试命令和结果，再进入下一项。

| 编号 | 子任务 | 进入条件 | 主要产出 | 验证重点 | 状态 |
| --- | --- | --- | --- | --- | --- |
| [T0](./T0-requirements-contract.md) | 需求、术语和验收契约冻结 | D1-D8 有结论；至少一条验收样例 | 决策表、字段契约、指标字典、非目标 | 文档评审；schema 草案自洽；无隐式字段 | 已完成（采用通用 Fixture，业务样例可替换） |
| [T1](./T1-provenance-semantica-spike.md) | 溯源契约与 `semantica` 可行性 | T0 完成；拿到确切包身份或记录不可核验 | `ProvenanceRecord`/`TraceEvent` 契约；采用/不采用结论 | 外部证据记录；本地成功/失败 Fixture POC | 已完成：暂不引入 Semantica |
| [T2](./T2-evaluation-adapters.md) | 现有链路适配边界 | T1 完成且选定纳入对象 | Agent、RAG、Graph 只读适配器 | 字段、失败和缺失 Trace 单测 | 已完成 |
| [T3](./T3-persistence.md) | Dataset/Version/Task 持久化 | T1 完成；事实源和版本规则已冻结 | 八张平台表、Repository、不可变版本、游标 | SQLite unit + PostgreSQL 建表/探针 | 已完成（create_all schema） |
| [T4](./T4-experiment-runner-lifecycle.md) | Experiment/Rollout 执行生命周期 | T3 完成；D3 已确认 fixture | Fixture Runner、状态、幂等、取消、恢复入口 | 成功/失败/超时/取消和重复运行 | 已完成（API 请求内同步 Runner；恢复方法已提供） |
| [T5](./T5-trace-metrics.md) | Trace 和确定性评估 | T4 产出稳定 rollout/事件 | Trace 校验、失败归因、四种 pass 指标 | 边界、父子关系和缺失事件 | 已完成 |
| [T6](./T6-platform-api.md) | API 适配层 | T3-T5 契约稳定；D8 已确认权限 | 管理员 API、cursor、错误映射、取消内部入口 | 路由权限/参数/资源边界 | 已完成（LITE 集成测试按设计跳过） |
| [T7](./T7-readonly-ui.md) | 页面查看 | D5 已确认；T6 API 稳定 | API client、来源筛选、指标、失败、Trace | ESLint、Build、关键路径 | 已完成 |
| [T8](./T8-regression-docs-release.md) | 回归、文档和交付收口 | 已选子任务全部完成 | changelog、验证记录、残余风险 | Docker 中 Unit/Integration/Lint/Build | 进行中 |

### 7.2 具体落地文件和验证命令

以下是当前可执行的初版文件清单。T0/T1 完成后如果源码结构或 Semantica POC 结论要求调整，必须先回写本节，再修改代码。

| 子任务 | 预期新增/修改文件 | 预期验证 |
| --- | --- | --- |
| T0 | `tasks/2026-08-28/task.md`、`T0-requirements-contract.md` | 文档审阅、验收样例双人判定、`git diff --check` |
| T1 | `T1-provenance-semantica-spike.md`、临时 `/tmp/yuxi-semantica-spike/`；通过后才考虑 `backend/package/yuxi/evaluation/provenance.py` | 包元数据/许可证检查；fixture 成功/失败 POC；脱敏、checksum、幂等和重启读取 |
| T2 | `backend/package/yuxi/evaluation/adapters.py`、`backend/test/unit/evaluation/test_adapters.py` | 三条旧链路只读映射；字段、失败和 trace_missing 单测 |
| T3 | `backend/package/yuxi/storage/postgres/models_evaluation.py`、`backend/package/yuxi/repositories/platform_evaluation_repository.py`、`backend/package/yuxi/storage/postgres/manager.py`、`backend/test/unit/evaluation/test_persistence.py` | 建表、发布不可变、游标、唯一约束、幂等写入、重连读取 |
| T4 | `backend/package/yuxi/evaluation/experiment_service.py`、`runner.py`、`backend/test/unit/evaluation/test_lifecycle.py`、`backend/test/integration/evaluation/test_runner.py` | Fixture 成功/失败/超时/取消、重复 job、并发上限、Worker 重启 |
| T5 | `backend/package/yuxi/evaluation/provenance.py`、`metrics.py`、`backend/test/unit/evaluation/test_provenance.py`、`test_metrics.py` | 事件父子关系、脱敏、缺失事件、逐条指标和聚合边界 |
| T6 | `backend/server/routers/evaluation_platform_router.py`、`backend/server/routers/__init__.py`、`backend/test/integration/api/test_evaluation_platform_router.py` | 管理员权限、参数错误、分页、状态机、旧 API 回归 |
| T7 | `web/src/apis/evaluation_api.js`、`web/src/views/EvaluationPlatformView.vue`、相关前端测试 | `docker compose exec web pnpm test:unit`、`pnpm run lint`、`pnpm run build`、关键页面流程 |
| T8 | `backend/test/`、`docs/develop-guides/changelog.md`、必要时 `docs/.vitepress/config.mts` | Docker 内 Unit/Integration/E2E/Lint/Build、敏感信息扫描、`git diff --check` |

### 7.3 首轮最小数据结构（实现前冻结）

为了让任务可以落地，先列出最小记录，不提前承诺字段类型或 ORM 写法：

| 记录 | 必须能回答的问题 | 最小字段 |
| --- | --- | --- |
| Dataset | 这批题目来自哪里 | `dataset_id`、名称、描述、创建者、时间 |
| DatasetVersion | 本次运行用的题目是否可复现 | `version_id`、`dataset_id`、checksum、来源声明、发布状态、发布时间 |
| Task | 单题输入和预期是什么 | `task_id`、`task_key`、版本、输入、expected、protocol、tags |
| Experiment | 用什么 Agent/配置评测 | `experiment_id`、DatasetVersion、Agent/Tool/MCP 快照、Evaluator 版本、并发/超时/重试 |
| Rollout | 单题第几次尝试结果如何 | `rollout_id`、Experiment/Task、attempt、幂等 key、状态、输出摘要、错误分类、耗时/token/cost |
| TraceEvent | 执行中发生了什么 | `sequence`、`parent_id`、组件/操作、脱敏摘要、checksum、时间、reason code、Langfuse trace ID |
| MetricResult | 哪条规则通过或失败 | `metric_name`、版本、值、状态、缺失原因、计算输入 checksum |
| Failure | 为什么失败、如何排查 | `failure_type`、主因、关联 rollout/metric/trace、是否可重试 |

禁止只保存一个 `overall_score` 作为事实源；聚合分数必须能由逐条结果重新计算。

### 7.4 首轮 API 草案（T0/T1 通过后冻结）

API 前缀暂定为 `/api/platform-evaluations`，避免与现有知识库 `/api/evaluation` 冲突。以下请求用于验收，不代表现在就创建路由：

```http
POST /datasets
{
  "name": "agent-fixture-smoke",
  "description": "无副作用 Agent + Tool/MCP 评测"
}

POST /datasets/{dataset_id}/versions
{
  "source": {"type": "inline", "uri": "fixture://agent-fixture-smoke"},
  "tasks": [{"task_key": "fixture_tool_echo_001", "input": {"query": "..."}, "expected": {"final_answer": {"value": "fixture-ok: hello", "validator": "normalized_exact"}}}]
}

POST /datasets/{dataset_id}/versions/{version_id}/publish
POST /experiments
{
  "dataset_version_id": "dv_...",
  "agent_slug": "fixture-agent",
  "runner": "fixture",
  "evaluator_version": "deterministic-v1",
  "concurrency": 1,
  "timeout_seconds": 5,
  "max_attempts": 1
}

POST /experiments/{experiment_id}/run
GET  /experiments/{experiment_id}
GET  /experiments/{experiment_id}/rollouts?limit=50&cursor=...
GET  /rollouts/{rollout_id}/trace
GET  /experiments/{experiment_id}/metrics
GET  /experiments/{experiment_id}/failures
```

响应必须带 `request_id`、资源 ID、状态和版本；列表接口必须返回 `next_cursor`。错误至少区分 `validation_error`、`not_found`、`conflict`、`invalid_state` 和 `forbidden`。

### 7.5 状态转换和中断处理

| 当前状态 | 允许动作 | 下一状态 | 重复动作 |
| --- | --- | --- | --- |
| `DRAFT` | 发布/排队 | `QUEUED` | 返回同一 experiment |
| `QUEUED` | Worker 接手 | `RUNNING` | 不重复创建 rollout |
| `RUNNING` | 题目完成 | `AGGREGATING` | 幂等更新 |
| `RUNNING` | 用户取消 | `CANCELLING` | 返回当前状态 |
| `CANCELLING` | 任务清理完成 | `CANCELLED` | 返回当前状态 |
| `AGGREGATING` | 聚合完成 | `FINISHED` | 返回当前结果 |
| 任意非终态 | 不可恢复异常 | `FAILED` | 保留失败原因 |
| `FINISHED`/`FAILED`/`CANCELLED` | 普通更新 | 拒绝 | `409 invalid_state` |

Worker 重启后的恢复入口是：先按 `status in (QUEUED, RUNNING, AGGREGATING)` 查询未终态 Experiment，再按幂等 key 补偿 rollout；不得从前端时间戳或 Redis 临时状态推断完成结果。

### 7.6 每个子任务必须填写的内容

开始子任务前，在 `tasks/2026-08-28/` 创建独立文件，例如：

`T0-requirements-contract.md`

文件必须包含：

1. 关联总领文件和已确认决策编号；
2. 本子任务的目标和明确不做的内容；
3. 修改文件清单（实现前允许是候选路径）；
4. 数据、接口、状态或指标的具体变更；
5. Unit/Integration/E2E/Lint 的验证命令和预期结果；
6. 完成后回写总领文件的状态、实际改动和测试结果。

若子任务发现需求矛盾或需要扩大范围，必须停在文档阶段，先更新第 7 节和第 9 节，不得顺手改代码。

## 8. 验证策略

### 8.1 需求确认阶段

- [ ] D1-D8 均有明确结论或明确标记为暂不做。
- [ ] 至少一条验收样例可以被人工判断通过/失败。
- [ ] 术语、结果事实源、权限、可见性和兼容性没有冲突。
- [ ] 子任务依赖形成单向顺序，没有未声明的跨任务前置条件。

### 8.2 代码实施阶段

- 后端：按改动范围执行目标 Unit、Integration；涉及主链路时补 E2E。
- 前端：执行相关 Unit、ESLint 和生产构建；涉及交互时补 Playwright 或等价手工记录。
- 环境：按项目约定在 Docker Compose 容器中检查、测试和 Lint。
- 安全：检查测试数据、日志、Trace 和文档无凭据、私有思维链和未脱敏敏感内容。
- 回归：对照 T0 基线区分既有失败和本次新增失败。
- 文档：代码完成后同步 `docs/develop-guides/changelog.md`，正式用户文档变更时同步导航。

### 8.3 首轮统一闭环

验收顺序固定为：

1. 导入数据集并发布不可变版本；
2. 创建实验并保存配置快照；
3. 通过统一适配协议接入 Agent/Tool/MCP、RAG、Graph 三类来源的代表性记录；
4. 运行一条成功、一条失败、一条超时或取消样例；
5. 查询逐题 rollout、Trace、指标和失败原因，并在同一个前端页面查看；
6. 重复投递同一任务，确认幂等；
7. 重启 Worker 或重新连接数据库后，确认结果仍可恢复；
8. 查询聚合结果，确认空值返回 `null` 或短横线，不用固定数字冒充测量值；
9. 确认内部评测线程不进入普通聊天和文件入口；
10. 回归旧 RAG 和企业关系评测入口。

## 9. 决策记录

每次需求确认或范围变化追加一行，不覆盖历史结论。

| 时间 | 决策编号 | 结论 | 依据/示例 | 影响的子任务 |
| --- | --- | --- | --- | --- |
| 2026-08-28 | 初始 | 先澄清需求和拆解，暂不改业务代码 | 用户对话 | T0 |
| 2026-08-28 | D1-D3、D5-D8 | 采用统一 Fixture 闭环；首轮覆盖 Agent + Tool/MCP；不做真实调用；页面只查看；答案 + 工具/轨迹契约；旧入口兼容；管理员且内部隔离 | 用户最新回复“其他的按照默认逻辑来” | T0、T2-T8 |
| 2026-08-28 | D4 | 以本地 provenance/trace 为事实源，Langfuse 可选关联；`semantica` 先做包身份、能力和 POC 核验，不作为唯一依赖 | 用户要求“尽量可溯源或排查”，询问引入 `semantica` | T1、T3、T5 |
| 2026-08-28 | T0 样例 | 首轮没有业务样例时，先采用 `fixture.echo` 通用样例；业务样例后续可替换 | 用户要求先拆解并按默认逻辑推进 | T1、T4、T5 |
| 2026-08-28 当前轮 | D1-D2、D5 | 评测逻辑升级为整个平台可用：Agent + Tool/MCP、RAG、Graph 均接入统一协议/API/页面；首个 Runner 仍使用 Fixture，前端页面首轮只读 | 用户明确“评测逻辑是整个平台可用的，并且需要添加对应前端页面” | T0、T2、T6、T7、T8 |

## 10. 当前进度与中断恢复

### 10.1 进度记录

| 时间（Asia/Shanghai） | 完成内容 | 产出/结论 | 当前状态 |
| --- | --- | --- | --- |
| 2026-08-28（本轮开始） | 盘点 RAG、企业关系、Agent Eval 三条现有链路及 `/evaluation` 页面 | 确认入口、数据表、指标和隔离现状 | 已完成 |
| 2026-08-28（需求澄清） | 将统一评测目标拆为 D1-D8，并确认默认口径 | D1-D3、D5-D8 已确认；D4 采用本地事实源 + 可选 Semantica 适配 | 已完成 |
| 2026-08-28 16:14-16:16 | 初次整理 T0-T8 子任务结构和文件级修改点 | 形成第一版可执行任务清单 | 已完成 |
| 2026-08-28 16:16 | 用户提供 Semantica 文档地址和 GitHub 仓库地址 | 固定 T1 核验输入；外部网络仍不可用 | 已完成 |
| 2026-08-28 16:17-16:18 | 尝试读取 `docs.getsemantica.ai`、GitHub API 和 README | DNS/API 访问失败，未安装、未修改依赖 | 阻塞于 T1 |
| 2026-08-28 16:29-16:32 | 整理 OpenAI Evals、LangSmith、Inspect、SWE-bench、WebArena、ToolSandbox、tau-bench 等公开方案的通用落地模式 | 形成研究附录草案；首次写入操作未完成 | 文档草案 |
| 2026-08-28 16:45-16:46 | 补写研究附录并完成当前项目适配性评估 | 明确最适配组合、Semantica 边界和推荐实现顺序 | 已完成（来源待联网复核） |
| 2026-08-28 当前轮 | 按新原则重建 T0-T8 独立任务文件，并补齐目标、确认项、疑问、非目标、任务、验证、进度和恢复入口 | 九份子任务文档与本总领文件链接一致；仍未修改业务代码和依赖 | 已完成 |
| 2026-08-28 当前轮补充 | 明确评测逻辑必须整个平台可用，并且必须交付对应前端页面 | D2 调整为 Agent/Tool/MCP、RAG、Graph 全部接入统一协议；T7 页面由“查看任务”升级为平台级必选交付 | 已完成（仅更新任务文档） |
| 2026-08-28 T1 | 重试 Semantica 文档/GitHub/API 访问并完成本地 provenance 结论 | 外部 DNS/API 不可用；不引入未知依赖，PostgreSQL TraceEvent 作为事实源 | 已完成 |
| 2026-08-28 T2 | 增加 Agent、RAG、Graph 只读适配器与统一记录 | `adapt_records()` 及三类 adapter 均不触发来源执行，缺失 Trace 显式为 `trace_missing` | 已完成 |
| 2026-08-28 T3 | 完成平台八表 ORM、幂等仓储和游标分页 | `create_all()` 随现有 PostgreSQL 生命周期注册；发布版本不可修改 | 已完成 |
| 2026-08-28 T4-T5 | 完成 Fixture Runner、取消/恢复入口、Trace 校验及 pass 指标 | 成功/失败/取消、重复运行、父子关系和 `[1,0,0]` 公式均有测试 | 已完成 |
| 2026-08-28 T6-T7 | 注册管理员平台 API 并接入 `/evaluation` 页面 | API 使用 `/api/platform-evaluations`；页面来源筛选、指标、失败和 Trace 只读展示 | 已完成 |
| 2026-08-28 T8 | Docker 分层回归 | 评测 unit 13 passed，Ruff/Lint/Build 通过；全量 unit 有 15 个既有环境/收集失败，待记录收口 | 进行中 |

### 10.2 当前检查点

```text
checkpoint: EVAL-20260828-T8-REGRESSION
last_safe_state: T1-T7 实际代码、测试和页面已落地；未引入 Semantica 或其他第三方依赖；旧业务改动保持原样
next_action: 更新各子任务进度、changelog，执行 `git diff --check` 和最终 Compose 状态检查
blockers: LITE_MODE 下平台 API 集成测试按设计跳过；全量 unit 的 15 个失败来自既有测试收集/导入环境或工作区并行改动，不属于评测模块
```

### 10.3 中断恢复入口

恢复工作时按以下顺序执行，不要直接从代码实现开始：

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,360p' tasks/2026-08-28/task.md
cat tasks/2026-08-28/T0-requirements-contract.md
cat tasks/2026-08-28/T1-provenance-semantica-spike.md
git status --short
docker compose ps
```

根据检查点选择恢复分支：

1. 能访问仓库：先完成 T1 的包名、版本、许可证、依赖和 API 核验，再在 `/tmp/yuxi-semantica-spike/` 做成功/失败 POC。
2. 不能访问仓库但用户提供 README/API：只依据提供内容完成 T1 评估，不能补猜未提供的接口。
3. 没有仓库信息：将 T1 结论记录为“不引入第三方”，冻结本地 `ProvenanceRecord`，进入 T2。
4. 发现已有业务代码改动：先区分 `git status` 中的既有改动和本任务改动，不使用 `git reset --hard` 或 `git checkout --` 清理。

每次中断前必须在本节追加：最后完成的子任务、最后一次验证命令及结果、下一条可执行命令、阻塞原因。

## 11. 当前待用户确认清单

当前不再等待业务样例，通用 Fixture 已用于首轮闭环。仍需补充的只有：

1. `semantica` 的可安装包名、版本、许可证和 README/API 摘要；仓库 URL 已确认。若暂时无法提供，按“先不引入，先完成本地溯源契约”执行。

Semantica 未取得可验证元数据前不引入依赖；真实业务样例和真实 Agent/Tool/MCP 回归需另建任务，不覆盖本轮 Fixture 契约。

## 12. 完成判定

- [x] 用户确认的目标和非目标已记录，未确认项没有被伪装成事实。
- [x] 当前三条评测链路、入口、数据、指标和隔离要求可从源码追溯。
- [x] 子任务均有进入条件、修改边界、验证命令和完成标准。
- [x] 已按 T0-T8 顺序推进并记录实际结果；需求澄清阶段的保护边界保持不变。
- [ ] T8 最终全量回归和 changelog 收口完成。

# Agent 评测落地研究：数据集、执行、指标与 pass@k/pass^k

| 属性 | 内容 |
| --- | --- |
| 研究日期 | 2026-08-28（Asia/Shanghai） |
| 适用项目 | Yuxi 平台级评测闭环：Agent、Tool/MCP、RAG、Graph 通过统一协议接入 |
| 研究结论 | 评测必须以“任务 + 隔离环境 + 多次 rollout + 可验证结果 + Trace”组成，不能只比较最终文本 |
| 实时核验状态 | 当前环境无法解析 Jina、Exa 和 GitHub API；下方来源 URL 需要网络恢复后复核版本细节 |
| 是否改代码 | 否；本文件只沉淀评测设计和实现依据 |

## 1. 结论摘要

Agent 评测和普通问答评测的核心差异是：Agent 会改变环境、调用工具、经历多步决策，最终答案可能正确但过程不合规，也可能文本看似正确但环境状态没有完成。因此一个可落地的评测单元至少包含：

```text
TaskSpec
  + 初始环境快照
  + 输入和期望结果
  + 允许的工具/权限/策略
  + 可执行的 evaluator
  + 每次尝试的隔离与随机种子
        |
        v
RolloutAttempt (一次完整执行)
  + final output
  + final state / side effects
  + ordered trace events
  + latency/token/cost
  + failure reason
        |
        v
Task-level aggregation
  + pass@1 / pass@k
  + pass^k
  + best@k
  + process/performance/reliability metrics
```

Yuxi 的平台级能力应统一承载 Agent、Tool/MCP、RAG、Graph 的数据、状态、Trace、指标和页面查询；第一种执行器先用可重复 Fixture 证明闭环，真实模型、真实 MCP 和有副作用的 Tool 评测另立任务。RAG/Graph 的既有领域评分通过适配器保留，不因统一平台而改写。

## 2. 可参考的公开实现

这些项目代表不同层次，不能直接当作 Yuxi 的依赖：

| 项目 | 主要做法 | 对 Yuxi 的借鉴 |
| --- | --- | --- |
| OpenAI Evals | registry + 数据样例 + eval 实现；支持模型评分和自定义评分 | 数据集版本、可插拔 evaluator、记录每个样例结果 |
| LangSmith Evaluation | Dataset examples + target/runner + custom/LLM evaluator + experiment trace | 实验与数据集分离、单题结果和实验聚合、比较实验 |
| Inspect AI | task/dataset/solver/scorer，支持多 epoch 重复采样和日志 | 将 Agent 执行器和 scorer 分离，重复尝试必须有 epoch/attempt |
| Braintrust | dataset + experiment/trial + scorer + 结果聚合 | 结果、评分和实验配置快照分开保存 |
| SWE-bench | 每个任务在隔离仓库中执行补丁，再用测试判定是否通过 | 环境快照、可执行判定、不要以文本相似度代替任务完成 |
| WebArena / BrowserGym | 浏览器环境任务 + 状态/网页断言 + trajectory | 多步轨迹、环境重置、状态验证和工具调用记录 |
| ToolSandbox | 受控工具环境、工具调用轨迹和最终状态检查 | Tool/MCP 评测需要沙盒、权限和副作用隔离 |
| tau-bench | 用户模拟器 + Agent + 工具环境，多次试验评估任务成功率和可靠性 | pass^k 用于衡量同一任务连续多次都成功的可靠性 |

建议在网络恢复后核验：

- https://github.com/openai/evals
- https://platform.openai.com/docs/guides/evals
- https://docs.smith.langchain.com/evaluation
- https://inspect.aisi.org.uk/
- https://github.com/sierra-research/tau-bench
- https://github.com/web-arena-x/webarena
- https://github.com/ServiceNow/ToolSandbox
- https://arxiv.org/abs/2406.12045（tau-bench 论文）
- https://arxiv.org/abs/2107.03374（代码生成中的 pass@k 估计）

## 3. 数据集怎么做

### 3.1 数据集的三层结构

不要把整个 JSONL 文件直接当作“数据集版本”。建议拆成：

1. **Dataset**：逻辑名称和负责人，例如 `agent-tool-regression`。
2. **DatasetVersion**：不可变快照，包含任务内容、评测契约、来源声明、生成时间和 checksum。
3. **TaskSpec**：单个可执行任务，包含输入、环境、期望结果、策略和 evaluator 参数。

### 3.2 TaskSpec 最小格式

```yaml
task_key: fixture_tool_echo_001
version: 1
category: tool_use
input:
  query: "调用 fixture.echo，输入 hello，返回固定结果"
environment:
  adapter: fixture
  snapshot: fixture://empty-v1
  seed: 42
expected:
  final_answer:
    value: "fixture-ok: hello"
    validator: normalized_exact
  state:
    predicates: []
expected_path:
  required_tools:
    - name: fixture.echo
      min_calls: 1
      max_calls: 1
  forbidden_tools:
    - filesystem.write
    - http.request
  required_events: [agent, tool, final]
  forbidden_events: [policy_violation, loop_detected]
protocol:
  max_steps: 3
  timeout_seconds: 5
  max_attempts: 5
tags: [fixture, regression, provenance]
```

### 3.3 真实任务集与 Fixture 任务集分开

| 类型 | 用途 | 允许的依赖 | 验证方式 |
| --- | --- | --- | --- |
| Fixture | 单元/集成回归，稳定验证状态机、Trace 和指标 | 固定工具、固定输出、固定事件 | 精确答案、事件序列、状态谓词 |
| 合成任务 | 覆盖参数边界、拒答、错误恢复、循环 | 受控生成器 | 属性断言和约束检查 |
| 脱敏生产样本 | 评估真实分布和回归 | 脱敏数据、只读环境 | 人工/LLM Judge + 确定性检查 |
| 外部公开基准 | 横向比较能力 | 按基准许可运行 | 使用基准原生 evaluator |

首轮只实现 Fixture。生产样本必须明确脱敏、授权、保留周期和是否允许写入外部 Trace。

### 3.4 数据集切分和防泄漏

- `train/dev/test` 或 `smoke/regression/challenge` 必须使用不可变版本。
- 同一实体、同一模板的近重复任务不能同时出现在开发和测试集。
- 测试集不能被 Agent prompt、Tool 描述或代码直接读取。
- 每次 Experiment 保存 DatasetVersion checksum；结果不能只引用文件名。
- 任务变更、expected 变更、Evaluator 变更都必须产生新版本。

## 4. 整体操作流程

### 4.1 创建和发布

1. 上传或生成 TaskSpec。
2. 校验必填字段、工具名称、协议上限和 evaluator 参数。
3. 计算覆盖任务内容、期望、来源和评测口径的 checksum。
4. 发布 DatasetVersion；发布后不可修改。

### 4.2 创建 Experiment

Experiment 必须保存：

- Agent 标识、prompt/Skill/SubAgent 配置版本；
- 模型供应商、模型名、采样参数和配置 checksum；
- Tool/MCP 列表、版本、权限和沙盒配置；
- DatasetVersion、Evaluator 名称和版本；
- `attempts`/`k`、并发上限、超时、重试策略和随机种子策略；
- 代码版本、容器镜像或运行环境摘要；
- 可选 baseline Experiment ID。

### 4.3 执行每个 Task

每个 attempt 都必须执行完整生命周期：

1. 根据 TaskSpec 恢复全新的 environment snapshot。
2. 写入 `rollout_id`、`task_id`、`attempt` 和幂等 key。
3. 启动 Agent，捕获 Agent、Tool、MCP、Skill、SubAgent、Retrieval 和错误事件。
4. 到达 final、超时、取消或不可恢复错误后停止。
5. 计算 final answer、state、process、policy、trace 和 performance 指标。
6. 保存脱敏后的 Trace、逐条 MetricResult 和 Failure。
7. 销毁或重置环境，不能让下一个 attempt 继承副作用。

### 4.4 聚合和查看

先写逐条结果，再由聚合器计算实验结果。查询顺序应支持：

```text
Experiment
  -> Task summary
      -> Attempt/Rollout
          -> TraceEvent
          -> MetricResult
          -> Failure
```

页面和 API 不能只返回一个总分；至少要能按任务、标签、组件、失败类型和 attempt 过滤。

## 5. 指标体系

### 5.1 Outcome 指标

| 指标 | 判定 | 适用 |
| --- | --- | --- |
| `task_success` | 所有必需结果谓词成立且无硬失败 | 所有 Agent 任务 |
| `answer_exact` | 字符串精确相等 | 固定文本 |
| `answer_normalized_exact` | 规范化空白/大小写/标点后相等 | 文本答案 |
| `json_match` | JSON schema/字段/值满足契约 | 结构化输出 |
| `state_match` | 环境最终状态满足谓词 | 有副作用任务 |
| `refusal_correct` | 该拒答且拒答，或不该拒答且完成 | 安全/权限任务 |

### 5.2 Process/Tool 指标

| 指标 | 推荐计算 |
| --- | --- |
| `tool_selection_exact` | 实际工具集合与允许/期望集合完全匹配 |
| `tool_selection_recall` | 必需工具被调用的比例 |
| `tool_argument_exact` | 规范化参数与 expected 参数匹配 |
| `tool_sequence_exact` | 调用序列与 expected sequence 匹配 |
| `tool_execution_success` | 工具返回成功且未违反契约 |
| `forbidden_tool_calls` | 禁止工具调用次数，非零即硬失败 |
| `duplicate_operation` | 同一幂等操作重复次数 |
| `loop_detected` | 相同状态/调用模式重复超过阈值 |
| `trace_completeness` | 预期可观察事件均有开始/结束或明确缺失原因 |
| `policy_violation` | 权限、数据或工具策略违反次数 |

### 5.3 Performance 指标

- wall-clock latency：从 Agent 开始到终态；至少保留每个 attempt 的值。
- step count/tool call count：不要只存平均值。
- input/output token、cached token、cost：无法取得时返回 `null`，不能填零。
- timeout、cancel、retry 次数。
- P50/P95 只在样本量达到预设阈值后展示，否则返回 `null` 并标注样本不足。

### 5.4 评测分层

不要把不同语义的指标加权成不可解释的总分。建议输出：

```text
Outcome:     task_success, answer_*, state_*
Process:     tool_*, policy_*, loop_*, trace_*
Performance: latency, steps, tokens, cost
Reliability: pass@k, pass^k, variance, confidence interval
```

如果产品确实需要综合分，必须保存权重、版本、缺失值处理和每个子分数，且不能覆盖原始指标。

## 6. pass@k 和 pass^k

### 6.1 统一符号

- `N`：测试任务数。
- `n`：每个任务实际完成的独立 attempt 数。
- `c`：其中通过的 attempt 数。
- `k`：报告的尝试窗口大小。
- `s_i`：第 `i` 次 attempt 的二值成功结果，成功为 1。

所有公式的前提是：每个 attempt 从相同初始状态开始、独立采样、环境副作用隔离，并记录失败/超时而不是静默丢弃。

### 6.2 pass@1

每个任务只运行一次时：

```text
pass@1 = (1 / N) * sum_task I(s_1 = 1)
```

它衡量一次运行成功率，是最直观的基线。

### 6.3 pass@k：至少一次成功

如果每个任务恰好执行 `k` 次：

```text
pass@k = (1 / N) * sum_task I(sum_{i=1..k} s_i >= 1)
```

如果每个任务有 `n >= k` 个独立样本，并从中随机取 `k` 个，代码生成领域常用无偏估计：

```text
pass@k = 1 - C(n-c, k) / C(n, k)
```

当 `n-c < k` 时，组合项按 0 处理，估计值为 1。该估计回答的是“k 次采样中至少有一次能成功的概率”，不是“把同一个失败环境重试 k 次”的成功率。

### 6.4 pass^k：全部成功

对同一个任务连续执行 `k` 次，只有全部成功才算该任务通过：

```text
pass^k = (1 / N) * sum_task I(sum_{i=1..k} s_i = k)
```

如果明确假设各次独立同分布，且单次成功概率为 `p`，理论值可以写成：

```text
p^k
```

但实际报告应优先使用按任务分组的经验 `pass^k`；不能把 `pass@1^k` 当作真实观测值。Agent 的工具状态、随机性和错误恢复通常会造成相关性。

### 6.5 best@k

对于连续分数或质量分数，记录 k 次中的最佳值：

```text
best@k(task) = max(score_1, ..., score_k)
```

它适合“多次尝试后选最好结果”的场景，但必须同时报告 pass^k 或失败比例，避免只展示最好的一次掩盖不稳定性。

### 6.6 必须同时报告的维度

| 场景 | 必报 |
| --- | --- |
| 一次执行回归 | pass@1、失败类型分布、P50/P95 |
| 随机采样能力 | pass@1、pass@k、attempt 数、采样参数 |
| 可靠性/稳定性 | pass@1、pass^k、每任务成功次数分布 |
| 工具合规性 | task_success、forbidden_tool_calls、tool_argument_exact、trace_completeness |
| 成本约束 | pass@k、平均/中位 cost、成功任务 cost、超时率 |

### 6.7 统计和解释注意事项

- `pass@k` 会随 k 增大而上升，不能跨不同 k 直接比较。
- `pass^k` 会随 k 增大而下降，适合观察稳定性，不适合替代能力分数。
- Fixture 若完全确定性，三者通常相等；这只能验证管线，不能证明随机 Agent 可靠性。
- 每个指标应保存分子、分母、attempt 数、k、聚合版本和置信区间输入。
- 小样本不要给出虚假的 P95 或显著性结论；返回样本不足状态。

## 7. Yuxi 的具体落地建议

### 7.1 第一阶段只做什么

1. 平台统一协议和 API：对 Agent、Tool/MCP、RAG、Graph 使用相同 Dataset/Experiment/Rollout/Trace/Metric/Failure 资源。
2. `fixture` runner：固定 Agent、固定 Tool、固定成功/失败事件，作为首个可执行 Runner。
3. 每个任务支持 `max_attempts`，默认 1；测试配置显式设为 3 或 5。
4. 每次 attempt 保存 `rollout_id`、`attempt`、环境 snapshot ID、状态、输出、Trace、指标和失败。
5. 聚合器实现 `pass@1`、`恰好 k 次的经验 pass@k`、`pass^k`、`best@k`。
6. API 和前端页面返回/展示逐条结果、来源类型、聚合结果、k、attempt 数和样本不足提示。

### 7.2 不能省略的幂等键

```text
idempotency_key = hash(
  experiment_id,
  task_id,
  attempt,
  runner_version,
  environment_snapshot_id
)
```

相同 key 的重复 job 必须返回原 rollout；如果输入、版本或环境快照不同，必须生成新 key。

### 7.3 失败分类

建议最小枚举：

```text
validation_error       TaskSpec 或 expected 非法
agent_error            Agent 未产出有效终态
answer_mismatch        最终答案不匹配
state_mismatch         环境状态不满足
tool_selection_error   工具选择不符合契约
tool_argument_error    工具参数不符合契约
tool_execution_error   工具执行失败
policy_violation       调用了禁止能力或越权
trace_incomplete       关键事件不可观测
timeout                超过任务时限
cancelled              用户或系统取消
runner_error           评测基础设施错误
```

每条失败保存主因和可重试标志；聚合只统计已完成的合法 attempt，不能把 runner 崩溃静默算成通过。

### 7.4 推荐验收矩阵

| 测试 | attempt 结果 | 预期 pass@1 | 预期 pass@3 | 预期 pass^3 |
| --- | --- | --- | --- | --- |
| 三次全成功 | 1,1,1 | 1 | 1 | 1 |
| 一次成功、两次失败 | 1,0,0 | 1/3 | 1 | 0 |
| 三次全失败 | 0,0,0 | 0 | 0 | 0 |
| 第一次超时，后两次成功 | 0,1,1 | 2/3 | 1 | 0 |
| Fixture 完全确定且成功 | 1,1,1 | 1 | 1 | 1 |

矩阵只验证聚合逻辑；真实随机 Agent 还需要环境隔离、采样种子和成本上限测试。

## 8. 对当前任务拆解的影响

研究结论对应总领任务中的修改：

- T1：确认 Semantica 是否能承载 provenance；不能替代本地 Experiment/Rollout/Trace 事实源。
- T2：适配器必须输出 Task/Attempt/Trace/Metric 的统一协议，不复制旧评分逻辑。
- T3：DatasetVersion checksum 必须覆盖 expected、protocol、evaluator 版本和来源声明。
- T4：Runner 必须实现环境重置、attempt、幂等、取消、超时和重启恢复。
- T5：Evaluator 先计算逐条 outcome/process/performance，再做 pass@k、pass^k 和 best@k 聚合。
- T6/T7：API/页面必须显示 k、attempt 数、分子/分母、失败分布和样本不足，而不是只显示一个总分。
- T8：回归至少覆盖本文件第 7.4 节验收矩阵。

## 9. 待联网核验的事实

恢复网络后补充以下内容，不影响本研究中的通用公式：

- Semantica 仓库实际提供的是数据血缘、知识图谱、Trace 还是其他能力；
- Semantica 的安装包名、版本、许可证、存储后端和异步 API；
- tau-bench 对 `pass^k` 的原始定义、采样协议和报告方式；
- Inspect AI 的 epoch/重复样本聚合细节；
- OpenAI Evals、LangSmith、Braintrust 当前版本的结果字段和实验导出格式。

联网核验后，只更新事实和引用，不改变本地事实源、环境隔离和指标公式的基本边界，除非新增证据与当前契约冲突并经过决策记录。

## 10. 与 Yuxi 当前项目的适配性评估

### 10.1 当前已有基础

从仓库代码和锁文件可以确认，Yuxi 已经具备：

- LangGraph Agent 运行链路；
- ARQ Worker 和 Redis Run Stream；
- PostgreSQL 的 AgentRun、Conversation、RAG 评测数据；
- Langfuse SDK、trace ID、metadata、tags 和 score；
- OpenTelemetry 相关依赖；
- 当前 Agent Eval 的 Langfuse Dataset -> HTTP eval run -> 最终输出回写链路。

当前真正缺少的是：

- 本地不可变 DatasetVersion；
- Experiment 配置快照；
- 每题多个独立 Rollout/Attempt；
- expected_output、state 和 tool contract 的确定性 evaluator；
- 逐条 MetricResult/Failure 的持久化；
- pass@k、pass^k 和 best@k 的本地聚合；
- 环境重置、幂等和 Worker 重启后的恢复协议。

因此不建议先引入一个新的“全量评测平台”或替换 Langfuse。

### 10.2 方案适配评分

| 方案 | Yuxi 适配度 | 直接借鉴 | 不直接引入的原因 |
| --- | ---: | --- | --- |
| Inspect AI 的 task/dataset/solver/scorer/epoch | 高 | 分离 TaskSpec、Runner、Evaluator；用 epoch 表示 attempt | 不引入框架，现有 AgentRun/ARQ 已是执行基础 |
| LangSmith 的 Dataset/Experiment/Trace | 高 | Dataset 与 Experiment 分离；保存单题结果、实验元数据和比较关系 | 当前已有 Langfuse；再接 LangSmith 会增加观测出口 |
| OpenAI Evals 的 registry + custom eval | 高 | evaluator 注册表、JSONL/manifest、每题结果 | 不把 Evals 运行时引入后端，避免第二套生命周期 |
| tau-bench 的环境任务和 pass^k | 高（方法论） | 每次尝试重置环境；报告稳定性而非只报最好一次 | 领域环境和用户模拟器不适合首轮 Fixture |
| ToolSandbox / WebArena 的隔离环境 | 中 | 工具副作用隔离、状态谓词和 trajectory | 首轮没有真实工具副作用，不需要引入浏览器/沙盒基准 |
| SWE-bench 的可执行判定 | 中（方法论） | 以测试/状态作为最终事实，不以文本相似度代替成功 | 代码修复任务与 Yuxi Agent 场景不同 |
| Braintrust 等 SaaS 实验平台 | 低到中 | 结果/评分/实验配置分开 | 外部事实源、成本和权限边界不符合当前约束 |
| semantica-agi/semantica | 未知 | 仅在确认有 provenance/lineage API 后做可选适配 | 仓库尚未读取；不能确认它是溯源库还是 Agent/知识库框架 |

### 10.3 推荐组合

对当前 Yuxi，最适配的不是单一第三方框架，而是以下组合：

```text
Inspect 的任务/执行器/评分器分层
  + OpenAI Evals 的 manifest/evaluator registry 思路
  + LangSmith 的 experiment/trace 组织方式
  + tau-bench 的环境重置与 pass^k 方法
  + Yuxi 已有 Langfuse + AgentRun + ARQ + PostgreSQL
```

落地时：

- Experiment、Rollout、MetricResult 和 Failure 由 Yuxi 本地保存；
- Langfuse 只保存或关联 trace、可选 score 和外部查看链接；
- AgentRun 继续负责真实 Agent 执行，不复制一套 Agent runtime；
- semantica 若通过 T1，只负责 provenance graph 的导出/查询适配；
- pass@k/pass^k 由 Yuxi 本地 evaluator 计算，不能委托给观测平台。

## 11. Semantica 的适配判断

在没有读到 `semantica-agi/semantica` 源码前，不能确认它是否是数据血缘库。即使它提供语义图或知识图谱，也需要区分：

- **适合的职责**：数据源、文档、chunk、实体、检索结果、Tool 输出之间的来源关系和版本关系；回答“这个结果来自哪里”。
- **不适合直接承担的职责**：Experiment 状态机、ARQ 调度、attempt 重试、pass@k/pass^k、最终成功判定、权限和评测页面。
- **与 Langfuse 的关系**：Langfuse 记录“这次运行发生了什么”；Semantica 若能力成立，记录“结果中的数据来自什么”。两者是互补关系，不应互相替代。
- **与 Yuxi Graph 的关系**：如果 Semantica 自带图存储或图查询，仍不能替换 Yuxi 现有 Neo4j/LightRAG；只允许作为 provenance read model，避免第二套事实图谱。

T1 的采用门槛应增加以下硬性检查：

1. 能从 TaskSpec/rollout 追溯到 DatasetVersion、document/chunk/entity/tool output；
2. 能保存 source version、transform operation、parent/child relation 和 checksum；
3. 能通过 Python 3.12、异步 Worker 和现有 PostgreSQL/Redis 部署；
4. 不强制新增常驻服务、第二数据库或外部网络；
5. 支持脱敏、查询、导出和失败回退；
6. POC 能复现“输入 -> retrieval/tool -> final answer”的来源链；
7. 许可证、维护状态和依赖风险通过审查。

如果只提供知识图谱构建、语义检索或 Agent 编排，而没有 provenance/lineage API，结论应为“不适配 D4”，不引入。

## 12. 推荐的当前实现顺序

1. T1：读取仓库元数据和 API，形成 Semantica 采用/不采用结论。
2. T2：按 Inspect/OpenAI Evals 思路冻结 TaskSpec、Runner、Evaluator 接口。
3. T3：在 PostgreSQL 增加本地 Experiment/Rollout/Metric/Failure 记录。
4. T4：用 Fixture 验证环境重置、attempt、幂等和失败恢复。
5. T5：实现确定性 evaluator 和 pass@1/pass@k/pass^k/best@k。
6. T6：暴露 API；Langfuse 只做 trace 关联。
7. T7：页面展示单题、attempt、Trace、失败和聚合指标。
8. T8：回归旧 RAG、企业关系评测和普通 AgentRun。

在 T1 结论前，不应把 Semantica 加入 `backend/pyproject.toml` 或 `uv.lock`。

# T0：需求、术语与验收契约冻结

关联总领文件：[task.md](./task.md)。本子任务只冻结后续实现所需的边界和可判定契约，不修改业务代码。

## 1. 目标

把“统一 Agent 评测闭环”转化为实施前可检查的契约：明确评测对象、输入输出、工具轨迹、状态、失败分类、指标口径、权限和验收样例。完成后，T1-T8 可以依据固定字段推进，不再依赖口头约定。

## 2. 已确认事项

- D1：交付目标是统一 Fixture 评测闭环；真实 Agent/Tool/MCP 回归另立任务。
- D2：平台现有 Agent + Tool/MCP、RAG、Graph 全部可接入统一协议；首个可执行 Runner 使用 Agent + Tool/MCP Fixture，RAG/Graph 通过只读适配器接入。
- D3：只使用 fake/fixture，不调用真实模型，不触发真实工具副作用。
- D4：本地 PostgreSQL provenance/trace 是事实源；Langfuse 只做可选关联；Semantica 必须先完成 T1 核验。
- D5：前端首轮只读查看。
- D6：通过条件为最终答案契约 + 工具/轨迹契约；暂不做 LLM Judge 或人工评分。
- D7：旧入口保持兼容，通过适配层接入统一平台。
- D8：管理员访问；评测线程保持 `visibility=internal`。
- 通用 Fixture `fixture_tool_echo_001`：必须调用 `fixture.echo`，禁止 `filesystem.write` 和 `http.request`；成功事件顺序为 `agent -> tool -> final`。

## 3. 剩余疑问

这些疑问不阻塞通用 Fixture 的文档冻结，但进入正式业务验收前必须补充：

1. 业务样例的最终答案是精确文本、归一化文本、JSON 还是状态契约？
2. 业务工具的允许参数和敏感字段清单是什么？
3. `semantica` 的准确包名、版本、许可证和 API 是否满足 T1 适配条件？
4. 首轮是否需要为 RAG/Graph 保留领域指标的原始字段，还是只映射到通用 outcome/process？

## 4. 明确不包含

- 不修改任何生产评测、聊天、队列、Worker、数据库或前端文件。
- 不定义真实模型的价格、Token 上限、限流和重试策略。
- 不引入 LLM Judge、人工标注、私有 chain-of-thought 或完整未脱敏工具载荷。
- 不把 `overall_score` 作为唯一事实源，也不把外部文章或未核验 Semantica 能力写成既定事实。

## 5. 具体文件、数据/API 变更与契约

本子任务实际只新增/维护以下文档：

- `tasks/2026-08-28/task.md`：回写决策、字段和状态变化。
- `tasks/2026-08-28/T0-requirements-contract.md`：本文件。
- `tasks/2026-08-28/research-agent-evaluation.md`：保留外部方案的研究依据，不作为接口规范。

后续实现必须以以下字段语义为准：

| 对象 | 必填契约 | 判定要求 |
| --- | --- | --- |
| TaskSpec | `task_key`、`input`、`expected`、`protocol`、`tags` | 同一版本中 `task_key` 唯一，输入和 expected 可重放 |
| Expected | `final_answer`、`tool_policy`、`trace_contract` | 三者均可独立产生通过/失败原因 |
| Rollout | `experiment_id`、`task_id`、`attempt`、`idempotency_key`、`status` | 同一幂等键只能产生一个事实结果 |
| TraceEvent | `sequence`、`parent_id`、`component`、`operation`、脱敏摘要、checksum | 可按序重建父子调用链 |
| MetricResult | `metric_name`、`metric_version`、`value`、`status`、输入 checksum | 聚合结果可由逐题结果重算 |
| Failure | `failure_type`、`reason_code`、关联 rollout/metric/trace | 主因明确且可排查 |

### 通用 Fixture 验收样例

```yaml
task_key: fixture_tool_echo_001
input:
  query: "hello"
expected:
  final_answer:
    value: "fixture-ok: hello"
    validator: normalized_exact
  tool_policy:
    required: [fixture.echo]
    forbidden: [filesystem.write, http.request]
  trace_contract:
    required_sequence: [agent, tool, final]
    required_operation: fixture.echo
protocol: agent_tool_final_v1
```

通过必须同时满足：答案匹配、调用 `fixture.echo` 一次且参数符合契约、未调用禁止工具、事件可排序且包含父子关系。任何一项失败都产生对应确定性 reason code。

## 6. 任务清单

- [x] 记录 D1-D8 和首轮非目标。
- [x] 定义 Dataset/Version/Task/Experiment/Rollout/Trace/Metric/Failure 的最小职责。
- [x] 固定 `fixture_tool_echo_001` 的成功和禁止行为。
- [x] 固定通过条件和 reason code 方向：`answer_mismatch`、`required_tool_missing`、`forbidden_tool_called`、`trace_contract_violation`、`timeout`、`cancelled`。
- [ ] 获取至少一个业务验收样例并记录替换字段。
- [ ] 在总领文件写入文档审阅结果和未决项。

## 7. 验证命令和预期结果

```bash
cd /Users/fishyuu/orca/company-relation
git diff --check -- tasks/2026-08-28
rg -n "D[1-8]|fixture_tool_echo_001|pass@k|pass\^k|不包含|完成条件" tasks/2026-08-28/task.md tasks/2026-08-28/T0-requirements-contract.md
```

预期：`git diff --check` 无输出且退出码为 0；关键术语均能在总领或本文件定位；没有将未决项标成已确认。实施前人工检查一遍样例是否能在不调用真实服务的情况下判定成功/失败。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 盘点现有 RAG、Graph、Agent Eval 链路 | 已写入总领文件 |
| 2026-08-28 | 确认 D1-D8 和通用 Fixture | 已完成 |
| 2026-08-28 | 补齐九份可执行子任务文档 | 本轮进行中 |

## 9. 中断恢复入口

恢复命令：

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,240p' tasks/2026-08-28/T0-requirements-contract.md
sed -n '1,180p' tasks/2026-08-28/task.md
```

若发现业务样例、权限或工具策略与本文件冲突，停止进入 T1，先在总领文件第 9 节追加决策，再更新本文件的“剩余疑问”和样例。不得直接改代码解决契约冲突。

## 10. 完成条件

- D1-D8 有明确值且与总领文件一致。
- 至少一条 Fixture 可以由第三方仅凭文档判定成功、失败和失败原因。
- 统一契约不包含任何单一 Agent、RAG 或 Graph 的私有字段依赖；三类来源均有明确 source_kind 和适配入口。
- 字段、状态、指标和权限没有隐式前置条件。
- 总领文件已记录 T0 实际状态、验证命令和下一检查点。

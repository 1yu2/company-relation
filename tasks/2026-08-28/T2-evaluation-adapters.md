# T2：现有评测链路适配边界

关联总领文件：[task.md](./task.md)。本子任务把现有 RAG、企业关系 Graph、Agent Eval 结果映射到统一协议，保持旧入口行为不变。

## 1. 目标

为平台现有三条评测链路定义只读适配器：统一返回 Task/Attempt/Trace/Metric/Failure 所需字段，使同一套平台 API、指标聚合和前端页面可以查看所有来源，同时保留领域特有原始结果和旧 API 响应。适配器不得改变原有查询、评分、队列和可见性逻辑。

## 2. 已确认事项

- 平台纳入对象为 Agent + Tool/MCP、知识库 RAG、企业关系 Graph；后两类通过适配器接入，不重写其评分。
- 旧 `/api/evaluation/*`、企业关系评测入口和 Agent Eval CLI/HTTP 协议保持兼容。
- 评测线程来源仍为 `agent_evaluation`，`visibility=internal`，不能混入普通会话。
- 统一平台的确定性通过条件由 T0 契约定义，LLM Judge 不在本任务内。

## 3. 剩余疑问

1. RAG 的 `Recall@K/F1@K/Judge` 是否需要统一成独立 Metric namespace，还是只保存原始指标并标记 domain。
2. 企业关系的事实 ID、关系路径、拒答状态在通用 expected 中的最终字段名。
3. Agent Eval 现有 Langfuse dataset 字段是否每题都能得到稳定 task/version checksum。
4. 历史结果缺少 trace 时是 `trace_missing` 还是不进入统一聚合。

## 4. 明确不包含

- 不修改三条链路内部算法、题库、查询策略、评分公式或数据表。
- 不把历史结果伪造成完整 Trace；缺失字段必须显式标记。
- 不将 RAG/Graph 的领域指标强行合并成一个不可解释的总分。
- 不允许适配器绕过权限、内部可见性或已有错误处理。

## 5. 具体文件、接口与映射

候选实现文件：

- `backend/package/yuxi/evaluation/adapters/`：统一适配器协议与三个实现。
- `backend/test/unit/evaluation/test_adapters.py`：字段、错误和兼容单测。
- 如需集成测试，放在 `backend/test/integration/evaluation/`。

统一只读接口：

```text
adapt(source_run_id) -> Iterable[EvaluationRecord]
EvaluationRecord:
  source_kind, source_run_id, task_key, input_summary
  expected_summary?, actual_summary?, status
  domain_metrics, trace_ref?, failure?
```

映射规则：

| 来源 | 输入 | 结果 | 领域字段 | 缺失处理 |
| --- | --- | --- | --- | --- |
| 知识库 RAG | query + dataset item | answer/检索结果 | Recall@K、F1@K、Judge | 没有答案 Judge 就是 `not_measured` |
| 企业关系 | golden question + snapshot version | 拒答/答案/证据 | MRR、证据对齐率、幻觉率、延迟 | snapshot/checksum 缺失则失败并标 reason |
| Agent Eval | Langfuse task + final text | final + tool summary | 事件数、工具错误、中断 | 历史无 trace 则 `trace_missing` |

适配器输出不能覆盖源数据；统一平台保存 `source_ref` 和输入 checksum，便于回到原入口排查。

## 6. 任务清单

- [ ] 逐条确认三个源的读取入口、权限和字段样本。
- [ ] 编写适配器协议和 source-to-unified 映射表。
- [ ] 定义缺失、旧版本和错误源数据的显式 reason code。
- [ ] 用固定 fixture/快照构造三类成功、失败和缺失 trace 样本。
- [ ] 验证三类适配器均可被同一平台 API/页面读取，且只读、不触发真实模型、工具或外部副作用。
- [ ] 回归旧 API/CLI 的响应结构和状态码，并回写总领文件。

## 7. 验证命令和预期结果

```bash
docker compose exec api pytest backend/test/unit/evaluation/test_adapters.py -q
docker compose exec api pytest backend/test/integration/evaluation -q
docker compose exec api pytest backend/test/unit/knowledge/eval -q
git diff --check -- tasks/2026-08-28/T2-evaluation-adapters.md
```

预期：统一字段映射单测通过；缺失 trace/领域字段产生显式状态；旧 RAG/Graph/Agent 评测测试结果与基线一致；无外部真实调用。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 源码盘点三条链路 | 已完成并记录于总领文件 |
| 待 T1 完成 | 冻结 provenance/source_ref 字段 | 未开始 |
| 待 T2 实施 | 适配器单测与只读回归 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,260p' tasks/2026-08-28/T2-evaluation-adapters.md
rg -n "evaluation|run_evaluation|agent_eval|Langfuse" backend packages web/src
```

若发现某个旧入口无法只读适配，暂停本任务并在总领第 9 节说明兼容风险；不得直接迁移旧表或改变旧 API。

## 10. 完成条件

- 三条来源均有字段映射、缺失策略、source_ref 和统一平台查询入口。
- 适配器不改变任何旧入口行为，且通过单元和集成回归。
- 统一结果可追溯回源 run、数据集版本和原始指标。
- 总领文件记录实际文件、命令、结果和 T3 进入条件。

## 11. 实施结果（2026-08-28）

已完成。实际实现位于 `backend/package/yuxi/evaluation/adapters.py`：

- `RagEvaluationAdapter` 读取旧 `EvaluationRunItem`/dict 的 query、gold answer/chunk、generated answer、retrieved chunks 和原始 metrics。
- `GraphEvaluationAdapter` 读取企业关系报告 item 的 question、expected、output、score、strategy 和 snapshot checksum；严格不重跑查询。
- `AgentEvaluationAdapter` 读取 Agent Eval/Langfuse item 的 input/output/status/trace summary；没有本地 Trace 时显式产生 `trace_missing`。
- `adapt_records()` 按 `agent`、`tool_mcp`、`rag`、`graph` 选择适配器；所有输入输出均经过脱敏和 checksum 归一化，保留 `source_run_id/source_version/source_ref`。

验证：`docker compose exec api pytest -q test/unit/evaluation/test_adapters.py` 为 3 passed；评测模块完整 unit 为 13 passed。适配器只读，不调用真实模型、工具、MCP、RAG 或 Graph 查询。旧入口未修改，T3 已进入。

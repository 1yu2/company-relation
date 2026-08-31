# T5：Trace、确定性评估与 pass@k/pass^k

关联总领文件：[task.md](./task.md)。本子任务消费 T4 的稳定 Rollout 和事件，归一化 Trace、计算逐题确定性指标并聚合实验结果。

## 1. 目标

提供可重放、可解释的 Trace 和指标体系：答案、工具策略、轨迹契约、失败分类、延迟等逐题结果可查询；支持 `pass@1`、`pass@k`、`pass^k`、`best@k`，且所有聚合都能从逐题事实重算。

## 2. 已确认事项

- 首轮只用确定性 validator：`normalized_exact`、required/forbidden tool、required event sequence；暂不使用 LLM Judge/人工评分。
- TraceEvent 有 sequence、parent_id、component、operation、脱敏摘要、checksum、时间和 reason code。
- `pass@1` 是单次尝试成功率；`pass@k` 是 k 次至少一次成功；`pass^k` 是 k 次全部成功；`best@k` 是 k 次最高分。
- 当每题 n 个独立样本、c 个成功时，`pass@k = 1 - C(n-c,k)/C(n,k)`；k 大于可用样本时必须显式报错或标记不可计算。
- 缺失事件、缺失 expected、重复 sequence、未授权工具都不能静默当成功。

## 3. 剩余疑问

1. 归一化文本的 Unicode、空白、大小写和 JSON canonicalization 规则是否需要业务定制。
2. 多次 attempt 是否必须统计为独立样本，还是按相同配置分组后再计算。
3. latency/token/cost 在 fixture 阶段为空时，API/页面统一返回 `null` 还是不展示。
4. RAG/Graph 领域指标是否作为独立 namespace 参与 `best@k`。

## 4. 明确不包含

- 不实现概率校准、置信区间、LLM Judge、人工评分或主观质量分。
- 不用固定数字填充未测量的 latency/token/cost/score。
- 不通过 Trace 推断未记录的模型思维过程，不保存私密链路。
- 不改变现有 RAG/Graph 指标公式，只做可选映射。

## 5. 具体文件、接口与指标变更

候选实现文件：

- `backend/package/yuxi/evaluation/trace.py`：事件排序、父子关系、脱敏和 checksum。
- `backend/package/yuxi/evaluation/evaluators/`：确定性 validators。
- `backend/package/yuxi/evaluation/metrics.py`：逐题与聚合公式。
- `backend/test/unit/evaluation/test_trace.py`、`test_metrics.py`。

逐题指标至少包括：`answer_pass`、`tool_policy_pass`、`trace_contract_pass`、`task_pass`、`latency_ms`（可空）及各自 reason code。`task_pass` 为三项契约均通过。聚合按 `task_key` 分组并报告样本数 n、成功数 c、k、公式版本、不可计算原因。

示例：attempt `[1, 0, 0]` 时 `pass@1=1/3`、`pass@3=1`、`pass^3=0`；不得将 `pass@3` 当作三次全部成功。

## 6. 任务清单

- [ ] 定义事件合法性、稳定排序、父子关系和脱敏规则。
- [ ] 实现答案、工具策略和轨迹契约 validator。
- [ ] 实现失败分类优先级，保留主因及附加原因。
- [ ] 实现逐题指标和 `pass@1/pass@k/pass^k/best@k` 聚合。
- [ ] 覆盖 n=0、c=0、c=n、k=1、k>n、重复 attempt、缺失事件等边界。
- [ ] 回写指标字典、公式版本和总领文件验证结果。

## 7. 验证命令和预期结果

```bash
docker compose exec api pytest backend/test/unit/evaluation/test_trace.py backend/test/unit/evaluation/test_metrics.py -q
docker compose exec api pytest backend/test/integration/evaluation -k metrics -q
git diff --check -- tasks/2026-08-28/T5-trace-metrics.md
```

预期：成功/失败 Fixture 的逐题 reason code 可解释；事件乱序可稳定排序，重复/缺失事件被拒绝或标记；公式边界结果与研究附录一致；未测量值为 `null`，不出现伪造分数。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 汇总公开 Agent 评测中的指标和 pass@k/pass^k 定义 | 已写入研究附录 |
| 待 T4 | 获取稳定 Rollout/Trace 事件 | 未开始 |
| 待 T5 实施 | validator、聚合和边界测试 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,300p' tasks/2026-08-28/T5-trace-metrics.md
sed -n '229,310p' tasks/2026-08-28/research-agent-evaluation.md
```

若业务要求新增主观评分或改变 pass@k 口径，暂停代码实现，在总领第 9 节记录新版本和兼容影响；不能覆写已有公式。

## 10. 完成条件

- Trace 可按 rollout 重建并通过脱敏和父子关系校验。
- 确定性 evaluator、失败分类和四种聚合指标有边界测试。
- 每个聚合值可追溯到逐题 metric 和输入 checksum。
- 总领文件记录公式版本和 T6 API 可消费的响应形状。

## 11. 实施结果（2026-08-28）

已完成首轮确定性指标和 Trace 校验：

- `provenance.validate_trace_events()` 检查空 Trace、重复 event/sequence、rollout 归属、单根和 parent 先后关系；`TraceEvent.to_dict()` 统一脱敏和 checksum。
- `evaluate_task()` 计算 `answer_match`、`tool_policy`、`trace_contract`、`task_pass`，缺失 expected 为 `not_measured`，失败 reason code 保留主因。
- `aggregate_pass_metrics()` 输出 `pass@1`、`pass@k`、`pass^k`、`best@k`、n/c/k 和样本不足计数；`[1,0,0]` 验证为 `1/3、1、0、1`。
- 失败记录通过 `metric_id` 和末尾 Trace event 关联，超时/取消/runner error 均有明确 reason code。

验证：评测模块 unit 13 passed；指标边界、缺失事件、父子顺序和脱敏测试通过。领域 RAG/Graph 公式仍由适配器保留原始指标，不在此处重算或合并。

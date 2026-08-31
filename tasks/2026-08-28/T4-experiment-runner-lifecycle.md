# T4：Experiment、Rollout 执行生命周期

关联总领文件：[task.md](./task.md)。本子任务在 T3 持久化可用后实现 Fixture Runner 的状态机、幂等、取消/超时和 Worker 恢复。

## 1. 目标

让一个 Experiment 能按发布的数据集版本生成逐题 Rollout，执行无副作用 Fixture，可靠记录成功、失败、超时、取消和重试，并在重复投递或 Worker 重启时不重复制造事实结果。

## 2. 已确认事项

- 首轮 Runner 只执行 fake/fixture；必须支持 `fixture_tool_echo_001`。
- 状态流转为 `DRAFT -> QUEUED -> RUNNING -> AGGREGATING -> FINISHED`，取消流为 `RUNNING -> CANCELLING -> CANCELLED`，不可恢复异常为 `FAILED`。
- 重复 job 使用 `(experiment_id, task_id, attempt)` 和 idempotency key 去重。
- Worker 恢复从 PostgreSQL 未终态 Experiment 和幂等 rollout 读取，不从前端时间戳或 Redis 临时状态推断完成。
- 真实模型、真实工具/MCP、副作用和高并发压测另立任务。

## 3. 剩余疑问

1. 项目现有 ARQ/Worker 的任务注册和取消 API 的准确调用方式。
2. 首轮是否允许失败 rollout 自动重试；默认 `max_attempts=1`，如启用需区分用户重跑与自动重试。
3. 超时是单题终态后继续其他题，还是直接终止 Experiment。
4. 并发上限在 Experiment 级还是 Worker 全局级控制。

## 4. 明确不包含

- 不修改普通 Chat/AgentRun 队列和 SSE 协议。
- 不实现真实模型/工具调用、网络访问、文件写入或外部副作用。
- 不在前端实现状态机，不用前端轮询结果作为状态事实源。
- 不实现 LLM Judge、成本计费或生产级调度策略。

## 5. 具体文件、状态和接口变更

候选实现文件：

- `backend/package/yuxi/evaluation/experiment_service.py`：状态转换和编排。
- `backend/package/yuxi/evaluation/runner.py`：Fixture 输入、工具策略、结果输出。
- `backend/test/unit/evaluation/test_lifecycle.py`。
- `backend/test/integration/evaluation/test_runner.py`。

每个 Rollout 至少记录：`attempt`、`idempotency_key`、`started_at`、`finished_at`、状态、输出摘要、failure、耗时和（fixture 中为空的）token/cost。Runner 事件顺序必须生成 `agent.start -> tool.fixture.echo -> agent.final`；禁止工具直接产生 `forbidden_tool_called`。

状态转换必须拒绝终态更新并返回 `invalid_state`；取消和超时要可观察、可重放，清理后不能遗留 RUNNING rollout。

## 6. 任务清单

- [ ] 对照现有 Worker/队列接口确定最小接入点。
- [ ] 实现 Experiment 状态转换表和非法转换错误。
- [ ] 实现 Fixture Runner、允许/禁止工具策略和事件回调。
- [ ] 实现逐题 rollout 创建、幂等去重、attempt 约束和终态写入。
- [ ] 实现取消、单题超时、实验失败和 Worker 恢复。
- [ ] 覆盖成功、答案失败、禁止工具、重复 job、重启恢复并回写总领。

## 7. 验证命令和预期结果

```bash
docker compose exec api pytest backend/test/unit/evaluation/test_lifecycle.py -q
docker compose exec api pytest backend/test/integration/evaluation/test_runner.py -q
docker compose exec api pytest backend/test/unit/services -k run -q
git diff --check -- tasks/2026-08-28/T4-experiment-runner-lifecycle.md
```

预期：Fixture 成功链生成一个 FINISHED 实验；失败/超时/取消有对应终态和 reason code；重复 job 不增加 rollout；Worker 重启后能继续未终态任务；现有 Run/Chat 测试不回归。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 固定状态机和恢复入口 | 已记录于总领文件 |
| 待 T3 | 确认持久化幂等约束 | 未开始 |
| 待 T4 实施 | Fixture Runner 和生命周期测试 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,300p' tasks/2026-08-28/T4-experiment-runner-lifecycle.md
rg -n "ARQ|run_worker|cancel|timeout|RunStream" backend/package backend/server backend/test
```

若状态转换与现有 Worker 语义冲突，停止新增调度分支，先记录冲突和最小适配方案；不得修改现有普通聊天队列来迁就评测。

## 10. 完成条件

- Fixture Runner 能执行成功和至少两类失败，并生成完整 Rollout/Trace 输入。
- 状态机、取消、超时、幂等和 Worker 恢复均有单元/集成覆盖。
- 现有聊天和 AgentRun 主链路测试保持通过。
- 总领文件记录 T4 检查点并允许 T5 使用稳定的 rollout/事件。

## 11. 实施结果（2026-08-28）

已完成 Fixture 生命周期的首轮实现：

- `PlatformEvaluationService.run_experiment()` 固定执行无副作用 `fixture.echo`，按题目和 attempt 写入 Rollout、Trace、Metric、Failure。
- `(experiment_id, task_id, attempt)` 生成稳定 idempotency checksum；重复 run 对终态实验只读返回摘要，不生成新结果。
- 支持答案不匹配、禁止工具、单题 timeout、runner error；`cancel_experiment()` 会将未终态 rollout 标记为 `CANCELLED` 并写入 `cancelled` failure。
- `resume_incomplete_experiments()` 从 PostgreSQL 读取 `QUEUED/RUNNING/AGGREGATING` 实验，作为 Worker/重启恢复入口；不依赖 Redis 临时状态。

验证：`docker compose exec api pytest -q test/unit/evaluation/test_experiment_service.py` 为 2 passed；PostgreSQL Fixture 探针成功运行并重复读取。首轮 Runner 仍在受控 HTTP 请求内同步执行，尚未接入 ARQ 调度、跨进程并发限流或真正的取消信号；这些属于后续生产调度任务，不改变本轮 Fixture 事实协议。

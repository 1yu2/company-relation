# T3：Dataset、Version、Task 与评测结果持久化

关联总领文件：[task.md](./task.md)。本子任务在 T1 的事实源结论和 T2 的字段映射冻结后，设计 PostgreSQL 持久化；不迁移旧评测表。

## 1. 目标

建立可复现、不可变、可查询的统一评测数据模型，确保数据集版本、实验配置、逐题 rollout、Trace、Metric、Failure 能在数据库重连和进程重启后恢复，并由逐题事实重新计算聚合结果。

## 2. 已确认事项

- 新平台使用独立命名空间/表，旧 `evaluation_*` 表和 API 不迁移、不重命名。
- DatasetVersion 发布后不可修改；Experiment 保存 Agent/Tool/MCP/Evaluator 配置快照。
- PostgreSQL 是本地事实源；Trace 与 Langfuse 的关联 ID 可空。
- 所有写入需要幂等约束、创建者/时间、版本和 checksum；敏感载荷只保存脱敏摘要。
- 管理员访问，评测数据为内部可见。

## 3. 剩余疑问

1. 项目现有 PostgreSQL migration 工具、schema 命名和事务封装的最终入口。
2. Dataset/Experiment ID 使用 UUID、ULID 还是现有 ID 类型。
3. Trace 大字段的保留期限、索引和归档策略。
4. 聚合查询是否需要按 task tag、source_kind 和 metric version 分组。

## 4. 明确不包含

- 不删除、修改或回填现有 `evaluation_*` 数据。
- 不把 Redis Stream、前端状态或 Langfuse 作为唯一结果存储。
- 不保存凭据、完整私有思维链、未脱敏工具参数和不必要的原文。
- 不在本任务实现 Runner、API 路由或页面。

## 5. 具体文件、数据和接口变更

候选实现文件（实施前以现有 ORM/迁移结构为准）：

- `backend/package/yuxi/evaluation/models.py`：领域模型/序列化约束。
- `backend/package/yuxi/repositories/platform_evaluation_repository.py`：事务、幂等和查询。
- `backend/package/yuxi/storage/postgres/manager.py`：仅在现有数据库管理器需要注册时修改。
- `backend/test/unit/evaluation/test_persistence.py`。
- `backend/test/integration/evaluation/test_persistence.py`。

建议表和关键约束：

| 表 | 关键字段/约束 |
| --- | --- |
| `platform_eval_datasets` | `dataset_id` 唯一、管理员、名称、创建时间 |
| `platform_eval_dataset_versions` | `(dataset_id, version_no)` 唯一、checksum 唯一、`published_at` 后禁止更新 |
| `platform_eval_tasks` | `(version_id, task_key)` 唯一、输入/expected JSON、protocol、tags |
| `platform_eval_experiments` | dataset version、配置快照 checksum、状态、并发/超时/最大尝试 |
| `platform_eval_rollouts` | `(experiment_id, task_id, attempt)` 唯一、idempotency key 唯一、终态字段 |
| `platform_eval_trace_events` | `(rollout_id, sequence)` 唯一、`parent_id`、脱敏摘要、checksum |
| `platform_eval_metric_results` | `(rollout_id, metric_name, metric_version)` 唯一、值/状态/输入 checksum |
| `platform_eval_failures` | rollout/metric/trace 引用、failure type、reason code、可重试标志 |

Repository 必须提供：创建草稿、发布版本（事务）、按 checksum 读取、幂等写入 rollout/event/metric、游标分页、按终态恢复未完成实验。

## 6. 任务清单

- [ ] 对照现有 migration/ORM 规范确认实际文件和命名。
- [ ] 将最小字段转换为数据库类型、索引、外键和 CHECK/UNIQUE 约束。
- [ ] 实现发布不可变和配置快照 checksum。
- [ ] 实现 rollout/trace/metric/failure 的事务和幂等写入。
- [ ] 实现 cursor 分页和未终态 Experiment 查询。
- [ ] 编写单元、PostgreSQL 集成和重连恢复测试。
- [ ] 回写总领文件的实际迁移编号、状态和结果。

## 7. 验证命令和预期结果

```bash
docker compose exec api pytest backend/test/unit/evaluation/test_persistence.py -q
docker compose exec api pytest backend/test/integration/evaluation/test_persistence.py -q
docker compose exec api pytest backend/test/integration -k evaluation -q
git diff --check -- tasks/2026-08-28/T3-persistence.md
```

预期：新表可重复执行迁移；发布后更新被拒绝；重复幂等键不产生第二条事实记录；分页不漏不重；断开并重连 PostgreSQL 后可读取相同 checksum 和终态数据。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 冻结最小记录和不可变版本原则 | 已记录于总领文件 |
| 待 T1/T2 | 确认 provenance 字段与适配器 source_ref | 未开始 |
| 待 T3 实施 | migration、Repository、DB 集成测试 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,280p' tasks/2026-08-28/T3-persistence.md
rg -n "migration|alembic|postgres|evaluation_" backend/package backend/server backend/test
docker compose ps
```

若发现现有 migration 工具不支持独立表或约束，先记录技术冲突和备选方案，暂停迁移编写，更新总领第 9 节后再继续。

## 10. 完成条件

- 新模型能保存并恢复 DatasetVersion、Task、Experiment、Rollout、Trace、Metric、Failure。
- 发布不可变、唯一约束、幂等、分页和恢复查询均有测试。
- 不影响旧 `evaluation_*` 表/API，Docker PostgreSQL 集成测试通过。
- 总领文件已有迁移文件、测试结果和 T4 进入条件。

## 11. 实施结果（2026-08-28）

已完成首轮持久化。实际实现：

- `backend/package/yuxi/storage/postgres/models_evaluation.py` 注册八张 `platform_eval_*` 表，包含外键、版本/attempt/metric 唯一约束和状态索引。
- `backend/package/yuxi/storage/postgres/manager.py` 在现有 `BusinessBase.metadata.create_all()` 生命周期前导入模型；不新增数据库服务或迁移工具。
- `backend/package/yuxi/repositories/platform_evaluation_repository.py` 提供创建、查询、发布后不可变保护、rollout/Trace/Metric/Failure 幂等写入和 base64 游标分页。
- `backend/test/unit/evaluation/test_persistence.py` 覆盖 SQLite schema、cursor 不漏不重、发布不可变和 idempotency key 去重。

验证：评测模块 13 个 unit 全部通过；Docker PostgreSQL Fixture 探针成功写入并读取数据集版本、6 个 rollout、24 条 metric、6 条 failure，重复运行不增加 rollout。当前未单独创建 Alembic migration，原因是项目事实入口使用 `create_all + ensure_*_schema`；后续若引入正式 migration 工具需另建迁移任务。

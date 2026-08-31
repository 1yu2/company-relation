# T1：溯源契约与 Semantica 可行性验证

关联总领文件：[task.md](./task.md)。本子任务先建立本地事实源契约，再决定是否增加 Semantica 可选适配器；在结论冻结前不新增项目依赖。

## 1. 目标

验证评测结果能否从 DatasetVersion -> Experiment -> Rollout -> TraceEvent -> Metric/Failure 逐级排查，并核验 `semantica-agi/semantica` 是否真的提供适合本项目的 provenance/lineage 能力。输出采用、暂缓或不采用结论及证据。

## 2. 已确认事项

- 本地 PostgreSQL 是事实源，Langfuse trace ID 可空且只做外部关联。
- Semantica 不是首轮唯一依赖，无法核验时必须继续使用本地 `ProvenanceRecord`/`TraceEvent` 契约。
- 首轮只跑 `fixture_tool_echo_001` 的成功和失败 fixture，不连接真实模型、工具、MCP 或外部服务。
- 溯源记录必须脱敏、可序列化、可按 checksum 和幂等键定位，并支持 Worker 重启后的读取。

## 3. 剩余疑问

必须在 T1 结束前有证据或明确标记未知：

1. PyPI 可安装包名、版本、许可证和 Python 3.12 支持范围。
2. 是否有 source/version/transform/parent-child/checksum 等实体和查询 API。
3. 是否兼容当前异步 Worker、PostgreSQL/Redis 部署，是否强制第二套数据库或常驻服务。
4. 是否支持脱敏、导出、失败回退、事务边界和幂等写入。
5. 依赖树、维护状态和安全扫描结果。

## 4. 明确不包含

- 不因“可能支持溯源”直接修改 `pyproject.toml`、锁文件或 Docker 镜像。
- 不替换 PostgreSQL 事实源，不引入第二套数据库、消息系统或常驻服务。
- 不记录私有 chain-of-thought、凭据、完整未脱敏请求或响应。
- 不把 Semantica 的语义检索、知识图谱能力等同于执行 provenance，除非 POC 有明确字段映射。

## 5. 契约、POC 文件与数据变更

文档阶段候选文件：

- `tasks/2026-08-28/T1-provenance-semantica-spike.md`：证据、结论和回写内容。
- `/tmp/yuxi-semantica-spike/`：仅存临时 POC，不进入仓库。
- 采用后才允许新增 `backend/package/yuxi/evaluation/provenance.py` 和适配器测试；不采用则不改依赖。

本地最小记录：

```text
ProvenanceRecord:
  record_id, source_type, source_id, source_version
  dataset_version_id, experiment_id, rollout_id, attempt
  parent_id, sequence, component, operation
  redacted_input_summary, redacted_output_summary, checksum
  occurred_at, reason_code, langfuse_trace_id?
```

POC 至少写出两条链：

1. 成功：`agent.start -> fixture.echo -> agent.final`，父子关系、checksum 和幂等重放一致。
2. 失败：禁止工具或答案不匹配，记录失败 reason code，不能吞掉适配器异常；本地记录仍可查询。

适配器接口只允许表达 `record(event) -> local_record_id`、`flush()`、`query(record_id)`、`export()`；外部系统写入失败时保留本地事实并返回可诊断错误。

## 6. 任务清单

- [ ] 读取仓库 README、打包元数据、许可证和发布记录；记录访问时间和来源。
- [ ] 核对 Python 3.12、异步调用、PostgreSQL/Redis 兼容性及依赖树。
- [ ] 将 Semantica 概念逐项映射到 `ProvenanceRecord` 字段，无法映射的字段记录缺口。
- [ ] 在临时目录完成成功/失败 fixture POC；验证脱敏、checksum、幂等、导出和异常回退。
- [ ] 执行依赖安全检查，不把凭据写入输出。
- [ ] 在本文件和总领文件记录采用/不采用结论、证据链接、版本和下一步。

## 7. 验证命令和预期结果

可联网时：

```bash
python -m pip index versions <已核验包名>
python -m pip download --no-deps <已核验包名> -d /tmp/yuxi-semantica-spike
python -m pip inspect
```

只能把实际得到的包名替换 `<已核验包名>`，不能猜包名。POC 阶段：

```bash
python /tmp/yuxi-semantica-spike/run_success.py
python /tmp/yuxi-semantica-spike/run_failure.py
git diff --check -- tasks/2026-08-28/T1-provenance-semantica-spike.md
```

预期：成功链输出完整三段父子链；失败链输出明确 reason code 且本地记录仍存在；依赖信息包含许可证和版本；任何未知能力写为“未证实”，不写成通过。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 固定 Semantica 仓库 URL | 已确认 URL，尚未取得可验证元数据 |
| 2026-08-28 | 尝试访问文档、GitHub API 和 README | 当前环境 DNS/API 不可用 |
| 2026-08-28 | 本地溯源字段和 POC 要求 | 已写入本文件，等待证据 |
| 2026-08-28 | Semantica 外部核验重试 | `agent-reach doctor --json`、Jina Reader、GitHub API 均受当前环境 DNS/API 限制，未取得可验证包元数据 |
| 2026-08-28 | 本地 provenance POC | `TraceEvent` 已覆盖脱敏、checksum、父子事件和按 rollout 查询；成功/失败 Fixture 已由平台 Runner 写入 PostgreSQL |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,280p' tasks/2026-08-28/T1-provenance-semantica-spike.md
ls -la /tmp/yuxi-semantica-spike 2>/dev/null || true
```

如果连续无法访问外部仓库，恢复路径是：在总领文件记录“暂不引入 Semantica”，冻结本地契约，进入 T2；不得因缺少网络而猜测 API 或安装未知包。

## 10. 完成条件

- Semantica 的包身份、版本、许可证、兼容性和能力均有证据，或明确标记不可核验。
- 成功/失败 POC 均覆盖本地事实源、脱敏、checksum、幂等和异常回退。
- 形成唯一结论：采用可选适配器、暂缓，或不采用；总领文件 checkpoint 已更新。
- T2 的进入条件明确，不产生未经授权的代码或依赖变更。

## 11. T1 结论与检查点

结论：**暂不引入 Semantica**。当前网络环境无法访问 `semantica-agi/semantica` 的 README、发布元数据、许可证和 API 文档，因此不能证明其包名、版本、Python 3.12/异步 Worker 兼容性，也不能证明它能表达本项目的父子事件、checksum、事务和幂等要求。将未知能力写成“可用”会使依赖和数据事实源不可审计。

当前实施采用仓库内 `yuxi.evaluation.provenance` + PostgreSQL `platform_eval_trace_events` 作为唯一本地事实源；Langfuse trace ID 仅保留为可空关联字段。后续若能取得可复核包信息，必须另开任务做可选适配器 POC，不得替换本地事实源。

验证证据：

- `agent-reach doctor --json`：已执行，网页/GitHub 后端当前不可用。
- `r.jina.ai` 和 `api.github.com`：DNS/API 解析失败。
- 平台 Fixture 成功/失败闭环：Trace 事件、脱敏摘要、checksum、幂等重跑均在本地测试通过。

进入 T2：允许实现三类旧评测来源的只读字段适配器；禁止新增 Semantica 依赖或修改旧评测入口。

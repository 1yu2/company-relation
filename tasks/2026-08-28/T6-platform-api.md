# T6：统一评测平台 API 适配层

关联总领文件：[task.md](./task.md)。本子任务在 T3-T5 契约稳定后提供管理员可用的资源查询 API；首轮不提供前端创建/运行操作。

## 1. 目标

通过与来源无关、清晰、可分页、可追溯的 HTTP API 查询 Dataset、Version、Experiment、Rollout、Trace、Metric 和 Failure，使同一套前端页面和排查脚本可以查看 Agent/Tool/MCP、RAG、Graph 三类评测，同时保持旧 `/api/evaluation/*` 等入口不变。

## 2. 已确认事项

- 新 API 前缀暂定 `/api/platform-evaluations`，避免与知识库评测 API 冲突。
- D5=B：首轮前端只读；运行入口可以由受控内部流程触发，是否暴露 HTTP 必须遵守 D8 管理员权限。
- D8=A：仅管理员可访问；所有资源属于内部评测上下文。
- 错误至少区分 `validation_error`、`not_found`、`conflict`、`invalid_state`、`forbidden`。
- 列表接口必须使用 cursor 分页并返回 `next_cursor`；响应带 request_id、资源 ID、状态和版本。

## 3. 剩余疑问

1. 项目现有管理员依赖、认证 header 和 request_id 中间件的确切名称。
2. `POST /experiments/{id}/run` 是否保留为内部 API，或只由 Worker/CLI 调用。
3. Trace/Failure 详情的最大页大小和字段脱敏策略。
4. 旧 Agent Eval HTTP 入口是否需要返回统一资源 ID，还是只保留 source_ref。

## 4. 明确不包含

- 不修改现有 `/api/evaluation/*`、企业关系和 Agent Eval API 的路径、状态码和响应结构。
- 不开放普通用户创建、运行、取消评测，不改变聊天权限。
- 不让 API 接受真实模型密钥、工具地址或任意 URL。
- 不在 API 层重新计算指标或从 Redis 临时状态推断终态；查询 Repository 事实源。

## 5. 具体文件、接口与响应变更

候选文件：

- `backend/server/routers/evaluation_platform_router.py`：路由和错误映射。
- `backend/server/routers/__init__.py`：按现有规范注册路由。
- `backend/test/integration/api/test_evaluation_platform_router.py`：权限、分页、状态集成测试。

首轮查询接口：

```http
GET /datasets?limit=50&cursor=...
GET /datasets/{dataset_id}
GET /experiments/{experiment_id}
GET /experiments/{experiment_id}/rollouts?limit=50&cursor=...
GET /rollouts/{rollout_id}/trace
GET /experiments/{experiment_id}/metrics
GET /experiments/{experiment_id}/failures
```

受控写入/触发接口如实现必须明确为管理员内部操作：

```http
POST /datasets
POST /datasets/{dataset_id}/versions
POST /datasets/{dataset_id}/versions/{version_id}/publish
POST /experiments
POST /experiments/{experiment_id}/run
```

统一错误响应：`{"request_id":"...","error":{"code":"...","message":"..."}}`。资源详情要带 `status`、`created_at`、`updated_at`、版本/公式信息和 source_ref；敏感内容只返回脱敏摘要。

## 6. 任务清单

- [ ] 核对现有认证、管理员依赖、router 注册和异常处理方式。
- [ ] 实现只读查询、cursor 校验和稳定排序。
- [ ] 实现资源详情的 source_ref、公式版本、终态和恢复信息。
- [ ] 若开放受控写入，加入发布不可变、非法状态和幂等响应。
- [ ] 编写管理员、普通用户、参数错误、资源不存在、空列表和终态查询测试。
- [ ] 回归旧 API，并在总领文件记录是否开放 run/cancel HTTP 接口。

## 7. 验证命令和预期结果

```bash
docker compose exec api pytest backend/test/integration/api/test_evaluation_platform_router.py -q
docker compose exec api pytest backend/test/integration -k "evaluation or company_relation" -q
docker compose exec api pytest backend/test/unit/server -q
git diff --check -- tasks/2026-08-28/T6-platform-api.md
```

预期：管理员可访问，非管理员得到 `403 forbidden`；无效 cursor/参数得到 `400 validation_error`；不存在资源得到 `404 not_found`；终态非法修改得到 `409 invalid_state`；分页稳定且旧 API 回归通过。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 冻结 API 草案、权限和错误分类 | 已记录于总领文件 |
| 待 T3-T5 | 验证 Repository 和指标响应 | 未开始 |
| 待 T6 实施 | 路由、集成测试和旧 API 回归 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,300p' tasks/2026-08-28/T6-platform-api.md
rg -n "admin|forbidden|request_id|include_router|evaluation" backend/server backend/test
```

若认证或现有路由规范不支持只读管理员 API，先在总领第 9 节记录，不能绕过权限或临时放开普通用户访问。

## 10. 完成条件

- API 能以同一套资源协议查询三类评测来源的所有首轮资源及 Trace/Metric/Failure 详情，响应可分页、可追溯、已脱敏。
- 管理员/非管理员、错误码、终态和幂等行为有集成测试。
- 旧 API/CLI 行为不变，T7 可只依赖公开 API。
- 总领文件记录实际路由、权限验证和 T7 进入条件。

## 11. 实施结果（2026-08-28）

已完成管理员 API 首轮实现，实际路由注册在 `backend/server/routers/__init__.py`：

- 前缀为 `/api/platform-evaluations`，覆盖 datasets、versions、experiments、rollouts、trace、metrics、failures。
- datasets/experiments/rollouts 列表支持 `limit` + base64 cursor，响应为 `{request_id, data: {items, next_cursor}}`；资源查询从 PostgreSQL 事实源读取。
- 所有路由依赖 `get_admin_user`；404 映射 `not_found`，非法参数为 `validation_error`，终态/不可变更新为 `invalid_state`，响应包含 request_id。
- `POST /experiments/{id}/run` 和 `/cancel` 是管理员内部入口；首轮页面不提供操作按钮。旧 `/api/evaluation/*`、企业关系和 Agent Eval 路径未修改。

验证：API 集成测试文件已新增，当前 Compose `LITE_MODE=1` 时按设计跳过；平台评测 unit 和 PostgreSQL Fixture 探针通过。非 LITE 部署应执行该集成文件覆盖管理员/普通用户、创建发布、运行和 cursor 分页。

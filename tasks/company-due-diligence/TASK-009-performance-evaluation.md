# TASK-009：系统性能评测

| 属性 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 类型 | Wave 5 并行任务 |
| 优先级 | P1 |
| 依赖 | `TASK-006`、`TASK-007` |
| 可并行任务 | `TASK-008`、`TASK-011` |

## 目标

建立企业基本信息、列表查询、多跳查询和完整报告的可重复性能评测，输出 P50/P95/P99、吞吐、错误率、分段耗时和连接池指标，并保护外部只读 MySQL 不被默认高并发压测。

## 交付物

- 性能场景、负载参数和安全模式模型。
- 可本地和容器运行的性能 runner。
- 后台性能任务、进度和取消 API。
- MySQL、Neo4j、Milvus、LLM 分段耗时和连接池指标。
- JSON 和 Markdown 性能报告。
- 性能阈值、回归比较和安全操作说明。
- 单元测试和低负载集成测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/performance/models.py`
- `backend/package/yuxi/company_due_diligence/performance/scenarios.py`
- `backend/package/yuxi/company_due_diligence/performance/runner.py`
- `backend/package/yuxi/company_due_diligence/performance/report.py`
- `backend/server/routers/company_due_diligence_evaluation_router.py`
- `backend/scripts/company_due_diligence/run_performance_evaluation.py`
- `backend/test/unit/company_due_diligence/performance/`
- `backend/test/integration/company_due_diligence/test_performance_evaluation_api.py`

首版使用进程内异步负载 runner 即可，不新增 Locust、JMeter 或其他常驻 Docker 服务。若后续引入外部压测工具，需要单独确认。

## 性能场景

- `enterprise_profile`：企业基本信息。
- `shareholder_list`：当前股东和出资。
- `financing_and_risk`：融资、处罚和许可列表。
- `graph_path_3_hops`：最多三跳路径。
- `report_aggregate_without_llm`：不含 LLM 的报告数据聚合。
- `report_with_llm`：包含在线 LLM 的完整报告。

## 安全模式

### 安全回归模式

- 使用冻结样本或测试环境。
- 默认并发 1～3。
- 可以进入日常回归和 CI。

### 受控真实库模式

- 仅管理员可启动。
- 必须显式确认真实只读库、并发、持续时间和查询集合。
- 初始最大并发 10，默认持续时间不超过 5 分钟。
- 获得数据源负责人允许后执行。
- 不进入默认 CI，不自动定时运行。

## API 契约

建议接口：

```text
POST /api/company-due-diligence/evaluations/performance-runs
GET  /api/company-due-diligence/evaluations/performance-runs
GET  /api/company-due-diligence/evaluations/performance-runs/{run_id}
POST /api/company-due-diligence/evaluations/performance-runs/{run_id}/cancel
GET  /api/company-due-diligence/evaluations/performance-runs/{run_id}/download?format=json|markdown
```

## 记录指标

- 请求总数、成功数、失败数和超时数。
- P50、P95、P99 和最大耗时。
- 每秒吞吐量。
- 身份解析、连接池等待、MySQL、Neo4j、Milvus、LLM 和组装耗时。
- 连接池大小、签出数、等待时间、溢出和超时。
- 冷缓存与热缓存结果。
- CPU、内存和容器基础资源快照。
- 代码版本、数据版本、模型版本和运行参数。

## 初始目标

- 企业基本信息 P95 < 500 ms。
- 股东、融资、风险列表 P95 < 1 s。
- 最多三跳路径 P95 < 3 s。
- 不含 LLM 的报告聚合 P95 < 5 s。
- 包含 LLM 的报告 P95 < 30 s，LLM 耗时单列。
- 10 并发核心查询错误率 < 1%。
- 连接池无泄漏、无非预期溢出。

## 验收标准

- [x] 性能报告包含 P50/P95/P99、吞吐和错误率。
- [x] 每个请求可以看到主要阶段耗时。
- [x] 连接池等待、签出和超时可观测。
- [x] 默认运行不会对真实 MySQL 发起高并发。
- [x] 真实库模式有管理员确认、硬上限和取消能力。
- [x] LLM 与非 LLM 场景分开统计。
- [x] 同一场景可对比不同代码、数据或策略版本。
- [x] 运行结束或取消后连接池和任务资源释放。

## 完成记录

- 已完成六类场景、进程内异步负载 runner、安全模式与受控真实库模式、Tasker 运行/取消、JSON/Markdown 导出和历史运行对比契约。
- 安全模式限制并发不超过 3、时长不超过 60 秒、请求数不超过 100；真实库模式要求管理员权限、确认口令和更严格硬上限。
- Docker 低负载真实 MySQL 集成测试和发布门禁性能基线通过，连接池无泄漏；当前报告不调用在线 LLM，因此 `report_with_llm` 明确记录为 `skipped/unavailable`，不复用无 LLM 结果冒充。

## 测试范围

### 单元测试

- 分位数、吞吐、错误率和阶段耗时聚合。
- 并发、持续时间和安全模式参数上限。
- 取消、超时和连接池快照。

### 集成测试

- 使用并发 1～2 的短时安全运行调用真实 API。
- 验证运行状态、结果、取消和导出。
- 验证不会超过配置的连接池和并发上限。

## 非目标

- 不默认对未知生产数据库做压力测试。
- 不以浏览器端计时替代后端阶段指标。
- 不新增常驻压测 Docker 服务。
- 不在本任务中优化所有性能问题；先建立可复现基线。

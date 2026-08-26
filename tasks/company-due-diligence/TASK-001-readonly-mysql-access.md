# TASK-001：只读 MySQL 接入

| 属性 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 类型 | 串行起点 |
| 优先级 | P0 |
| 依赖 | 无 |
| 可并行任务 | 无；该任务完成后才能冻结数据契约 |

## 目标

建立广东企业 MySQL 的健康只读接入层，为后续结构化查询、图谱投影、文本索引和报告生成提供统一连接池与 Repository 基础。

## 输入

- 当前 `.env` 中的外部 MySQL 连接配置。
- 数据审计中确认的必要表和字段。
- 当前 Yuxi FastAPI lifespan、日志和配置加载方式。

## 交付物

- 独立的企业尽调 MySQL 配置模型。
- 进程级 SQLAlchemy 连接池及启动/关闭生命周期。
- 只读会话创建方法和基础 Repository 边界。
- 管理员健康检查接口。
- 单元测试和真实只读库集成测试。
- 敏感信息脱敏规则和运行说明。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/config.py`
- `backend/package/yuxi/company_due_diligence/mysql.py`
- `backend/package/yuxi/company_due_diligence/repositories/base.py`
- `backend/server/utils/lifespan.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/server/routers/__init__.py`
- `backend/test/unit/company_due_diligence/`
- `backend/test/integration/company_due_diligence/`

最终路径可以按现有包结构微调，但领域逻辑必须位于 `backend/package/yuxi`，Router 只处理请求、权限和响应装配。

## 实现步骤

1. 新增标准环境变量读取，不使用 `eval` 解析连接配置。
2. 创建小连接池：默认 `pool_size=2`、`max_overflow=0`、`pool_pre_ping=true`、`pool_recycle=300`、`pool_timeout=5`。
3. 设置连接超时和单条语句执行超时。
4. 每个会话设置为只读；Repository 不暴露任意 SQL 执行入口。
5. 在 lifespan 中初始化并在退出时释放连接池。
6. 健康检查执行轻量 `SELECT 1` 和必要表存在性检查。
7. 输出连接池签出数、等待和超时等可观测指标，但不输出连接详情。
8. 对日志和异常做 DSN、用户名、密码和查询参数脱敏。

## 健康检查契约

建议接口：

```text
GET /api/company-due-diligence/health
```

响应仅包含：

```json
{
  "configured": true,
  "reachable": true,
  "readonly": true,
  "required_tables_ready": true,
  "latency_ms": 0,
  "pool": {
    "size": 2,
    "checked_out": 0,
    "overflow": 0
  }
}
```

## 验收标准

- [x] 应用启动和关闭不会泄漏连接。
- [x] 多次健康检查复用连接池，不为每个请求创建新 Engine。
- [x] 连接失效后 `pool_pre_ping` 能显式失败或恢复。
- [x] 写 SQL 在代码边界和数据库会话层均被拒绝。
- [x] 超时查询停止后连接可再次使用。
- [x] 日志、响应和测试失败信息不包含完整 DSN 或密码。
- [x] 不新增 MySQL Docker 服务，不修改原 MySQL 表。

## 完成记录

- 异步 Engine 只创建一次，启用 `pool_pre_ping`、固定池大小、零溢出、回收/等待/连接超时，并在应用 lifespan 关闭时显式释放。
- Repository 与 Engine 事件形成双层只读守卫，每个会话设置只读事务和执行上限；真实 MySQL 健康检查确认只读状态和必要表。
- 客户端查询边界使用强制超时，超时连接立即失效并退出池；真实 `SELECT SLEEP` 超时后下一条 `SELECT 1` 成功且签出数回到 0。
- 宿主机与容器敏感信息审计通过，未新增 MySQL Compose 服务，未执行任何 DML/DDL。

## 测试范围

### 单元测试

- 配置缺失、非法格式和脱敏输出。
- 连接池参数和单例生命周期。
- 只读 SQL 防线和禁止语句。
- 连接异常、查询超时和关闭行为。

### 集成测试

- 在 Docker `api-dev` 中连接真实只读库执行 `SELECT 1`。
- 查询必要表的有限元数据。
- 尝试写入并确认被拒绝，不留下任何数据变更。
- 连续查询后确认连接池签出数回到 0。

## 非目标

- 不实现具体企业业务查询。
- 不建立企业身份映射。
- 不同步 Neo4j 或 Milvus。
- 不将外部 MySQL 注册为 Yuxi 的主业务 PostgreSQL。

## 安全说明

历史诊断过程中连接表达式曾在终端回显，实施前应轮换数据库密码。测试报告只记录脱敏后的连接结果，不复述旧凭据。

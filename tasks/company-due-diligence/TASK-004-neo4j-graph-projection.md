# TASK-004：Neo4j 关系投影

| 属性 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 类型 | Wave 3 并行任务 |
| 优先级 | P1 |
| 依赖 | `TASK-002` |
| 可并行任务 | `TASK-003`、`TASK-005` |

## 目标

把 MySQL 中高置信度企业、股东、融资机构和知识产权关系投影到现有 Neo4j，提供可追溯、可版本化、可全量重建的最多三跳关系查询。

## 交付物

- 图谱 Schema、约束和稳定业务键。
- MySQL 只读批量抽取与 Neo4j 幂等投影任务。
- 投影版本、checksum、统计和校验报告。
- 受控路径查询服务和 Cypher 模板。
- 管理员投影启动、状态和统计接口。
- 单元测试、Neo4j 集成测试和重建测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/graph/schema.py`
- `backend/package/yuxi/company_due_diligence/graph/projection.py`
- `backend/package/yuxi/company_due_diligence/graph/repository.py`
- `backend/package/yuxi/company_due_diligence/graph/query_service.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/graph/`
- `backend/test/integration/company_due_diligence/test_graph_projection.py`

可以复用 `backend/package/yuxi/company_relation/neo4j_repository.py` 的连接和批量写入经验，但不得把 30 家 AkShare 快照和广东企业投影混成同一数据版本。

## 首版节点与关系

节点：`Company`、`Shareholder`、`FinancingEvent`、`Investor`、`Patent`、`Trademark`、`Copyright`、`Punishment`、`License`、`Regulator`。

关系：`HOLDS`、`RECEIVED_FINANCING`、`INVESTED_IN`、`OWNS_PATENT`、`OWNS_TRADEMARK`、`OWNS_COPYRIGHT`、`PUNISHED_BY`、`LICENSED_BY`。

## 实现步骤

1. 定义节点和关系稳定键、唯一约束和必要索引。
2. 从 MySQL 分批读取，不执行无界联表和正文扫描。
3. 仅将稳定 ID 关系标记为高置信度。
4. 名称匹配关系单独标记中低置信度，默认不进入严格路径。
5. 为每个节点和边保存 `source_id`、来源表、来源记录键、有效日期和投影版本。
6. 生成投影快照 checksum 和抽取统计后再写入 Neo4j。
7. 使用版本标签或等价机制完成幂等替换，不留下半成品当前版本。
8. 提供最多三跳、关系类型白名单和结果上限的参数化路径查询。
9. 路径返回后校验每条边来源和置信度。

## API 契约

建议管理员接口：

```text
POST /api/company-due-diligence/graph/projections
GET  /api/company-due-diligence/graph/projections/{task_id}
GET  /api/company-due-diligence/graph/info
```

路径查询先作为内部服务，由 `TASK-006` 统一开放问答 API。

## 验收标准

- [x] Neo4j 数据可以从 MySQL 全量重建。
- [x] 重复执行同一版本不会产生重复节点或边。
- [x] 每条边都有来源和投影版本。
- [x] 默认严格查询只使用高置信度关系。
- [x] 最多三跳、关系类型和返回路径数均有限制。
- [x] 删除投影不会影响 MySQL 事实。
- [x] 投影统计包含节点数、边数、孤儿数、重复数和置信度分布。

## 测试范围

### 单元测试

- 稳定键、置信度、节点和关系映射。
- 路径模板参数校验和三跳限制。
- 投影版本和 checksum。

### 集成测试

- 小规模固定企业集合的导入、重复导入和删除重建。
- 共同股东、融资机构网络和最短路径。
- 低置信度关系默认排除。
- Neo4j 不可用时显式失败，不回退为无路径。

## 非目标

- 不让 LLM 生成任意 Cypher。
- 不把 `RELATED_TO` 保存为无来源原始事实。
- 不把产品同名或自然人同名直接当作高置信度关系。
- 不新增 Neo4j Docker 服务或镜像。

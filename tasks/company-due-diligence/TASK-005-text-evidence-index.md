# TASK-005：文本证据索引

| 属性 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 类型 | Wave 3 并行任务 |
| 优先级 | P1 |
| 依赖 | `TASK-002` |
| 可并行任务 | `TASK-003`、`TASK-004` |

## 目标

把新闻、处罚文书、许可内容、专利摘要和产品描述从只读 MySQL 投影到现有 Milvus/MinIO，提供按企业、类型和日期过滤的可追溯文本证据检索。

## 交付物

- 文本文档模型和稳定 `document_id`。
- 分批抽取、清洗、分块、嵌入和索引任务。
- Milvus collection 或现有知识库命名空间规划。
- 文档元数据、checksum 和索引版本。
- 证据检索服务和来源回查。
- 管理员索引任务、状态和统计接口。
- 单元测试和 Milvus 集成测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/evidence/models.py`
- `backend/package/yuxi/company_due_diligence/evidence/extractor.py`
- `backend/package/yuxi/company_due_diligence/evidence/indexer.py`
- `backend/package/yuxi/company_due_diligence/evidence/query_service.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/evidence/`
- `backend/test/integration/company_due_diligence/test_evidence_index.py`

## 首版文本范围

1. 行政处罚事实、依据、决定和机关。
2. 环保处罚内容。
3. 行政许可事项和许可内容。
4. 专利摘要。
5. 产品描述。
6. 已能稳定映射企业的新闻标题、摘要和允许读取的正文。

新闻映射不足的记录不得强行关联企业；首版只索引已解析为唯一 `eid` 的文本。

## 实现步骤

1. 为每类文档定义稳定业务键、允许读取字段和数据日期。
2. 只按企业、主键游标和批次读取，不进行无界正文扫描。
3. 清理空文本、重复文本和明显模板噪声，保留原始 checksum。
4. 使用语义完整的分块策略，避免切断处罚决定、许可事项和专利摘要。
5. 元数据必须包含 `eid`、来源表、来源记录键、文档类型、业务日期、索引版本和 checksum。
6. 复用现有 embedding 配置、Milvus 和 MinIO，不新增基础设施。
7. 查询时先按 `eid`、文档类型和日期过滤，再向量召回和可选重排。
8. 返回证据片段后回查来源元数据，不能只返回向量文本。
9. 记录抽取数、跳过数、重复数、失败数和覆盖企业数。

## 证据输出

```json
{
  "evidence_id": "证据标识",
  "document_id": "文档标识",
  "eid": "企业标识",
  "document_type": "administrative_punishment",
  "text": "命中的证据片段",
  "score": 0.0,
  "published_at": "业务日期",
  "source_id": "来源标识",
  "projection_version": "索引版本"
}
```

## 验收标准

- [x] 同一源记录重复索引不会产生重复文档。
- [x] 每个证据片段可以追溯到 MySQL 来源记录。
- [x] 查询必须先按企业和类型过滤，不能跨企业混入证据。
- [x] 新闻等映射失败记录不会自动归属企业。
- [x] 索引版本可以删除并从 MySQL 重建。
- [x] Milvus 不可用时返回来源不可用，不伪装为无文本证据。
- [x] 不新增 Milvus、MinIO 或 embedding 基础设施镜像。

## 完成记录

- 六类来源按主键游标和可选 `eid` 白名单只读抽取；新闻仅通过企业标准全称精确映射，新闻解读保留独立来源记录。
- SQLite 快照完成内容去重、稳定文档/分块 ID、checksum 和统计后，写入版本化 Milvus collection；MinIO 保存 `documents.jsonl`、`manifest.json` 和原子 `current.json` 指针。
- embedding 模型或维度变化会生成不同版本；发布失败会删除半成品 collection，不切换当前版本；同版本重复执行不重复写向量。
- 查询构造 `eid + 文档类型 + 日期` 标量过滤后再向量召回，并校验企业、来源键和投影版本；基础设施或模型不可用时返回 `unavailable`。
- 管理接口：`POST /api/company-due-diligence/evidence/indexes`、`GET /evidence/indexes/{task_id}`、`GET /evidence/info`、`POST /evidence/query`、`DELETE /evidence/versions/{projection_version}`。
- Docker 验证：六类真实 MySQL 文本抽取、真实 Milvus/MinIO 幂等索引、企业/类型/日期隔离、来源回查、删除重建和连接释放均通过；`TASK-001`～`TASK-005` 回归 `79 passed`。

## 测试范围

### 单元测试

- 稳定文档键、checksum、分块和元数据。
- 空文本、重复文本、日期异常和企业映射失败。
- 查询过滤条件和结果装配。

### 集成测试

- 固定小样本抽取、索引、重复索引和删除重建。
- 按企业、类型、日期检索并校验来源。
- Milvus 连接失败和 embedding 超时。
- 批量任务完成后 MySQL 和 Milvus 连接均正常释放。

## 非目标

- 不把向量结果当作结构化金额、日期或状态事实。
- 不索引无法稳定映射企业的全部新闻。
- 不自动抓取新的外部网页。
- 不修改原 MySQL 文本。

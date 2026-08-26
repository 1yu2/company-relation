# 企业关系图谱评测

> 当前定位：本页面记录 30 家公开上市公司的 AkShare 回归基线，属于独立的企业关系图谱评测，不作为广东企业尽调智能体的主数据或评测结果。企业尽调使用只读 MySQL 数据，并通过单元、集成和关键 E2E 测试保障主链路。

企业关系图谱链路使用同一份公开数据冻结快照和 Golden 题库，对 TF-IDF 向量检索基线、GraphRAG 与 Cypher 直查进行可重复对照。查询结果统一返回答案、实体、事实、关系路径、证据来源和延迟，评测同时落盘完整 JSON、逐题 JSONL 与 Markdown 汇总报告。

## Docker 资源复用

如果 PostgreSQL、Redis、MinIO、Neo4j 和 API 已经在本机运行，不需要为本链路再次拉取或启动同类镜像。先用 `docker ps` 确认现有容器，再直接进入现有 `api-dev` 执行脚本；企业关系链路只额外使用 Neo4j，冻结快照和题库均为本地文件。

开发 Compose 已将 `backend/data` 挂载到 `/app/data`，生产 API 镜像也会复制该目录。不要为单独运行评测创建第二套 PostgreSQL、Redis 或 MinIO。

## 输入输出链路

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| 公开数据采集 | AkShare 接口、报告期和公告日期范围 | 清洗、实体归一、关系派生、来源绑定 | `backend/data/company_relation/snapshot.json` |
| 快照校验 | 标准化快照 | 校验 Schema、重复 ID、孤儿边和来源覆盖率 | 校验摘要与快照 SHA-256 |
| 图谱导入 | 校验通过的快照 | 按 `dataset_id` 替换导入 Neo4j | 节点、边和数据库统计 |
| 题库冻结 | 快照中的事实和关系路径 | 分层生成属性、单跳、多跳和拒答题 | `backend/data/company_relation/golden_questions.jsonl` |
| 三策略查询 | 同一自然语言问题 | TF-IDF 检索、图遍历或 Cypher 查询 | 答案、事实 ID、路径、来源和延迟 |
| 严格评分 | 查询输出与 Golden 答案 | 比较拒答、实体、事实和完整路径 | 准确率、Recall@10、MRR、证据对齐和错误归因 |
| 报告落盘 | 逐题评分结果 | 汇总分类、性能、并发和回归门禁 | `backend/data/company_relation/results/` |

## 数据来源

当前冻结数据集为 `company_relation_public_20251231`，数据截至 2025 年 12 月 31 日。

- 公司概况、高管与公告元数据：AkShare 的 `stock_profile_cninfo`、`stock_zh_a_disclosure_report_cninfo`，公开底层来源为巨潮资讯网，共 330 条来源记录。
- 十大股东与流通股东：AkShare 的 `stock_gdfx_top_10_em`、`stock_gdfx_free_top_10_em`，公开底层来源为东方财富，共 60 条来源记录。
- 每个节点和关系都关联至少一个 `source_id`；快照当前来源覆盖率为 100%。

冻结快照包含 541 个节点和 1985 条边：137 个公司、90 个人员、300 条公告、14 个行业；关系包括 316 条 `HOLDS`、30 条 `EXECUTIVE_OF`、30 条 `BELONGS_TO`、300 条 `RELEASED` 和 1309 条派生 `RELATED_TO`。

## 执行方式

以下命令默认复用已经运行的 `api-dev` 和 `graph`，不会启动新的 PostgreSQL、Redis 或 MinIO。

```bash
# 可选：重新采集公开数据；该步骤需要网络并临时安装 AkShare
docker exec api-dev uv run --with akshare python scripts/company_relation/collect_public_snapshot.py

# 校验并替换导入 Neo4j
docker exec api-dev uv run --no-sync --no-dev python scripts/company_relation/import_graph.py

# 从冻结快照重新生成 120 道题
docker exec api-dev uv run --no-sync --no-dev python scripts/company_relation/generate_golden_questions.py

# 已导入相同 dataset_id 时直接执行三策略评测
docker exec api-dev uv run --no-sync --no-dev python scripts/company_relation/run_evaluation.py --skip-import
```

管理员还可以通过以下接口读取数据摘要、导入快照、执行查询和启动评测：

- `GET /api/company-relations/info`
- `POST /api/company-relations/import`
- `POST /api/company-relations/query`
- `POST /api/company-relations/evaluations/run`

## 当前整体结果

基准运行 `company-relation-20260807T093623Z-1c93067f` 使用 120 道冻结题目：属性 30、单跳 30、多跳 40、拒答 20。快照 checksum 为 `aaf0648e85e7964758fdfe273dcac8b63e55cd45dea897b2fad1b29be809bb76`，题库 checksum 为 `908db8dce33429d1c6825967d5ce97206891d85c4d3882b2bf89950930150d4a`。

| 策略 | 严格准确率 | 多跳准确率 | 拒答准确率 | 幻觉率 | Recall@10 | MRR | 证据对齐率 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TF-IDF 向量基线 | 55.83% | 0.00% | 100.00% | 0.00% | 0.512 | 0.400 | 100.00% | 0.808 ms |
| GraphRAG | 100.00% | 100.00% | 100.00% | 0.00% | 0.997 | 1.000 | 100.00% | 0.117 ms |
| Cypher 直查 | 100.00% | 100.00% | 100.00% | 0.00% | 0.997 | 1.000 | 100.00% | 8.611 ms |

10 并发探针中三种策略均为 0 错误；P95 分别为 11.031 ms、4.058 ms 和 21.708 ms。GraphRAG 相对向量基线净改善 53 道题，没有新增失败，回归门禁全部通过。

完整结果位于：

- `backend/data/company_relation/results/company-relation-20260807T093623Z-1c93067f.json`
- `backend/data/company_relation/results/company-relation-20260807T093623Z-1c93067f-items.jsonl`
- `backend/data/company_relation/results/company-relation-20260807T093623Z-1c93067f.md`

## 验证状态

- 企业关系单元测试 7/7 通过，相关 Python 代码 Ruff 检查通过。
- 冻结快照已真实导入并复用本机 `graph` Neo4j 完成 120 题三策略评测，图中统计为 541 个节点、1985 条边。
- 新增 HTTP 集成测试已放入 `backend/test/integration/api/test_company_relation_router.py`，但当前没有可复用的 Yuxi API 容器；为避免重新构建 5.78GB API 镜像，本次未执行真实 HTTP 集成测试。
- 文档依赖目录当前不存在；为避免额外下载，本次未安装依赖或执行 VitePress 构建。

## 结果口径与限制

- 严格准确率要求拒答状态、答案实体、事实 ID 和关系路径全部与 Golden 答案一致。
- Golden 答案由来源快照派生并冻结，当前人工复核覆盖率为 0%，因此 100% 表示对派生金标准的一致性，不代表人工事实核验准确率。
- 当前 `graph_rag` 使用确定性内存图遍历和证据模板，不包含在线 LLM 推理；向量基线使用 TF-IDF 稀疏向量，不是生产 embedding 向量库。
- 公告 PDF 实体/关系抽取 P/R/F1、公告入库到图谱可见的增量延迟，以及在线 LLM 生成质量和延迟仍为未测项。

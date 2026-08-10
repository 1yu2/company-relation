# TASK-008：结果质量评测

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 5 并行任务 |
| 优先级 | P0 |
| 依赖 | `TASK-006`、`TASK-007` |
| 可并行任务 | `TASK-009`、`TASK-011` |

## 目标

建立人工复核 Golden、冻结数据集和可重复质量评测链路，对简单问答、多跳路径和报告章节进行字段、关系、路径、证据、拒答、幻觉、完整性和时效评测。

## 交付物

- Golden 数据集格式和人工复核流程。
- 脱敏冻结快照、数据版本和 checksum。
- 质量评测运行服务和后台任务。
- 指标计算、逐题差异和首错归因。
- 数据集、运行、详情、取消和导出 API。
- JSON、JSONL、Markdown 评测报告。
- 单元测试、集成测试和回归门禁脚本。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/evaluation/dataset.py`
- `backend/package/yuxi/company_due_diligence/evaluation/metrics.py`
- `backend/package/yuxi/company_due_diligence/evaluation/runner.py`
- `backend/package/yuxi/company_due_diligence/evaluation/report.py`
- `backend/package/yuxi/storage/postgres/` 中必要的数据集和运行模型与迁移
- `backend/server/routers/company_due_diligence_evaluation_router.py`
- `backend/server/routers/__init__.py`
- `backend/test/unit/company_due_diligence/evaluation/`
- `backend/test/integration/company_due_diligence/test_quality_evaluation_api.py`
- `backend/scripts/company_due_diligence/run_quality_evaluation.py`

## Golden 数据集

至少包含：

- 字段问答：基本信息、股东、出资、产品、融资、风险和知识产权。
- 多跳路径：共同股东、投资机构网络、最短路径、无路径和低置信度拒答。
- 报告章节：必要事实、必要来源、缺失声明、时效声明和人工摘要。

每条样本必须有 `review_status`。只有 `human_verified` 样本可以进入严格发布门禁；程序派生但未人工复核的样本必须单独统计。

## 核心指标

- 字段严格准确率。
- 关系严格准确率。
- 多跳路径准确率。
- 证据覆盖率。
- 拒答准确率。
- 幻觉率。
- 报告章节完整率。
- 时效声明准确率。

辅助指标可以包含答案文本相似度，但不能替代严格事实和路径指标。

## API 契约

建议接口：

```text
GET  /api/company-due-diligence/evaluations/datasets
GET  /api/company-due-diligence/evaluations/datasets/{dataset_id}
POST /api/company-due-diligence/evaluations/runs
GET  /api/company-due-diligence/evaluations/runs
GET  /api/company-due-diligence/evaluations/runs/{run_id}
POST /api/company-due-diligence/evaluations/runs/{run_id}/cancel
GET  /api/company-due-diligence/evaluations/runs/{run_id}/download?format=json|jsonl|markdown
```

## 实现步骤

1. 定义脱敏冻结数据格式、Golden 格式和人工复核字段。
2. 选择覆盖有数据、无数据、歧义、过期和低置信度的企业与问题。
3. 冻结数据版本、业务时点、checksum 和生成环境。
4. 使用真实 `TASK-006`、`TASK-007` 服务运行每条样本。
5. 对事实、关系和路径做集合级严格比较。
6. 检查每个输出事实是否存在可用 `source_id`。
7. 计算拒答、幻觉、章节完整性和时效声明。
8. 保存逐题结果、错误类别和首错阶段。
9. 聚合总体、问题类型、数据域、策略和人工复核状态指标。
10. 输出可复现报告并建立回归阈值。

## 首错归因

至少区分：

- 企业身份解析错误。
- MySQL 字段映射错误。
- Neo4j 投影或路径错误。
- 文本召回错误。
- 来源缺失。
- 时效判断错误。
- LLM 组织错误。
- 正确拒答失败。
- 报告章节编排错误。

## 验收标准

- [ ] 评测数据集有版本、checksum、业务时点和人工复核率。
- [ ] 同一代码、配置和数据版本运行结果可复现。
- [ ] 每个失败样本可以查看预测、Golden 和差异。
- [ ] 未人工复核样本不会混入严格门禁指标。
- [ ] 幻觉检查覆盖所有企业事实，不只检查最终答案文本。
- [ ] 支持取消运行和断点查看已完成样本。
- [ ] 支持 JSON、JSONL 和 Markdown 导出。
- [ ] 30 家 AkShare 基线与广东企业新 Golden 分开报告。

## 测试范围

### 单元测试

- 字段、关系、路径、来源、拒答和章节指标。
- 空集合、重复事实、顺序差异、金额单位和日期差异。
- 人工复核率和门禁过滤。

### 集成测试

- 数据集列表、运行、进度、取消、详情和导出 API。
- 固定小 Golden 的可重复结果。
- 运行元数据与文件产物一致。

## 非目标

- 不用程序生成的答案直接作为严格 Golden。
- 不用单一文本相似度宣称系统准确。
- 不在评测文件中保存数据库凭据或完整原始个人明细。
- 不实现前端页面，该部分由 `TASK-011` 完成。

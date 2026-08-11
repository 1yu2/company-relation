# TASK-008：全链路质量评测

| 属性 | 内容 |
| --- | --- |
| 状态 | 自动评测升级已完成；正式门禁受人工 Golden 阻塞 |
| 类型 | Wave 5 基础任务 / Wave 7 升级任务 |
| 优先级 | P0 |
| 依赖 | `TASK-006`、`TASK-007` |
| 可并行任务 | `TASK-009`、`TASK-011` |

## 目标

建立人工复核 Golden、冻结数据集和可重复质量评测链路，从用户原始输入开始，对企业上下文、意图理解、工具计划、事实、关系、路径、证据、拒答、报告范围、动态展示和输入输出一致性进行分层评测。

## 交付物

- Golden 数据集格式和人工复核流程。
- 脱敏冻结快照、数据版本和 checksum。
- 质量评测运行服务和后台任务。
- 指标计算、逐题差异和首错归因。
- 企业上下文、意图、工具计划和 `presentation` Golden。
- 输入输出一致性和无关内容检查。
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
- `backend/scripts/company_due_diligence/freeze_quality_dataset.py`

## Golden 数据集

至少包含：

- 企业上下文：唯一企业、名称歧义、会话指代和问题中的第二家企业。
- 业务调查：基本信息、股东、出资、产品、融资、风险和知识产权。
- 多跳路径：共同股东、投资机构网络、最短路径、无路径和低置信度拒答。
- 复合意图：多个主题、指定时间范围、详细程度、同步/异步交付。
- 报告输出：专题范围、完整报告章节、必要来源、缺失声明、时效声明和人工摘要。
- 动态展示：摘要、列表、关系结果、专题报告和完整报告的预期 `presentation`。

每条升级样本至少标注：

```text
input.company_context
input.user_message
expected.entities
expected.intent
expected.required_tools
expected.forbidden_tools
expected.facts / relations / paths / sources
expected.presentation
expected.required_topics
expected.forbidden_topics
```

每条样本必须有 `review_status`。只有 `human_verified` 样本可以进入严格发布门禁；程序派生但未人工复核的样本必须单独统计。

## 核心指标

- 企业主体解析正确率。
- 歧义召回率和错误自动选中率。
- 主题、时间、深度、展示和交付识别准确率。
- 必要工具召回率和无关工具调用率。
- 字段严格准确率。
- 关系严格准确率。
- 多跳路径准确率。
- 证据覆盖率。
- 拒答准确率。
- 幻觉率。
- 报告章节完整率。
- 时效声明准确率。
- Claim Ledger 一致率和来源引用准确率。
- 输入主题覆盖率和无关内容率。
- `presentation` 匹配率。
- 专题报告范围一致率。

辅助指标可以包含答案文本相似度和人工表达评分，但不能替代企业、意图、事实、路径、来源和输入输出一致性的严格指标。

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
2. 选择覆盖有数据、无数据、歧义、会话指代、过期、复合意图和低置信度的企业与问题。
3. 冻结数据版本、业务时点、checksum 和生成环境。
4. 使用真实 `TASK-006`、`TASK-007` 服务运行每条样本。
5. 对实体、意图、工具计划、事实、关系、路径和 `presentation` 做严格比较。
6. 检查每个输出事实是否存在可用 `source_id`。
7. 计算拒答、幻觉、章节完整性、时效声明、主题覆盖、无关内容和专题范围一致性。
8. 保存逐题结果、错误类别和首错阶段。
9. 聚合总体、问题类型、数据域、策略和人工复核状态指标。
10. 输出可复现报告并建立回归阈值。

## 首错归因

至少区分：

- 企业身份解析错误。
- 企业上下文复用错误。
- 意图主题或时间范围错误。
- 输出形式或交付方式错误。
- 必要工具漏调或无关工具误调。
- MySQL 字段映射错误。
- Neo4j 投影或路径错误。
- 文本召回错误。
- 来源缺失。
- 时效判断错误。
- LLM 组织错误。
- 正确拒答失败。
- 报告章节编排错误。
- 输入输出不一致。

## 验收标准

- [x] 评测数据集有版本、checksum、业务时点和人工复核率。
- [x] 同一代码、配置和数据版本运行结果可复现。
- [x] 每个失败样本可以查看预测、Golden 和差异。
- [x] 未人工复核样本不会混入严格门禁指标。
- [x] 幻觉检查覆盖所有企业事实，不只检查最终答案文本。
- [x] 支持取消运行和断点查看已完成样本。
- [x] 支持 JSON、JSONL 和 Markdown 导出。
- [x] 30 家 AkShare 基线与广东企业新 Golden 分开报告。
- [x] Golden 可以标注企业上下文、统一意图、必要/禁止工具和 `presentation`。
- [x] 评测器可以计算意图理解、工具计划和输入输出一致性指标。
- [x] 专题报告遗漏主题或加入无关章节时严格判失败。
- [ ] LLM 接入后可以区分意图错误、Claim 错误和表达错误。

## 当前验收记录

- 已完成广东企业与 AkShare 来源类别隔离、样本复核状态过滤、严格事实/关系/路径/证据/拒答/章节指标、首错归因、Tasker 运行与三种格式导出。
- `human_verified` 样本必须填写 `reviewed_by`、`reviewed_at` 和 `reviewer_note`；未复核样本禁止携带人工复核人和时间，防止程序派生数据误入严格门禁。
- Golden 冻结工具会校验复核留痕、原子写入 `declared_checksum`，并只输出版本、checksum、样本数和人工复核率等非敏感摘要。
- 发布门禁固定运行开发烟测，并独立选择最新的已冻结、含 `human_verified` 样本的广东企业数据集；可通过 `COMPANY_DD_HUMAN_QUALITY_DATASET` 显式指定，未冻结数据不得进入正式门禁。
- Docker 自动烟测与真实 MySQL、报告、PostgreSQL Tasker、MinIO 集成测试通过；同一冻结数据集重复运行结果一致。
- 阻塞原因：当前仓库只有 `source_derived_unreviewed` 开发烟测样本，尚无 `human_verified` 广东企业 Golden，因此严格发布门禁不具备资格。
- 已新增 `investigation` Golden 类型，支持企业解析、主题、时间、深度、展示、交付、必要/禁止工具、Claim Ledger、主题覆盖和无关主题规避严格指标。
- 发布门禁已单独展示统一智能体入口、意图、工具、`presentation`、Claim 和输入输出一致性结果。
- 在线 LLM 尚未接入，因此表达质量与“意图错误/Claim 错误/表达错误”三分评测保留为后续项。

## 测试范围

### 单元测试

- 企业、意图、工具计划、字段、关系、路径、来源、拒答、章节和 `presentation` 指标。
- 空集合、重复事实、顺序差异、金额单位和日期差异。
- 主题遗漏、无关主题、错误报告形式和错误同步/异步交付。
- 人工复核率和门禁过滤。

### 集成测试

- 数据集列表、运行、进度、取消、详情和导出 API。
- 固定小 Golden 的可重复结果。
- 运行元数据与文件产物一致。

## 非目标

- 不用程序生成的答案直接作为严格 Golden。
- 不用单一文本相似度宣称系统准确。
- 不只评最终答案而忽略企业解析、意图和工具计划首错。
- 不在评测文件中保存数据库凭据或完整原始个人明细。
- 不实现前端页面，该部分由 `TASK-011` 完成。

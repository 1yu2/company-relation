# TASK-011：前端评测工作台

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 5 条件并行任务 |
| 优先级 | P1 |
| 依赖 | `TASK-008`、`TASK-009` API 契约 |
| 可并行任务 | `TASK-008`、`TASK-009` 可先冻结契约并使用 mock |

## 目标

新增企业尽调评测前端，让管理员选择冻结数据集、运行质量或性能评测、查看进度、指标、错误样本、报告章节差异、连接池状态并导出结果。

## 交付物

- 独立企业尽调评测路由或工作台标签页。
- 数据集、质量运行和性能运行三个区域。
- 总体指标、分类指标、策略对比和历史运行对比。
- 逐题错误、首错归因和报告章节详情。
- P50/P95/P99、吞吐、错误率、分段耗时和连接池展示。
- API 封装和前端单元测试。

## 预计修改范围

- `web/src/apis/company_due_diligence_api.js`
- `web/src/views/CompanyDueDiligenceEvaluationView.vue`
- `web/src/components/company-due-diligence/evaluation/DatasetPanel.vue`
- `web/src/components/company-due-diligence/evaluation/QualityRunPanel.vue`
- `web/src/components/company-due-diligence/evaluation/QualityResultDetail.vue`
- `web/src/components/company-due-diligence/evaluation/PerformanceRunPanel.vue`
- `web/src/components/company-due-diligence/evaluation/PerformanceResultDetail.vue`
- `web/src/router/index.js`
- `web/test/unit/company-due-diligence/evaluation/`

可以复用 `EvaluationBenchmarks.vue` 和 `RAGEvaluationTab.vue` 的数据集、运行列表、进度、分页和错误筛选交互经验，但不要继续扩大原 RAG 评测组件。

## 数据集区域

- 数据集名称、版本、checksum 和业务时点。
- 样本数、问题类型和报告 Golden 数量。
- 人工复核数和人工复核率。
- 查看样本、筛选类型和下载允许导出的文件。
- 明确区分广东企业 Golden 和 30 家 AkShare 公开基线。

## 质量评测区域

- 选择数据集、问题类型、报告模板和查询策略。
- 启动、取消和查看进度。
- 展示字段、关系、路径、证据、拒答、幻觉、章节完整和时效指标。
- 按数据域、问题类型、策略和人工复核状态查看分类指标。
- 错误筛选：只看错误、错误类型、首错阶段、企业和章节。
- 详情：输入、预测、Golden、事实差异、路径差异、缺失来源和运行耗时。

## 性能评测区域

- 选择场景、安全模式、并发、持续时间和数据集。
- 真实只读库模式显示管理员确认和风险提示。
- 展示 P50/P95/P99、吞吐、成功率、超时率和错误率。
- 展示身份、池等待、MySQL、Neo4j、Milvus、LLM 和组装耗时。
- 展示连接池大小、签出、等待、溢出和超时。
- 对比两个运行的代码版本、数据版本和参数。

## 实现步骤

1. 在既有企业尽调 API 文件中增加评测分组。
2. 使用后端契约构建 mock，先完成页面和交互。
3. 复用现有任务进度模式，统一处理排队、运行、终态和取消。
4. 指标卡和图表使用 `base.css` 与既有图表色板。
5. 逐题详情保留原始结构化差异，不只展示自然语言摘要。
6. 对真实库性能运行增加二次确认和硬参数提示。
7. 导出由后端生成，前端只下载，不重新计算指标。
8. 长列表使用分页或虚拟化，避免一次渲染全部样本。

## 验收标准

- [ ] 可以查看冻结数据集和人工复核率。
- [ ] 可以启动、取消并查看质量评测进度。
- [ ] 可以查看总体、分类和逐题质量结果。
- [ ] 可以启动安全性能评测并查看 P50/P95/P99。
- [ ] 真实库性能模式有明确二次确认。
- [ ] 可以查看连接池和分段耗时。
- [ ] 可以下载 JSON、JSONL 或 Markdown 报告。
- [ ] RAG 召回指标与企业尽调严格指标不会混淆。

## 测试范围

### 前端单元测试

- 数据集列表、复核率和筛选。
- 质量运行创建、取消、进度和终态。
- 指标、错误筛选、首错归因和章节差异。
- 性能参数校验、二次确认和连接池展示。
- 文件下载和 API 错误状态。

### 联调验证

- 与 `TASK-008` 数据集和质量运行 API 联调。
- 与 `TASK-009` 性能运行 API 联调。
- 大结果分页和取消状态同步。

## 非目标

- 不在浏览器计算最终评测指标。
- 不允许普通用户启动真实库压力测试。
- 不修改现有 RAG 指标语义。
- 不把未人工复核率隐藏在次要信息中。

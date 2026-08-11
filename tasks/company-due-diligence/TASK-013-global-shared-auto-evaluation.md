# TASK-013：全局共享智能体与无企业必填的自动评测

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 8 横切升级任务 |
| 优先级 | P0 |
| 依赖 | `TASK-006`、`TASK-007`、`TASK-008`、`TASK-009`、`TASK-010`、`TASK-011`、`TASK-012` |
| 可并行任务 | 契约冻结后可并行修改后端注册、自动评测、全局性能聚合和前端入口 |

## 目标

把企业尽调智能体从“需要先选择一家企业的领域工作台”升级为平台级全局共享智能体，并建立不依赖人工输入企业名称的自动评测和全局性能评测。

本任务不否定企业身份解析：用户在自然语言问题中提到企业时，系统仍应自动识别；变化点是 `company_ref` 不再是接口、页面或评测启动的必填字段。

## 需求解释与边界

### 全局共享

- 智能体注册为一个全局实例，`scope=global`、不绑定单个用户或单个工作区。
- 所有有权限的登录用户在智能体目录和主聊天入口看到同一智能体，不为每个用户复制一份配置。
- Prompt、工具白名单、数据源版本、查询策略和评测策略由全局配置统一管理。
- “全局共享”不等于绕过数据权限：后端仍执行用户权限、敏感字段脱敏和来源访问控制。
- 管理员负责配置和运行评测；普通用户只使用智能体，不直接启动真实库性能压力任务。

### 不强制输入企业名称

- `POST /query` 或主智能体调用只要求自然语言 `question`，`company_ref` 改为可选上下文。
- 用户可以直接问“注册资本是多少”“有哪些共同股东”“当前数据覆盖哪些企业”等问题。
- 问题中出现企业名称、股票代码或信用代码时，系统通过 `EntityResolver` 自动提取并形成 `CompanyContext`。
- 问题不含企业但本身是全局统计问题时，系统查询全局数据集并返回聚合结果。
- 问题必须依赖企业但存在多个候选时，返回 `needs_context` 和候选提示；前端不能在提交前强制企业名称表单。
- 评测和性能运行完全不要求用户填写企业名称，统一从现有数据目录或冻结数据集自动生成样本。

## 输入输出契约

### 智能体输入

```json
{
  "question": "当前数据中哪些企业存在共同股东关系？",
  "company_ref": null,
  "conversation_context": null,
  "mode": "auto"
}
```

约束：

- `question` 是唯一必填业务输入；
- `company_ref` 只能作为可选的已确认上下文，不能作为页面进入条件；
- `mode=auto` 由系统自行判断是企业问题、全局统计、关系问题、报告问题还是评测问题；
- 不把内部 `eid` 暴露为普通用户的必填字段。

### 统一状态

- `resolved`：从问题或上下文解析出唯一企业。
- `global_scope`：问题不需要单一企业，按全局数据回答。
- `needs_context`：问题需要企业范围，但当前无法唯一判断。
- `not_found`：问题中明确的企业在当前数据目录不存在。
- `source_unavailable`：数据源不可用，不能伪装为无数据。

## 自动评测范围

评测启动只需要数据版本、评测策略和安全运行参数，不需要企业名称：

```json
{
  "dataset_version": "latest_frozen",
  "scope": "global",
  "sampling_policy": "all_or_stratified",
  "case_families": [
    "field_coverage",
    "relation_consistency",
    "evidence_provenance",
    "report_completeness",
    "refusal_and_ambiguity"
  ]
}
```

### 自动生成评测样本

系统从只读 MySQL 数据目录、Neo4j 投影统计、文本索引元数据和冻结快照自动生成候选样本：

1. 从 `t_enterprise` 枚举全部企业身份，不要求人工逐家输入名称。
2. 根据有值/无值字段生成基本信息、股东、产品、融资、风险、许可和知识产权样本。
3. 根据高置信度图谱边生成共同股东、融资机构和最多三跳路径样本。
4. 根据文本索引元数据生成证据覆盖和来源回溯样本。
5. 根据无数据、歧义、过期和低置信度状态生成拒答样本。
6. 根据报告章节和数据域覆盖生成报告完整性样本。
7. 数据量过大时按照固定种子分层抽样；样本仍记录总体分母、抽样分母和抽样策略。

自动生成的期望值必须标记为 `source_derived`，并保存数据版本、来源表、来源记录键和 checksum。它适合每日回归和数据管道健康检查；正式语义质量门禁仍应单独展示 `human_verified` 样本，不能把程序自生成答案包装成人工 Golden。

## 自动指标

至少计算以下全局指标，并按数据域、问题族、状态和版本分组：

- **数据覆盖率**：924 家企业及各数据域的企业覆盖、记录覆盖和字段非空率。
- **结构化字段一致率**：自动查询结果与只读源事实的值、单位、日期和身份一致率。
- **关系投影一致率**：Neo4j 节点/边与 MySQL 高置信度源关系的数量、方向、属性和来源一致率。
- **路径可复现率**：自动生成的高置信度路径在查询接口中能否按版本复现。
- **证据来源覆盖率**：输出事实是否都有有效 `source_id` 和可追溯记录。
- **报告章节完整率**：全局企业集合中各必需报告章节的可生成率和部分失败率。
- **拒答/歧义准确率**：无数据、过期、冲突和多候选场景是否进入正确状态。
- **时效声明覆盖率**：结果是否携带数据业务日期和过期警告。
- **数据新鲜度**：源表更新时间、投影更新时间和索引更新时间的全局分布。

每个自动评测运行必须输出分母、分子、覆盖范围、跳过原因和 `human_verified` 比例，避免只展示一个无法解释的百分比。

## 全局性能评测

性能评测从“选一家企业后压测”改为全局聚合：

- 采集所有企业问答、全局统计、关系调查、报告和评测请求的运行指标。
- 按时间窗口、接口、意图、工具链、数据源和结果状态聚合，不以企业名称作为必填维度。
- 自动混合全局数据集中的代表性问题，生成安全模式性能基线。
- 真实库性能模式继续需要管理员确认、并发硬上限和取消能力。
- 统计全局 P50/P95/P99、吞吐、成功率、错误率、超时率和取消率。
- 统计身份解析、意图理解、MySQL、Neo4j、Milvus、LLM、报告组装和前端 API 的阶段耗时。
- 统计连接池等待、签出、超时、泄漏、Redis/Tasker 队列等待和 worker 资源。
- 性能日志只保留脱敏的请求、意图和数据版本维度，不写入企业名称、信用代码等明细。

全局性能看板可以按场景和时间筛选，但不能要求用户先选择企业；默认首页展示整个智能体的总体状态。

## API 变更

建议保持兼容并新增/调整以下接口：

```text
POST /api/company-due-diligence/query
POST /api/company-due-diligence/evaluations/auto-runs
GET  /api/company-due-diligence/evaluations/coverage
GET  /api/company-due-diligence/evaluations/auto-runs/{run_id}
POST /api/company-due-diligence/evaluations/performance-runs
GET  /api/company-due-diligence/evaluations/performance-runs/global
```

要求：

- 查询接口不再校验 `company_ref` 必填；
- 自动评测接口不接受单企业必填参数，使用 `scope=global` 和数据版本；
- 全局覆盖率接口返回企业总数、数据域总数、缺失和更新时间分布；
- 全局性能接口返回时间窗口内全量聚合指标和维度摘要；
- 旧接口如仍支持 `company_ref`，只能作为可选兼容参数。

## 前端改造

### 全局智能体入口

- 在全局智能体目录展示一个共享企业尽调智能体。
- 主页面直接显示自然语言输入框，不显示“必须先选择企业”的阻断步骤。
- 企业选择可以作为可选上下文辅助，不得成为提交前置条件。
- 查询返回 `global_scope`、`resolved`、`needs_context` 等状态。
- 全局统计问题直接展示覆盖率、数据时点和聚合结果。

### 自动评测入口

- 评测页面默认选择全局数据版本和自动样本策略。
- 用户不选择企业名称，只选择评测类型、数据版本、抽样策略和运行模式。
- 展示总体覆盖、数据域覆盖、来源覆盖、报告完整和拒答指标。
- 显示 `source_derived` 与 `human_verified` 的分开结果。

### 全局性能入口

- 默认展示整个智能体的全局 P50/P95/P99、吞吐和错误率。
- 支持按时间、意图、工具链和数据源筛选，不提供企业名称作为必填筛选。
- 显示连接池、队列、worker 和各数据源阶段耗时。
- 真实库运行保留管理员确认和安全模式提示。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/agent_registry.py`
- `backend/package/yuxi/company_due_diligence/qa/`
- `backend/package/yuxi/company_due_diligence/evaluation/`
- `backend/package/yuxi/company_due_diligence/performance/`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/server/routers/company_due_diligence_evaluation_router.py`
- `backend/test/unit/company_due_diligence/`
- `backend/test/integration/company_due_diligence/`
- `backend/test/e2e/company_due_diligence/`
- `web/src/apis/company_due_diligence_api.js`
- `web/src/views/CompanyDueDiligenceView.vue`
- `web/src/views/CompanyDueDiligenceEvaluationView.vue`
- `web/src/components/company-due-diligence/`
- `web/test/unit/company-due-diligence/`

## 实现步骤

1. 冻结全局 Agent 注册和可见性契约，确认没有按用户复制的配置。
2. 将 `company_ref` 从请求必填项降为可选上下文，增加自然语言自动实体解析和 `global_scope` 路由。
3. 为无企业问题实现全局统计查询；企业依赖问题无法唯一定位时返回 `needs_context`，不在 UI 阶段强制输入。
4. 建立数据目录扫描器，按当前数据版本枚举企业、数据域、关系、文本索引和报告覆盖。
5. 建立自动样本生成器和 `source_derived` 评测运行，保存分母、来源、版本、抽样和跳过原因。
6. 将质量指标统一聚合到全局评测运行，不按单企业启动评测。
7. 对所有入口埋点，生成全局性能时间窗聚合和阶段耗时。
8. 修改前端入口、评测页和性能页，删除企业名称必填阻断。
9. 增加跨用户共享、无企业输入、全局评测、来源一致性和性能聚合回归。
10. 更新 `TASK-006` 至 `TASK-012` 的输入、评测和性能假设，并重新执行发布门禁。

## 验收标准

- [ ] 登录用户可以从全局目录发现同一个企业尽调智能体实例。
- [ ] 不复制用户级 Agent 配置，不因用户不同生成不同事实源或工具策略。
- [ ] 查询 API 只要求 `question`，不要求 `company_ref` 或企业名称。
- [ ] 前端不以企业名称确认作为进入智能体或提交问题的前置步骤。
- [ ] 问题中包含企业时可以自动解析；无企业的全局统计问题可以直接回答。
- [ ] 企业依赖问题无法唯一定位时返回 `needs_context`，而不是错误或伪造答案。
- [ ] 自动评测可以只用数据版本和全局策略启动，不输入任何企业名称。
- [ ] 自动评测覆盖现有企业目录，并输出数据域覆盖、来源覆盖、关系一致、报告完整和拒答指标。
- [ ] `source_derived` 与 `human_verified` 结果分开统计，未人工复核不宣称正式质量门禁通过。
- [ ] 性能评测按全局请求和全局时间窗口聚合，输出 P50/P95/P99、吞吐、错误率和阶段耗时。
- [ ] 全局性能运行不要求企业名称，真实库压力模式仍有管理员确认和硬上限。
- [ ] 前端可以查看全局评测和全局性能结果，且不泄露企业明细到性能聚合日志。
- [ ] 所有现有简单问答、关系查询、报告和 AkShare 回归不因契约变化失效。

## 测试范围

### 单元测试

- 全局 Agent 注册唯一性、可见性和配置不可复制。
- `company_ref` 缺省、问题内实体识别、全局问题和 `needs_context`。
- 数据目录扫描、自动样本生成、分层抽样、分母和跳过原因。
- `source_derived`/`human_verified` 指标隔离。
- 全局性能分位数、时间窗、维度聚合和脱敏。

### 集成测试

- 多个用户读取同一全局智能体配置。
- 不带企业名称调用查询、自动评测和性能 API。
- 924 家企业数据目录扫描及至少一个有数据/无数据域的自动样本。
- MySQL、Neo4j、Milvus、Tasker 和 worker 阶段指标聚合。

### E2E 测试

- 从主聊天入口直接提问全局统计问题。
- 在问题文本中带企业名称但不传 `company_ref`，成功完成企业查询。
- 对企业依赖问题不传企业信息，返回可解释的 `needs_context`。
- 不选择企业名称启动自动评测并查看全局指标。
- 不选择企业名称查看或启动安全性能评测并查看全局 P95。

## 非目标

- 不允许模型在没有来源时编造企业事实。
- 不取消企业身份歧义处理，只取消前端和 API 的企业名称必填阻断。
- 不把全局共享解释为绕过用户权限或敏感字段访问控制。
- 不把 `source_derived` 自动结果等同于人工业务 Golden。
- 不默认对真实 MySQL 执行无限并发压力测试。
- 不新增 PostgreSQL、MySQL、Redis、MinIO、Milvus 或 Neo4j Docker 服务。

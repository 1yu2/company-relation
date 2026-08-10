# TASK-010：前端查询与报告工作台

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 4 条件并行任务 |
| 优先级 | P0 |
| 依赖 | `TASK-002` 契约；真实联调依赖 `TASK-006`、`TASK-007` |
| 可并行任务 | `TASK-006`、`TASK-007` |

## 目标

新增企业尽调前端工作台，让用户完成企业搜索、简单问答、多跳关系查看、证据核对、完整报告生成、章节进度查看和报告下载。

## 交付物

- 独立企业尽调路由和页面。
- 企业身份搜索与候选确认组件。
- 简单问答与快捷查询组件。
- 事实、路径、证据、数据质量和耗时展示。
- 报告创建、进度、章节详情、取消、重试和下载。
- 独立 API 封装和前端单元测试。

## 预计修改范围

- `web/src/apis/company_due_diligence_api.js`
- `web/src/views/CompanyDueDiligenceView.vue`
- `web/src/components/company-due-diligence/CompanyResolver.vue`
- `web/src/components/company-due-diligence/QuestionPanel.vue`
- `web/src/components/company-due-diligence/QueryResult.vue`
- `web/src/components/company-due-diligence/RelationPathView.vue`
- `web/src/components/company-due-diligence/EvidenceList.vue`
- `web/src/components/company-due-diligence/DataQualityPanel.vue`
- `web/src/components/company-due-diligence/ReportWorkspace.vue`
- `web/src/router/index.js`
- `web/test/unit/company-due-diligence/`

实际组件数量应根据可读性调整，不为单次展示拆出无意义小组件。

## 页面结构

### 企业与问题区

- 企业名称、信用代码或股票代码搜索。
- 唯一企业确认和多候选选择。
- 自然语言问题输入。
- 快捷问题：基本信息、当前股东、融资、风险、知识产权。
- 模式：简单问答、多跳关系、证据检索。
- 高级选项：时间范围、最多跳数、是否显示低置信度线索。

### 查询结果区

- `答案`：简短答案和状态。
- `事实`：字段、值、单位、日期、置信度和来源。
- `关系`：路径节点、边、方向、跳数和置信度。
- `证据`：文本片段、日期、类型和来源。
- `数据质量`：缺失、过期、冲突和低置信度。
- `运行明细`：各阶段耗时和失败阶段。

### 报告区

- 章节选择和报告创建。
- 总体进度、章节进度和当前运行状态。
- 每章事实、证据和数据质量。
- 取消任务、失败章节重试。
- JSON、Markdown 下载。

## 前端状态

必须明确展示：

- 企业解析中、唯一匹配、多候选、未找到、数据源不可用。
- 查询中、成功、部分成功、无数据、过期、冲突、失败。
- 报告排队、运行、部分完成、完成、取消和失败。
- 低置信度线索和确定事实使用不同视觉样式。

## 实现步骤

1. 在 `web/src/apis/company_due_diligence_api.js` 定义所有请求。
2. 按 `TASK-002` 契约建立前端数据模型和 mock。
3. 实现企业身份确认，再开放问题输入和报告创建。
4. 查询结果使用标签页或分区展示统一对象。
5. 关系视图优先复用现有图组件能力，保留可读的路径列表作为无图降级。
6. 报告进度复用现有 Tasker/SSE 或轮询模式，不在组件内散落定时器。
7. 所有错误展示后端明确状态，不把来源不可用解释为无数据。
8. 使用 `base.css` 变量和 `lucide-vue-next` 图标。
9. 新路由默认管理员可见，后续权限变化以后端为最终边界。

## 验收标准

- [ ] 用户可以搜索并确认唯一企业。
- [ ] 用户可以完成简单问答并查看事实和来源。
- [ ] 用户可以查看最多三跳路径和置信度。
- [ ] 用户可以查看文本证据和数据日期。
- [ ] 缺失、过期、冲突和来源不可用状态清晰区分。
- [ ] 用户可以创建报告、查看章节进度、取消和下载。
- [ ] 所有普通 API 请求集中在 `web/src/apis`。
- [ ] 页面在常见桌面宽度和窄屏下可用。

## 测试范围

### 前端单元测试

- 企业解析三种状态：成功、多候选、未找到。
- 简单问答结果、无数据和来源不可用。
- 路径、证据、数据质量和耗时展示。
- 报告进度、部分失败、取消和下载。
- API 参数和错误映射。

### 联调验证

- 与 `TASK-006` 查询 API 联调。
- 与 `TASK-007` 报告 API 和任务进度联调。
- 管理员路由和后端权限验证。

## 非目标

- 不在前端执行事实计算或评测计算。
- 不在浏览器直连 MySQL、Neo4j 或 Milvus。
- 不把聊天 UI 作为唯一查询方式。
- 不实现质量和性能评测页面，该部分由 `TASK-011` 完成。

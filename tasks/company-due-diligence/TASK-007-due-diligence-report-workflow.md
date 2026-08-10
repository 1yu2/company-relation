# TASK-007：完整尽调报告工作流

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 4 并行任务 |
| 优先级 | P0 |
| 依赖 | `TASK-003`、`TASK-004`、`TASK-005` |
| 可并行任务 | `TASK-006`、`TASK-010` |

## 目标

实现按章节并行聚合事实、关系和证据的完整企业尽调报告工作流，输出可追溯 JSON 和 Markdown，并提供任务进度、章节状态、取消、重试和下载能力。

## 交付物

- 报告请求、章节、状态和结果模型。
- 报告数据聚合服务和 LangGraph 工作流。
- 后台任务、进度事件和取消机制。
- JSON、Markdown 报告产物及 checksum。
- 报告创建、列表、详情、重试、取消和下载 API。
- 单元测试、API 集成测试和报告 E2E。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/report/models.py`
- `backend/package/yuxi/company_due_diligence/report/graph.py`
- `backend/package/yuxi/company_due_diligence/report/service.py`
- `backend/package/yuxi/company_due_diligence/report/renderers.py`
- `backend/package/yuxi/storage/postgres/` 中必要的报告运行模型与迁移
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/report/`
- `backend/test/integration/company_due_diligence/test_report_api.py`
- `backend/test/e2e/company_due_diligence/test_report_workflow.py`

报告运行元数据可以写入 Yuxi PostgreSQL；企业事实仍只来自广东企业 MySQL。文件产物复用现有 MinIO，不新增存储服务。

## 报告章节

1. `identity`：企业身份确认。
2. `business`：工商基本信息。
3. `shareholders`：股权结构和出资。
4. `relations`：共同股东、融资网络和多跳路径。
5. `products`：产品与业务线索。
6. `financing`：融资历程。
7. `financials`：财务指标和覆盖范围。
8. `compliance`：处罚、异常和许可。
9. `intellectual_property`：专利、商标和著作权。
10. `news`：新闻证据和重大事件。
11. `risks`：关键风险和待核验事项。
12. `data_quality`：来源、时效、缺失、冲突和置信度声明。

## API 契约

建议接口：

```text
POST /api/company-due-diligence/reports
GET  /api/company-due-diligence/reports
GET  /api/company-due-diligence/reports/{report_id}
POST /api/company-due-diligence/reports/{report_id}/cancel
POST /api/company-due-diligence/reports/{report_id}/sections/{section}/retry
GET  /api/company-due-diligence/reports/{report_id}/download?format=json|markdown
```

创建请求必须包含企业身份、章节、数据时点、是否包含低置信度线索和输出格式。

## 实现步骤

1. 创建任务前解析唯一企业并锁定企业身份快照。
2. 记录代码版本、配置版本、图谱版本、文本索引版本和数据业务时点。
3. 根据章节构建查询计划，独立章节并行执行。
4. 结构化事实来自 `TASK-003`，多跳路径来自 `TASK-004`，文本证据来自 `TASK-005`。
5. 每个章节先形成结构化数据包，再做章节质量检查。
6. 先输出 JSON；Markdown 只能引用 JSON 数据包中的事实和来源。
7. LLM 不可用时使用模板生成基础 Markdown，不影响 JSON 产物。
8. 通过现有 Tasker/worker 模式发布进度、取消信号和终态。
9. 将报告文件、checksum 和导出元数据保存到既有 MinIO。
10. 失败章节标记为 `failed`，整体报告使用 `partial`，不能伪装完整成功。

## 章节状态

- `pending`
- `running`
- `complete`
- `partial`
- `no_data`
- `stale`
- `conflicted`
- `failed`
- `cancelled`

## 验收标准

- [ ] 任意唯一 `eid` 可以创建报告任务。
- [ ] 报告进度可以看到当前章节和完成比例。
- [ ] JSON 报告不依赖在线 LLM 即可生成。
- [ ] Markdown 中的企业事实全部可映射到 JSON `source_id`。
- [ ] 章节无数据、过期、冲突和失败状态被明确展示。
- [ ] 单章节失败不会丢失其他成功章节。
- [ ] 任务可以取消，取消后连接和 worker 资源正常释放。
- [ ] JSON 和 Markdown 文件 checksum 可验证。
- [ ] 不修改原 MySQL，不新增 MinIO 或 PostgreSQL 容器。

## 测试范围

### 单元测试

- 章节计划、状态机、事实引用和 Markdown 渲染。
- 部分失败、取消、单章节重试和 LLM 降级。
- 文件名、checksum 和敏感信息清理。

### 集成测试

- 报告任务 API、进度、取消、重试和下载。
- PostgreSQL 运行元数据与 MinIO 文件产物一致。
- 数据源部分不可用时的章节状态。

### E2E 测试

- 选择固定企业生成全部章节报告。
- 验证关键事实、来源、数据质量声明和两个导出格式。

## 非目标

- 不输出法律、投资或信用评级结论。
- 不把缺失章节交给 LLM 自由补写。
- 不实现前端页面，该部分由 `TASK-010` 完成。
- 不生成 PDF；首版只要求 JSON 和 Markdown。

# TASK-007：统一报告意图与工作流

| 属性 | 内容 |
| --- | --- |
| 状态 | 基础版已完成，统一报告意图升级待实现 |
| 类型 | Wave 4 基础任务 / Wave 7 升级任务 |
| 优先级 | P0 |
| 依赖 | `TASK-003`、`TASK-004`、`TASK-005`、升级后的 `TASK-006` 契约 |
| 可并行任务 | 契约冻结后可与 `TASK-006`、`TASK-010` 并行 |

## 目标

将当前完整尽调报告工作流升级为 `CompanyDueDiligenceAgent` 的 `ReportGenerationTool`。报告既可以由自然语言意图触发，也可以由“生成报告”快捷按钮触发；两种入口必须归一到同一个 `InvestigationIntent`，复用相同企业上下文、工具计划、Claim Ledger、来源校验和任务交付链路。

报告分为专题报告和完整报告。用户只要求关系与风险时，不自动加入无关章节；只有明确要求完整尽调或点击完整报告按钮时才生成标准全量章节。

## 当前基础版

当前报告工作流已实现：

- 十二章节并行聚合。
- 企业、代码、配置、Neo4j 和证据索引版本锁定。
- Tasker/PostgreSQL 任务进度、取消和单章节重试。
- MinIO JSON、Markdown、record 产物与 checksum 下载校验。
- 章节部分失败和整体 `partial` 状态。
- 无在线 LLM 时的确定性模板报告。
- `2026-08-11` 管理员部署态 HTTP E2E 通过。

这些能力继续复用，升级重点是统一入口和报告范围一致性，不重写成熟任务基础设施。

## 升级交付物

- `ReportGenerationTool` 领域智能体工具适配。
- 专题报告、完整报告的统一意图和章节映射。
- 自然语言报告要求与报告按钮的等价意图转换。
- 基于 Claim Ledger 的章节输入和范围校验。
- 统一响应中的 `presentation=topic_report|full_report` 与 `task`。
- 报告范围一致性和输入输出一致性测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/agent/`
- `backend/package/yuxi/company_due_diligence/report/`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/report/`
- `backend/test/integration/company_due_diligence/test_report_api.py`
- `backend/test/e2e/company_due_diligence/`

## 统一报告意图

专题报告示例：

```json
{
  "entities": [
    {"eid": "系统已确认企业", "role": "primary_company"}
  ],
  "intent": {
    "topics": ["relations", "risks"],
    "presentation": "topic_report",
    "delivery": "async_task",
    "time_scope": {"type": "recent_years", "years": 3},
    "depth": "detailed",
    "evidence_required": true
  }
}
```

完整报告按钮生成标准意图：

```json
{
  "entities": [
    {"eid": "当前企业上下文", "role": "primary_company"}
  ],
  "intent": {
    "topics": ["full_due_diligence"],
    "presentation": "full_report",
    "delivery": "async_task",
    "time_scope": {"type": "latest_available"},
    "depth": "detailed",
    "evidence_required": true
  }
}
```

按钮不得直接跳过 `CompanyDueDiligenceAgent` 调用旧报告服务。按钮和自然语言入口经过意图归一后，再共同调用 `ReportGenerationTool`。

## 报告范围映射

### 专题报告

根据 `topics` 只选择必要章节：

| 主题 | 主要章节 | 必要工具 |
| --- | --- | --- |
| `shareholders` | 企业身份、股权结构、出资、数据质量 | 结构化查询 |
| `relations` | 企业身份、多跳关联、关系来源、数据质量 | 图路径 |
| `risks` | 合规、处罚、异常、许可、风险证据、数据质量 | 结构化查询 + 证据检索 |
| `financing` | 融资历程、投资机构、必要关系、数据质量 | 结构化查询 + 可选图路径 |
| `intellectual_property` | 专利、商标、著作权、必要证据、数据质量 | 结构化查询 + 可选证据 |

### 完整报告

标准章节：

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

## 目标执行链路

```text
自然语言报告要求 / 报告快捷按钮
  → CompanyDueDiligenceAgent 归一报告意图
  → 确认企业与必要目标企业
  → 将 topics 映射为专题或完整章节
  → 锁定代码、配置、数据、图谱和证据索引版本
  → 调用结构化、图路径和证据工具
  → 形成报告 Claim Ledger
  → 校验主题范围、来源、时效、冲突和版本
  → 创建或更新 Tasker 报告任务
  → 生成 JSON 报告
  → 模板或受约束 LLM 生成 Markdown
  → 保存 checksum 与产物
  → 统一响应返回 task 和 presentation
```

LLM 只能读取通过校验的报告 Claim Ledger。无法映射到 `source_id` 的企业事实不得进入报告；后置校验失败时保留结构化 JSON 并回退模板 Markdown。

## 目标统一响应

```json
{
  "status": "accepted",
  "understood_request": {
    "topics": ["relations", "risks"],
    "presentation": "topic_report",
    "delivery": "async_task",
    "time_scope": {"type": "recent_years", "years": 3}
  },
  "entities": [],
  "summary": "已开始生成近三年关系与风险专题报告",
  "presentation": {
    "type": "topic_report",
    "components": ["report_progress", "relations", "risks", "sources"]
  },
  "task": {
    "id": "报告任务标识",
    "status": "pending",
    "progress": 0
  }
}
```

现有报告创建、列表、详情、取消、重试和下载接口可继续作为任务资源 API；报告创建入口需要与统一意图适配，不再要求普通用户手工提交内部 `eid` 和章节数组。

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

## 实现步骤

1. 冻结专题报告和完整报告的意图到章节映射。
2. 将现有报告服务封装成 `ReportGenerationTool`。
3. 让自然语言报告意图和报告按钮生成相同内部对象。
4. 使用 `TASK-006` 企业上下文，不重复实现企业解析。
5. 将工具结果汇总为报告 Claim Ledger。
6. 增加主题范围、时间范围和无关章节校验。
7. 统一同步接受响应、异步任务进度和最终产物描述。
8. 保持现有版本锁定、取消、重试、部分失败和 checksum 行为。
9. 接入 LLM 时增加报告事实和来源后置校验。
10. 增加专题报告、按钮等价和输入输出一致性回归。

## 基础版完成记录

- 十二章节、Tasker/PostgreSQL、MinIO 双格式产物、checksum、取消和重试已完成。
- 单章节失败保留 `failed`，整体返回 `partial`，不使用 LLM 补写。
- 当前报告主链路使用确定性模板，`report_with_llm` 明确标记为 `unavailable`。

## 升级验收标准

- [ ] 自然语言可触发专题报告和完整报告。
- [ ] 报告按钮与等价自然语言产生相同内部意图和章节计划。
- [ ] 专题报告只包含用户要求的主题和必要质量声明。
- [ ] 普通用户不需要提交内部 `eid`、章节数组或查询模式。
- [ ] 报告与同步调查复用同一企业上下文、工具和 Claim Ledger 契约。
- [ ] `presentation` 和 `task` 足以驱动前端动态展示和轮询。
- [ ] 章节无数据、过期、冲突和失败状态被明确展示。
- [ ] LLM 不可用或校验失败时仍生成可追溯 JSON 和模板 Markdown。
- [ ] JSON/Markdown 中的企业事实全部可映射到有效 `source_id`。
- [ ] 版本锁定、取消、重试、部分失败和 checksum 现有能力不回归。

## 测试范围

### 单元测试

- 报告意图到章节映射。
- 专题范围和无关章节检查。
- 报告按钮与自然语言等价转换。
- Claim Ledger、来源校验和模板回退。

### 集成测试

- 关系、风险和关系+风险专题报告。
- 完整报告任务、版本锁定、取消、重试和下载。
- 服务部分不可用时的章节状态和整体状态。

### E2E 测试

- 自然语言专题报告从输入到任务产物。
- 报告按钮与自然语言等价性。
- 专题范围一致性、完整报告完整性和部分失败各至少一条。

## 非目标

- 不另建一套企业解析或事实来源规则。
- 不用长提示词替代结构化报告数据包。
- 不要求专题报告固定包含全部十二章节。
- 不在本次文档修订中实现上述升级代码。

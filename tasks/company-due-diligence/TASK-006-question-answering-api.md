# TASK-006：统一调查意图与领域智能体编排

| 属性 | 内容 |
| --- | --- |
| 状态 | 基础版已完成，领域智能体升级待实现 |
| 类型 | Wave 4 基础任务 / Wave 7 升级任务 |
| 优先级 | P0 |
| 依赖 | `TASK-003`、`TASK-004`、`TASK-005` |
| 可并行任务 | 契约冻结后可与 `TASK-007`、`TASK-010` 并行 |

## 目标

将当前确定性问答与多跳 API 升级为 `CompanyDueDiligenceAgent` 统一入口。用户只提供当前企业上下文和自然语言调查要求；领域智能体负责拆解主体、主题、时间范围、深度、输出形式和交付方式，并自动调用结构化查询、图路径、证据检索或报告工具。

“简单问答、多跳关系、证据检索”只描述内部执行能力，不再作为用户模式。

## 当前基础版

当前 `POST /api/company-due-diligence/query` 已实现：

- 企业身份解析与歧义拒答。
- 确定性问题分类和白名单计划。
- MySQL 结构化短链路。
- Neo4j 最多三跳关系查询。
- Milvus 企业过滤证据检索。
- 混合结果合并、来源校验和模板答案。
- Neo4j 在线但无激活投影时按来源不可用降级。

这些能力继续作为领域智能体工具复用，不重写底层服务。

## 升级交付物

- `CompanyDueDiligenceAgent` 领域智能体入口。
- 会话级 `CompanyContext`。
- `InvestigationIntent`、`UnderstoodRequest`、`Presentation` 和 `Task` 契约。
- 企业提及提取、必要消歧和上下文复用。
- 规则优先、LLM 可选的意图理解节点。
- 受控工具计划、Claim Ledger 和后置质量校验。
- 同步摘要/列表/关系响应与异步报告路由。
- 旧 `company_ref + question + mode` 请求的最小兼容适配。
- 单元测试、集成测试和领域智能体 E2E。

## 建议修改范围

- `backend/package/yuxi/company_due_diligence/agent/`
- `backend/package/yuxi/company_due_diligence/qa/`
- `backend/package/yuxi/company_due_diligence/contracts.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/agent/`
- `backend/test/integration/company_due_diligence/test_query_api.py`
- `backend/test/e2e/company_due_diligence/`

具体路径以实现时的现有包结构为准，不为单次逻辑创建多余抽象。

## 目标输入契约

用户可见请求：

```json
{
  "conversation_id": "会话标识",
  "company_context": {
    "eid": "前端已确认时可携带",
    "name": "标准企业名称"
  },
  "message": "请用报告形式分析这家企业近三年的关系和风险"
}
```

普通用户不直接输入 `eid`；该字段由已确认企业上下文自动携带。没有上下文时，智能体从 `message` 中提取企业提及并调用解析工具。

内部统一意图：

```json
{
  "entities": [
    {"eid": "系统解析结果", "role": "primary_company"}
  ],
  "intent": {
    "topics": ["relations", "risks"],
    "presentation": "topic_report",
    "delivery": "async_task",
    "time_scope": {"type": "recent_years", "years": 3},
    "depth": "detailed",
    "evidence_required": true
  },
  "constraints": {
    "hop_limit": 3,
    "include_low_confidence": false
  }
}
```

## 目标输出契约

```json
{
  "status": "complete",
  "understood_request": {
    "topics": ["shareholders"],
    "presentation": "summary_with_list",
    "delivery": "sync",
    "time_scope": {"type": "current"}
  },
  "entities": [],
  "summary": "直接回答用户问题",
  "findings": [],
  "facts": [],
  "relations": [],
  "paths": [],
  "evidence": [],
  "sources": [],
  "data_quality": {},
  "presentation": {
    "type": "summary_with_list",
    "components": ["summary", "shareholder_table", "sources"]
  },
  "task": null,
  "latency": {}
}
```

前端根据 `presentation` 展示结果；用户不通过请求中的 `mode` 指定执行工具。

## 领域智能体工具

```text
CompanyDueDiligenceAgent
├── EntityResolutionTool
├── StructuredQueryTool
├── GraphPathTool
├── EvidenceSearchTool
├── ReportGenerationTool
└── QualityValidationTool
```

工具边界：

- `EntityResolutionTool` 最终确认 `eid`，LLM 只能提取名称和候选线索。
- `StructuredQueryTool` 只接受白名单业务操作。
- `GraphPathTool` 最多三跳，并返回完整节点、边、方向和来源。
- `EvidenceSearchTool` 强制企业、类型、日期和版本过滤。
- `ReportGenerationTool` 由报告意图触发，具体任务见 `TASK-007`。
- `QualityValidationTool` 建立 Claim Ledger 并阻止无来源事实进入输出。

## 目标编排

```text
load_company_context
  → resolve_referenced_entities
  → understand_investigation_intent
  → validate_intent_schema
  → build_tool_plan
  → execute_controlled_tools
  → build_claim_ledger
  → validate_claims
  → compose_response_or_create_report
  → select_presentation
  → finalize_metrics
```

明确问题优先由确定性规则解析；只有复合自然语言意图需要 LLM。LLM 不可用或意图 schema 校验失败时，系统应返回澄清问题或落入可证明正确的规则路径。

## 输入输出一致性示例

| 用户要求 | 必要工具 | presentation | 核心输出 |
| --- | --- | --- | --- |
| “股东有哪些？” | `StructuredQueryTool` | `summary_with_list` | 股东摘要、列表、来源 |
| “A 和 B 有什么关系？” | `EntityResolutionTool`、`GraphPathTool` | `relation_result` | 关系结论、完整路径 |
| “近三年的风险” | 结构化风险 + 必要证据 | `risk_summary` | 风险事件、时间、证据 |
| “用报告形式分析关系和风险” | 图路径、风险、证据、报告 | `topic_report` | 异步专题报告任务 |

## 实现步骤

1. 冻结 `CompanyContext`、`InvestigationIntent` 和统一响应 schema。
2. 将当前查询、图路径和证据服务包装为薄工具适配层。
3. 实现企业提及提取、上下文复用和必要消歧。
4. 先用确定性规则覆盖明确主题和报告快捷入口。
5. 接入可选 LLM 处理复合主题、时间、深度和输出形式。
6. 校验意图 schema，并生成受控工具计划。
7. 合并工具结果形成 Claim Ledger，执行来源、时效和冲突校验。
8. 根据 `presentation` 生成同步结果或调用 `TASK-007` 创建异步报告。
9. 为旧请求增加单向适配，内部统一转换成新意图。
10. 增加分层指标和端到端回归。

## 基础版完成记录

- 确定性分类和白名单计划已覆盖字段、列表、图路径、文本证据和混合问题。
- 结构化问题只调用 MySQL，多跳最多三跳，文本问题强制企业过滤。
- 模板答案不依赖在线 LLM，每条事实、关系、路径和证据都引用 `source_id`。
- `2026-08-11` 管理员部署态 HTTP E2E 通过；来源不可用和真实无数据已区分。

## 升级验收标准

- [ ] 用户不需要选择简单问答、多跳关系或证据检索模式。
- [ ] 普通用户不需要填写内部 `eid`，已确认企业上下文可以跨轮复用。
- [ ] 复合问题能正确拆解企业、主题、时间、深度、展示和交付方式。
- [ ] 报告自然语言意图和报告按钮进入同一个内部意图与工具链路。
- [ ] 简单问题保持短链路，不因统一智能体无条件调用所有工具。
- [ ] 每个最终事实都能映射到 Claim Ledger 和有效 `source_id`。
- [ ] LLM 不能决定最终 `eid`，不能生成任意 SQL/Cypher。
- [ ] LLM 或后置校验失败时回退模板或返回明确澄清状态。
- [ ] 输出 `presentation` 与用户要求一致，无关内容不被自动加入。
- [ ] 系统主智能体和独立工作台复用同一个领域智能体服务。

## 测试范围

### 单元测试

- 企业上下文复用、企业提及提取和歧义处理。
- 主题、时间、深度、展示和交付解析。
- 工具计划、参数上限和无关工具抑制。
- Claim Ledger、来源校验、模板回退和 `presentation` 路由。
- 旧请求兼容适配。

### 集成测试

- 调用真实 MySQL、Neo4j 和 Milvus 的固定样本。
- 同步摘要、列表、关系结果与异步报告路由。
- 服务部分不可用时的明确状态。

### E2E 测试

- 企业确认后连续询问股东、关系和风险，验证上下文复用。
- 自然语言专题报告和报告按钮产生等价内部意图。
- 输入输出一致性、正确拒答和模板回退各至少一条。

## 非目标

- 不重写 `TASK-003`、`TASK-004` 和 `TASK-005` 底层数据服务。
- 不开放任意 SQL/Cypher 调试接口给普通用户。
- 不把 LLM 或 AkShare 作为广东企业事实主库。
- 不在本次文档修订中实现上述升级代码。

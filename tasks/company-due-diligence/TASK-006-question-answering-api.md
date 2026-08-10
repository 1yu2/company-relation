# TASK-006：简单问答与多跳 API

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 4 并行任务 |
| 优先级 | P0 |
| 依赖 | `TASK-003`、`TASK-004`、`TASK-005` |
| 可并行任务 | `TASK-007`、`TASK-010` |

## 目标

把企业身份解析、结构化查询、图谱路径和文本证据组合成统一企业问答 API，优先保证简单问答可用，并支持最多三跳的关系问题。

## 交付物

- 问题类型和查询计划模型。
- LangGraph 或等价线性编排器。
- 简单字段、列表、风险解释和多跳问题路由。
- 统一问答 API 和来源完整性检查。
- 同步响应与必要的流式/任务状态支持。
- 单元测试、API 集成测试和核心问答 E2E。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/qa/models.py`
- `backend/package/yuxi/company_due_diligence/qa/planner.py`
- `backend/package/yuxi/company_due_diligence/qa/graph.py`
- `backend/package/yuxi/company_due_diligence/qa/service.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/qa/`
- `backend/test/integration/company_due_diligence/test_query_api.py`
- `backend/test/e2e/company_due_diligence/test_question_answering.py`

## API 契约

建议接口：

```text
POST /api/company-due-diligence/query
```

请求：

```json
{
  "company_ref": {"name": "企业名称"},
  "question": "当前股东及出资情况是什么？",
  "mode": "qa",
  "as_of": null,
  "hop_limit": 3,
  "top_k": 10,
  "include_low_confidence": false
}
```

响应使用 `TASK-002` 公共契约，并包含 `answer`、`facts`、`relations`、`paths`、`evidence`、`sources`、`data_quality` 和分段耗时。

## 实现步骤

1. 先校验请求并解析唯一企业身份。
2. 将问题分类为结构化字段、结构化列表、多跳路径、文本解释或混合问题。
3. 生成受控查询计划，不允许任意 SQL 或无限制 Cypher。
4. 简单字段和列表问题只调用结构化查询服务，保持短链路。
5. 多跳问题调用图谱服务，并校验每条边的来源和置信度。
6. 文本解释问题调用证据检索，并用结构化来源核验企业和日期。
7. 混合问题可并行执行多个服务，再统一去重和冲突检查。
8. LLM 只根据验证后的数据包组织答案，输出事实必须引用 `source_id`。
9. 记录各节点耗时、工具调用、失败阶段和最终状态。
10. 实现歧义、无数据、过期、低置信度和来源不可用的拒答规则。

## 首版问题验收集

- 企业注册资本、法人和经营状态。
- 当前股东、历史股东、持股比例、认缴和实缴。
- 产品和融资历史。
- 行政处罚、许可和知识产权数量。
- 两家企业共同股东。
- 企业与投资机构的两到三跳关联。
- 无路径、同名低置信度和数据过期问题。

## 验收标准

- [ ] 简单问答不依赖 Neo4j 或 Milvus 即可回答结构化问题。
- [ ] 多跳问题返回完整节点、边、方向、跳数和来源。
- [ ] 无法唯一解析企业时不执行后续查询。
- [ ] LLM 不可用时仍可返回结构化结果和模板化简答。
- [ ] 输出事实全部存在有效 `source_id`。
- [ ] 来源不可用与真实无数据明确区分。
- [ ] `hop_limit` 不超过 3，`top_k` 和列表分页有上限。
- [ ] 响应包含 MySQL、Neo4j、Milvus、LLM 和总耗时。

## 测试范围

### 单元测试

- 问题分类、查询计划和参数校验。
- 简单、图谱、文本和混合路由。
- 来源完整性、冲突、拒答和模板答案。
- LLM 输出引用检查。

### 集成测试

- 调用真实 MySQL、Neo4j 和 Milvus 的固定样本。
- 验证服务部分不可用时的明确状态。
- 管理员权限、输入上限和错误响应。

### E2E 测试

- 从 API 输入自然语言问题到完整统一响应。
- 简单问答、多跳查询和正确拒答各至少一条。

## 非目标

- 不生成完整尽调报告。
- 不开放任意 SQL/Cypher 调试接口给普通用户。
- 不把 AkShare 作为广东企业问题默认查询源。
- 不实现浏览器页面，该部分由 `TASK-010` 完成。

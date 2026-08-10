# TASK-002：统一契约与企业身份解析

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | 串行基础任务 |
| 优先级 | P0 |
| 依赖 | `TASK-001` |
| 可并行任务 | 完成后解锁 `TASK-003`、`TASK-004`、`TASK-005` 和前端 mock |

## 目标

冻结企业尽调的统一输入输出对象，并把用户输入的 `eid`、统一社会信用代码、股票代码、企业全称或简称解析成唯一企业身份。

## 交付物

- `CompanyReference`、`ResolvedCompany` 和候选企业模型。
- `Entity`、`Fact`、`Relation`、`Path`、`Evidence`、`Source`、`DataQuality`、`LatencyBreakdown` 模型。
- 企业身份解析服务和搜索接口。
- 字段规范化、日期、金额、置信度和来源约定。
- 前后端可共享的 API 示例和 JSON Schema。
- 单元测试和身份解析集成测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/models.py`
- `backend/package/yuxi/company_due_diligence/entity_resolution.py`
- `backend/package/yuxi/company_due_diligence/repositories/enterprise.py`
- `backend/server/routers/company_due_diligence_router.py`
- `backend/test/unit/company_due_diligence/test_entity_resolution.py`
- `backend/test/integration/company_due_diligence/test_company_resolution_api.py`
- `docs/agents/company-due-diligence-implementation-guide.md`

## 身份解析优先级

1. `eid` 精确匹配。
2. 统一社会信用代码精确匹配。
3. 股票代码精确匹配。
4. 企业标准全称精确匹配。
5. 规范化全称精确匹配。
6. 简称或模糊名称只返回候选，不自动选中。

## API 契约

建议接口：

```text
POST /api/company-due-diligence/companies/resolve
GET  /api/company-due-diligence/companies/search?q=...
```

解析状态：

- `resolved`：唯一高置信度企业。
- `ambiguous`：多个候选，必须由用户选择。
- `not_found`：当前数据源无匹配。
- `source_unavailable`：只读库不可用，不能伪装为未找到。

## 统一契约要求

所有查询结果必须表达：

- 标准企业身份和本次匹配方式；
- 事实的规范化值、展示值、单位和有效日期；
- 关系方向、类型、属性和置信度；
- 多跳路径的节点、边和跳数；
- 文本证据片段和文档元数据；
- 来源表、来源记录键、业务日期和查询时间；
- 缺失、过期、冲突和低置信度警告；
- 各检索阶段耗时。

## 实现步骤

1. 从 `t_enterprise` 和 `listed_company_gd` 定义唯一企业身份字段。
2. 实现不改变原库的名称规范化，仅用于内存比较和查询参数。
3. 对准确标识执行精确查询，对名称执行受限候选查询。
4. 明确候选排序依据，但不把排序第一自动视为确定结果。
5. 定义高、中、低置信度枚举和允许进入事实答案的规则。
6. 定义金额、比例、日期和空值的统一表达。
7. 定义 `Source` 的稳定记录键，确保可追溯且不包含凭据。
8. 生成前端可使用的示例响应。

## 验收标准

- [ ] `eid`、信用代码、股票代码和标准全称可以稳定解析同一企业。
- [ ] 多候选名称返回 `ambiguous`，不会静默选中。
- [ ] 无匹配与数据源故障使用不同状态。
- [ ] 所有公共对象可 JSON 序列化并有稳定字段定义。
- [ ] 金额、比例、日期、空值和置信度表达一致。
- [ ] 任何企业事实都可以引用一个 `source_id`。
- [ ] 前端 mock 与后端模型字段一致。

## 测试范围

### 单元测试

- 五种精确身份解析路径。
- 简称、多候选、同名和不存在企业。
- 名称规范化不改变原始展示名称。
- DTO 序列化、枚举、金额、日期和来源验证。

### 集成测试

- 对审计确认的企业执行多标识交叉解析。
- 搜索结果分页和上限。
- 数据源不可用时的错误状态和脱敏日志。

## 非目标

- 不建立跨全网企业实体库。
- 不用 LLM 决定企业唯一身份。
- 不把自然人同名自动合并。
- 不实现业务问答和报告。

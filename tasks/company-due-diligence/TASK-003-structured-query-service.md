# TASK-003：结构化查询服务

| 属性 | 内容 |
| --- | --- |
| 状态 | 待开始 |
| 类型 | Wave 3 并行任务 |
| 优先级 | P0 |
| 依赖 | `TASK-002` |
| 可并行任务 | `TASK-004`、`TASK-005` |

## 目标

基于只读 Repository 实现企业基本信息、股东出资、产品、融资、风险、许可、知识产权和可映射财务指标的精确结构化查询服务。

## 交付物

- 按数据域拆分的只读 Repository。
- 结构化查询服务和白名单问题类型。
- 原始字段到统一 `Fact`、`Relation` 和 `Source` 的映射。
- 分页、排序、时间过滤和大表查询上限。
- 数据缺失、过期、冲突和覆盖率说明。
- 单元测试和真实库集成测试。

## 预计修改范围

- `backend/package/yuxi/company_due_diligence/repositories/enterprise.py`
- `backend/package/yuxi/company_due_diligence/repositories/shareholder.py`
- `backend/package/yuxi/company_due_diligence/repositories/product.py`
- `backend/package/yuxi/company_due_diligence/repositories/finance.py`
- `backend/package/yuxi/company_due_diligence/repositories/risk.py`
- `backend/package/yuxi/company_due_diligence/repositories/intellectual_property.py`
- `backend/package/yuxi/company_due_diligence/repositories/financial_metric.py`
- `backend/package/yuxi/company_due_diligence/structured_query_service.py`
- `backend/test/unit/company_due_diligence/`
- `backend/test/integration/company_due_diligence/`

## 首版查询类型

- `enterprise_profile`
- `registered_capital`
- `legal_representative`
- `business_status`
- `current_shareholders`
- `historical_shareholders`
- `shareholder_contributions`
- `products`
- `financing_history`
- `administrative_penalties`
- `environmental_penalties`
- `abnormal_operations`
- `licenses`
- `patent_summary`
- `trademark_summary`
- `copyright_summary`
- `financial_metric_series`

## 实现步骤

1. 为每个数据域确认表、连接字段、索引条件和默认排序。
2. 所有查询以唯一 `eid` 为入口，不在业务 Repository 中重新猜企业名称。
3. 解析股东出资和融资 JSON 字段，保留原始来源记录键。
4. 规范化金额、币种、比例和日期，同时保留展示值。
5. 大表查询使用企业键、日期和分页，不执行全表正文扫描。
6. 财务和新闻映射不足时只返回已覆盖结果和覆盖警告。
7. 对风险、许可和财务结果返回业务日期和数据时效状态。
8. 记录 MySQL 查询耗时、返回条数和截断状态。

## 服务输入输出

输入：唯一 `eid`、查询类型、时间范围、分页和排序参数。

输出：

```json
{
  "query_type": "current_shareholders",
  "entities": [],
  "facts": [],
  "relations": [],
  "sources": [],
  "data_quality": {},
  "pagination": {},
  "latency_ms": 0
}
```

## 验收标准

- [ ] 任意有效 `eid` 可查询企业基本信息并携带来源。
- [ ] 当前和历史股东明确区分。
- [ ] 股东比例、认缴和实缴与源 JSON 一致。
- [ ] 融资投资人 JSON 全量解析且不会丢失类型信息。
- [ ] 处罚、许可和知识产权查询具备分页和时间范围。
- [ ] 财务映射不足时返回覆盖警告，不误报为企业无财务数据。
- [ ] 所有列表有确定排序和最大返回上限。
- [ ] 查询不会执行 DDL/DML 或无界大表扫描。

## 测试范围

### 单元测试

- 各域字段映射、JSON 解析和规范化。
- 当前/历史状态、空值、日期异常和金额异常。
- 分页、排序、截断和数据质量警告。

### 集成测试

- 每个数据域至少选择一个有数据企业和一个无数据企业。
- 校验源行数、返回条数和来源记录键。
- 对专利、许可等大表检查 SQL 使用企业过滤和 `LIMIT`。
- 连接池在批量查询后无泄漏。

## 非目标

- 不生成自然语言最终答案。
- 不做多跳图遍历。
- 不进行向量检索。
- 不修复或回写原始财务、新闻映射字段。

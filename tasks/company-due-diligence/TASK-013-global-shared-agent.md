# TASK-013：全局共享智能体与自然语言企业上下文

| 属性 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 类型 | 横切升级任务 |
| 优先级 | P0 |
| 依赖 | `TASK-006`、`TASK-007`、`TASK-010`、`TASK-012` |

## 目标

把企业尽调能力升级为平台级全局共享智能体，并取消企业名称和内部 `eid` 的页面级必填限制。用户只需要在主聊天窗口说明调查问题，系统负责判断全局范围、解析企业主体并选择受控工具。

## 核心规则

- 智能体使用唯一 slug `company-due-diligence`，以全局资源注册，不为每个用户复制配置。
- `question` 是唯一必填业务输入，`company_ref` 仅作为可选的已确认上下文。
- 无需单一企业的白名单问题返回 `global_scope`。
- 需要企业但当前无法唯一确认时返回 `needs_context` 和候选提示。
- 问题中明确企业但目录不存在时返回 `not_found`。
- 数据源不可用时返回 `source_unavailable`，不能伪装成无数据。
- Prompt、工具白名单和查询策略由全局智能体统一维护，后端仍执行权限与来源校验。

## 统一入口

```text
POST /api/company-due-diligence/investigations
POST /api/company-due-diligence/query
POST /api/company-due-diligence/reports
```

主聊天调用 `investigate_company` Tool，最终与 REST 入口复用 `CompanyDueDiligenceAgent.investigate`，不形成第二套事实逻辑。

## 验收标准

- [x] 所有有权限的登录用户看到同一个企业尽调智能体。
- [x] 不选择企业也可以提交自然语言问题。
- [x] 企业全称、股票代码和信用代码可以从问题中自动解析。
- [x] 企业上下文可以跨轮复用。
- [x] 全局问题、缺少上下文、企业不存在和数据源不可用有不同状态。
- [x] 主聊天、REST 查询、关系调查和报告使用同一领域服务。
- [x] 输出事实、关系、路径和证据均经过 Claim Ledger 来源校验。

## 非目标

- 不提供独立企业尽调工作台。
- 不提供企业尽调专属评测数据集、指标、结果页或性能看板。
- 不绕过用户权限、数据脱敏和来源访问控制。

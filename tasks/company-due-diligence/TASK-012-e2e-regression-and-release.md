# TASK-012：端到端回归与发布门禁

| 属性 | 内容 |
| --- | --- |
| 状态 | 自动回归与升级门禁已完成；正式发布受人工 Golden 阻塞 |
| 类型 | Wave 6 基础版收口 / Wave 7 升级收口 |
| 优先级 | P0 |
| 依赖 | `TASK-006`、`TASK-007`、`TASK-008`、`TASK-009`、`TASK-010`、`TASK-011` |
| 可并行任务 | 无；这是最终验收任务 |

## 目标

把企业上下文、自然语言意图、受控工具计划、Claim Ledger、动态输出、专题/完整报告、质量评测、性能评测和两个前端工作台串成可重复端到端链路，并建立输入输出一致性的发布门禁。

## 交付物

- 核心链路 E2E 测试套件。
- 固定脱敏测试数据和运行脚本。
- 质量与性能回归门禁。
- Docker Compose 开发验证步骤。
- 数据源、投影、任务和评测故障排查文档。
- README、正式文档、导航和 changelog 最终同步。
- 发布验收报告。

## 预计修改范围

- `backend/test/e2e/company_due_diligence/`
- `web/test/unit/company-due-diligence/`
- `backend/scripts/company_due_diligence/`
- `docs/agents/company-due-diligence-system.md`
- `docs/agents/company-due-diligence-implementation-guide.md`
- `README.md`
- `docs/.vitepress/config.mts`
- `docs/develop-guides/changelog.md`
- `tasks/company-due-diligence/README.md`

## 端到端场景

1. 搜索和确认企业后，连续使用“这家企业”提问并复用上下文。
2. 用户不选择内部模式，直接询问股东并得到摘要、列表和来源。
3. 问题中出现第二家企业时完成必要消歧并返回最多三跳完整路径。
4. 查询产品、融资、处罚、许可和知识产权时动态展示对应业务列表。
5. 低置信度同名关系正确降级或拒答。
6. 新闻或处罚问题返回文本证据和结构化来源。
7. 自然语言生成关系与风险专题报告，不加入无关章节。
8. 报告按钮与等价自然语言生成相同完整报告意图和任务。
9. 查看报告进度、处理部分失败并下载 JSON/Markdown。
10. 运行人工 Golden 质量评测并查看实体、意图、计划、事实和 presentation 首错。
11. 运行安全性能评测并查看 P50/P95/P99 和连接池指标。
12. MySQL、Neo4j、Milvus 或 LLM 单项不可用时返回正确状态和模板回退。

## 发布门禁

正式阈值在首批人工 Golden 完成后冻结，至少包含：

- 关键字段严格准确率不得低于约定基线。
- 高置信度路径准确率不得低于约定基线。
- 输出企业事实必须全部存在来源，幻觉门禁接近 0。
- 歧义、无数据、过期和低置信度拒答必须通过。
- 企业上下文、主题、时间、输出形式和交付方式识别达到人工冻结阈值。
- 必要工具召回率和无关工具调用率达到人工冻结阈值。
- 输入主题覆盖率、无关内容率和 `presentation` 匹配率达到人工冻结阈值。
- 报告必需章节和数据质量声明完整。
- 专题报告不遗漏指定主题，也不自动加入无关章节。
- 核心查询和报告性能不超过约定 P95。
- 连接池无泄漏、无非预期溢出。
- AkShare 公开基线回归不得因新系统接入而破坏。

## 当前发布状态

截至 2026 年 8 月 11 日，自动门禁和管理员部署态 HTTP E2E 已通过，最终发布状态仍为 `blocked`：

- 尚无 `human_verified` 广东企业 Golden，不能冻结正式质量阈值。

当前报告工作流不调用在线 LLM，`report_with_llm` 明确标记为 `unavailable`。这是已显式处理的能力限制，不属于当前发布阻塞，也不能被无 LLM 结果伪装成在线 LLM 性能数据。

发布报告由 `backend/scripts/company_due_diligence/generate_release_gate.py` 生成到 `saves/company_due_diligence/release/`；`--enforce` 在上述阻塞解除前必须返回非零。

升级后的发布报告会显式记录 `/investigations`、主智能体工具 `investigate_company`、意图/工具/`presentation`/Claim/输入输出一致性指标和管理员部署态 E2E。报告文件仍写入 `saves/company_due_diligence/release/` 且不提交 Git。

### 解阻步骤

1. 保持专用测试账号仅存在于本地、不提交的 `.env`；门禁会创建临时测试容器读取当前 `env_file`，无需把测试凭据注入长期运行的 `api` 容器。
2. 由真实业务或数据复核人员检查广东企业样本，填写 `reviewed_by`、`reviewed_at`、`reviewer_note` 并将状态改为 `human_verified`，再使用 `freeze_quality_dataset.py --write` 冻结 checksum。
3. 执行 `scripts/company_due_diligence/run_release_gate.sh --enforce`；只有人工门禁具备资格且报告为 `ready` 后，命令才返回成功并允许勾选剩余验收项。

## 一键验证

先使用本地不提交的 `.env` 启动 Compose，再执行：

```bash
scripts/company_due_diligence/run_release_gate.sh
```

正式发布或 CI 必须追加 `--enforce`，使任何人工阻塞项或自动门禁失败都返回非零：

```bash
scripts/company_due_diligence/run_release_gate.sh --enforce
```

需要覆盖端口或其他 Compose 设置时，通过环境变量追加 override，不修改主 Compose：

```bash
COMPANY_DD_COMPOSE_OVERRIDE=/path/to/company-dd-compose.override.yml \
  scripts/company_due_diligence/run_release_gate.sh
```

脚本依次运行后端单元/集成/E2E、AkShare 回归、宿主机与容器敏感信息审计、发布报告生成、前端单测、ESLint 和生产构建。管理员 E2E 使用临时 `api` 测试容器读取当前 `.env`，AkShare 产物写入被忽略的发布目录；没有管理员账号时 E2E 会按测试规范跳过，发布报告仍保持阻塞。

## 实现步骤

1. 确认所有前置任务状态和 API 契约已经冻结。
2. 准备不含凭据和个人明细的固定测试数据。
3. 在 Docker Compose 中检查现有服务和日志，不重复拉取镜像。
4. 按企业上下文、自然语言调查、关系、专题/完整报告、质量和性能顺序执行 E2E。
5. 注入单数据源故障并验证显式失败和恢复。
6. 执行相关单元测试、集成测试、E2E、前端测试和 Lint。
7. 运行质量 Golden 和安全性能基线，保存版本化报告。
8. 更新文档中的“当前状态”，不得把未通过项写为已完成。
9. 输出发布验收报告和已知限制。
10. 将意图、工具计划、Claim Ledger 和 `presentation` 纳入发布门禁。

## 验收标准

- [x] 当前基础版可以从前端完成问答并查看来源。
- [x] 当前基础版可以完成多跳查询并查看路径。
- [x] 当前基础版可以生成并下载完整报告。
- [x] 当前基础版可以启动和查看质量评测。
- [x] 当前基础版可以启动安全性能评测。
- [x] 核心 API、任务进度和导出产物状态一致。
- [x] 质量和性能门禁报告包含代码、数据和配置版本。
- [x] 数据源故障不会被误报为无数据。
- [x] 日志、测试、快照和文档没有敏感连接信息。
- [x] 所有当前基础版相关自动回归通过。
- [x] 用户无需选择内部模式即可完成事实、关系和证据调查。
- [x] 企业上下文能够跨轮复用，普通用户无需填写 `eid`。
- [x] 自然语言专题报告与报告按钮共用统一意图链路。
- [x] 动态 `presentation` 与用户要求一致。
- [x] 开发烟测中的意图、工具计划和输入输出一致性门禁通过。
- [ ] 含 `human_verified` 样本的广东企业 Golden 已冻结并达到正式阈值。

## 必跑测试

```text
后端相关单元测试
后端企业尽调 API 集成测试
后端企业尽调 E2E 测试
前端企业尽调单元测试
领域智能体意图与工具计划回归
输入输出一致性与 presentation 回归
30 家（120 题）AkShare 企业关系回归
质量 Golden 回归
安全模式性能回归
make format
项目既有 Lint
```

具体命令以 `docs/develop-guides/testing-guidelines.md` 和项目 Makefile 为准，在运行中的 Docker 容器执行。

## Wave 8 回归补充

- 管理员 HTTP E2E 已覆盖无企业全局统计、问题内企业全称解析、`needs_context`、无企业自动评测和无企业安全性能评测。
- 真实只读目录断言分母为 924，并校验许可/IP 覆盖、全局性能脱敏维度和性能报告不保留企业引用。
- 自动回归通过不改变正式发布阻塞条件：仍需 `human_verified` 广东企业 Golden。

## 非目标

- 不在没有数据源负责人允许时执行真实库高并发容量测试。
- 不自动提交或推送 GitHub；提交和推送由用户明确要求后执行。
- 不把已知数据覆盖限制包装成系统全量能力。
- 不处理与企业尽调无关的既有测试失败。
- 不用开发烟测替代人工冻结的正式广东企业 Golden。

# T7：平台级评测结果只读查看页面

关联总领文件：[task.md](./task.md)。本子任务只实现评测资源和结果查看，不把页面变成实验编排入口。

## 1. 目标

让管理员在 `/evaluation` 查看平台所有评测来源的数据集版本、实验状态、逐题 Rollout、通过指标、失败原因和 Trace 关键事件，并能沿 source_ref 回到 Agent/Tool/MCP、RAG 或 Graph 原始链路。页面必须清晰区分未测量、失败、运行中和终态。

## 2. 已确认事项

- D5=B：首轮必须交付平台级只读查看页面；不在页面创建数据集、发布版本、运行、取消或重试实验。
- D8=A：沿用管理员权限和内部评测可见性。
- 前端 API 按项目规范放在 `web/src/apis`，样式使用 Less 和 `base.css` 变量，图标优先 `lucide-vue-next`。
- 页面消费 T6 API，不直接读取 Langfuse、Redis 或数据库。

## 3. 剩余疑问

1. 现有 `/evaluation` 页面是硬编码 Fixture，替换为平台 API 数据后，三类来源是否需要不同的详情分组。
2. 前端项目的测试命令、Playwright 配置和管理员登录测试凭据注入方式。
3. Trace 展开层需要展示哪些脱敏字段、默认最大事件数和分页方式。
4. 空值统一显示 `null`、短横线还是“未测量”的本地化文案。

## 4. 明确不包含

- 不新增创建/编辑/运行/取消/删除按钮，不显示可执行真实工具的控件。
- 不展示凭据、私有 chain-of-thought、未脱敏工具载荷或内部错误堆栈。
- 不在页面复制指标计算逻辑；后端返回 metric version 和值。
- 不顺手重构全局布局、主题或无关视图。

## 5. 具体文件、视图和交互变更

候选文件：

- `web/src/apis/evaluation_api.js`：API client、类型/响应归一化。
- `web/src/views/EvaluationPlatformView.vue`：列表、详情、Trace 和失败查看。
- 相关 Less/测试文件按现有目录约定新增。

页面最小区域：

1. 来源类型（Agent/Tool/MCP、RAG、Graph）筛选、Dataset/Version 选择和 checksum/发布状态。
2. Experiment 概览：状态、配置快照版本、完成/总题数、`pass@1/pass@k/pass^k/best@k`。
3. Rollout 表格：task_key、attempt、状态、task_pass、主要失败原因、耗时（未测量为明确空值）。
4. Trace/Failure 详情：按 sequence 展示脱敏事件、父子关系、source_ref 和 reason code。

列表必须处理 loading、空数据、403、404、网络错误、部分 Trace 缺失和运行中刷新；轮询如需要只能读取后端状态，不能自行改变状态。

## 6. 任务清单

- [ ] 核对现有 Evaluation 页面、API client、权限和组件风格。
- [ ] 实现 API client 的错误和 cursor 分页处理。
- [ ] 实现实验概览、Rollout 列表、Metric/Failure/Trace 查看。
- [ ] 显式区分 `not_measured`、`running`、`failed`、`cancelled`、`finished`。
- [ ] 加入响应式布局、键盘可达、图标 tooltip 和长文本截断/展开。
- [ ] 执行前端单测、Lint、Build 和关键查看流程验证。

## 7. 验证命令和预期结果

```bash
docker compose exec web pnpm test:unit
docker compose exec web pnpm run lint
docker compose exec web pnpm run build
git diff --check -- tasks/2026-08-28/T7-readonly-ui.md
```

有 Playwright 配置时补充：

```bash
docker compose exec web pnpm exec playwright test --grep evaluation
```

预期：管理员可完成“选择实验 -> 查看概览 -> 打开 rollout -> 查看 Trace/Failure”的只读路径；空值不伪造数字；403/404/网络错误可理解；无按钮触发真实执行；Lint 和 Build 通过。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 盘点现有 `/evaluation` 页面 | 确认当前使用硬编码 Fixture |
| 待 T6 | 稳定 API 响应和权限 | 未开始 |
| 待 T7 实施 | API 接入、状态/Trace 查看和前端验证 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,300p' tasks/2026-08-28/T7-readonly-ui.md
sed -n '1,260p' web/src/views/EvaluationPlatformView.vue
rg -n "evaluation|lucide|base.css|pnpm" web/src package.json
```

若 API 字段或权限尚未稳定，保留页面契约文档，暂停真实数据接入，不在前端猜测后端状态或增加写操作。

## 10. 完成条件

- `/evaluation` 能使用同一套 T6 API 只读展示三类来源的实验、rollout、指标、失败和 trace。
- loading/empty/error/forbidden/not-found/partial-trace 状态可见且无敏感泄露。
- 前端单测、Lint、Build，以及可用时的 Playwright 关键路径通过。
- 总领文件记录页面验证证据并进入 T8 收口。

## 11. 实施结果（2026-08-28）

已完成 `/evaluation` 平台级只读页面：

- `web/src/apis/evaluation_api.js` 封装平台资源 API、limit/cursor、Trace 和失败查询。
- `EvaluationPlatformView.vue` 正常优先加载平台 API；按 Agent/Tool/MCP、RAG、Graph 来源筛选，展示数据集、实验状态、pass@1/pass@k/pass^k/best@k、逐题 rollout、失败原因和展开加载的脱敏 Trace。
- API 不可用、空数据、部分 Trace 缺失时显示明确状态，并保留标明“Fixture 回退”的本地样例；页面没有创建、运行、取消、重试或真实工具操作按钮。
- 路由继续要求管理员，页面不直接访问 Langfuse、Redis 或数据库。

验证：`docker compose exec web pnpm run lint` 通过；`docker compose exec web pnpm run build` 成功。当前未配置 Playwright 评测专用用例，后续可在非 LITE 管理员环境补关键路径截图/交互回归。

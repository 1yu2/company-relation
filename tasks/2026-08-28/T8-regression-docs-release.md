# T8：回归、文档与交付收口

关联总领文件：[task.md](./task.md)。本子任务只有在 T0-T7 的实际实现和验证结果齐备后执行，负责整理证据、补文档和确认发布边界。

## 1. 目标

在 Docker Compose 环境中完成平台级统一评测闭环验收、三类来源适配回归、前端页面关键路径、敏感信息检查、Lint/Build 和开发文档收口，形成可交付的变更记录与中断恢复信息。

## 2. 已确认事项

- 交付目标是平台级统一评测闭环；首个执行器使用 Fixture，不包含真实 Agent/Tool/MCP 回归。
- 旧知识库、企业关系和 Agent Eval 入口必须保持兼容。
- 必须按项目规范更新 `docs/develop-guides/changelog.md`；正式用户文档如有新增才同步 VitePress 导航。
- 测试、Lint 和调试在 Docker 容器中执行；敏感值只能用于本地命令，不写入输出或文档。
- `task.md` 和各子任务文档是本次需求和验证的事实记录。

## 3. 剩余疑问

1. T1 最终是否采用 Semantica 可选适配器，以及版本/许可证证据。
2. T6 是否开放内部 run/cancel API，还是仅保留查询 API。
3. 真实业务样例何时替换通用 Fixture，是否需要另建任务目录。
4. 发布环境是否允许启用 Langfuse 关联和 Trace 保留策略。

## 4. 明确不包含

- 不在收口阶段顺手修复无关业务改动、删除用户已有工作区变更或重写旧评测链路。
- 不把测试环境成功结果宣称为真实模型质量或生产 SLA。
- 不新增未在 T0-T7 记录的功能、依赖、数据库或 Docker 服务。
- 不把网络不可用造成的 Semantica 未核验写成已验证通过。

## 5. 具体文件、验证产物与发布变更

预期维护文件：

- `tasks/2026-08-28/task.md`：最终状态、检查点、命令和残余风险。
- `tasks/2026-08-28/T0-*.md` 至 `T7-*.md`：各自实际改动和测试结果。
- `docs/develop-guides/changelog.md`：面向开发者的变更摘要。
- 必要时 `docs/` 正式文档及 `docs/.vitepress/config.mts` 导航。

平台级统一闭环验收证据必须包括：

1. 数据集版本发布 checksum，以及 Agent/Tool/MCP、RAG、Graph 三类来源至少各一条可查询记录。
2. 成功、失败、超时/取消各一条 Rollout 及 Trace/Failure 查询结果。
3. 重复 job 的幂等证明和 Worker/DB 重启恢复结果。
4. `pass@1/pass@k/pass^k/best@k` 的逐题输入和聚合输出。
5. 内部线程隔离、管理员权限和旧入口回归结果。

## 6. 任务清单

- [ ] 检查 T0-T7 完成条件、实际文件和总领状态一致。
- [ ] 在 Docker 中执行后端 Unit、Integration、必要的 E2E、Lint/format。
- [ ] 执行前端 Unit、Lint、Build 和可用的 Playwright 关键流程。
- [ ] 运行统一 Fixture 验收矩阵并保存命令、时间、结果和失败样例。
- [ ] 执行敏感信息、凭据、未脱敏 Trace 和依赖变更扫描。
- [ ] 更新 changelog/正式文档（如适用），检查导航和链接。
- [ ] 在总领文件记录发布/不发布结论、残余风险和下一任务入口。

## 7. 验证命令和预期结果

```bash
docker compose ps
docker compose exec api pytest backend/test/unit -q
docker compose exec api pytest backend/test/integration -q
docker compose exec web pnpm test:unit
docker compose exec web pnpm run lint
docker compose exec web pnpm run build
make format --check
git diff --check
```

按项目现有脚本补充 E2E；对敏感内容执行仓库约定的扫描命令。预期：相关测试、Lint、Build 全通过；失败必须归类为既有基线、环境阻塞或本次回归，并写入总领文件，不能只报“通过/失败”。

平台级统一闭环的最小验收序列：导入并发布版本 -> 创建实验 -> 成功/失败/超时或取消 -> 查询三类来源的 rollout/trace/metrics/failures -> 重复投递 -> 重启恢复 -> 前端页面查看 -> 旧入口回归。

## 8. 当前进度时间表

| 时间 | 事项 | 结果 |
| --- | --- | --- |
| 2026-08-28 | 创建总领、研究附录和 T0-T8 任务文档 | 文档阶段完成 |
| 待 T1-T7 | 逐项实施和测试 | 未开始 |
| 待 T8 | Docker 全量验证、changelog 和交付记录 | 未开始 |

## 9. 中断恢复入口

```bash
cd /Users/fishyuu/orca/company-relation
sed -n '1,430p' tasks/2026-08-28/task.md
git status --short
docker compose ps
docker logs api-dev --tail 100
docker logs web-dev --tail 100
```

从总领文件的 `checkpoint` 和最后一条进度记录继续；先重跑最后一次失败命令，再决定是否修改。若是外部网络、Docker 或依赖环境阻塞，记录环境证据并保留业务代码状态，不删除用户已有改动。

## 10. 完成条件

- T0-T7 的完成条件、实际改动和验证结果全部回写总领文件。
- Docker 内相关 Unit/Integration/E2E/Lint/Build 通过，或每个未通过项有明确阻塞证据和后续任务。
- 三类来源的统一 Fixture/适配闭环、前端查看页面和旧入口回归有可复核记录，敏感信息扫描无新增泄露。
- changelog/正式文档与实际行为一致；最终 checkpoint、残余风险和中断恢复入口已更新。

## 11. 实施结果（2026-08-28，进行中）

已完成 T1-T7 的代码和分层验证，正在收口文档：

- 评测 unit：13 passed；新增 API 集成测试在 `LITE_MODE=1` 下按路由不注册策略跳过。
- 新增后端评测模块 Ruff：通过；前端 ESLint：通过；Vite production build：成功（仅有既有 chunk/依赖注释警告）。
- PostgreSQL Fixture 探针：`FINISHED`，2 tasks x 3 attempts，24 metrics，6 failures；重复运行不增加 rollout，聚合值可重算。
- 全量 unit 使用 `--import-mode=importlib`：1237 passed、2 skipped、15 failed；失败集中在已有 Agent/Voice/OCR/Worker 测试的环境导入或工作区并行改动，评测模块无失败。默认 import 模式另有既有同名 `test_metrics.py` 收集冲突。

残余风险：首轮 Runner 仍为同步 Fixture，不代表真实 Agent 质量或生产 SLA；Semantica 外部元数据因 DNS/API 不可用未核验；非 LITE 管理员 API/Playwright 尚需部署级验证。以上不会被标记为本轮已通过能力。

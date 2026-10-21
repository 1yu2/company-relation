#!/usr/bin/env bash

set -euo pipefail

# 不修改源码的门禁：不执行 format --fix；docker 模式会刷新 web 容器内的测试副本。
# VERIFY_MODE=docker（默认）：在本地 Compose 的 api/web 容器中执行，供 `make verify` 使用。
# VERIFY_MODE=native：直接在 backend/、web/ 目录执行，供 CI 使用；需要事先安装好依赖。
# 两种模式运行同一份检查清单，CI 与本地门禁不会各自漂移。
mode="${VERIFY_MODE:-docker}"
root="$(cd "$(dirname "$0")/.." && pwd)"

api() {
  if [ "$mode" = native ]; then (cd "$root/backend" && "$@"); else docker compose exec -T api "$@"; fi
}

web() {
  if [ "$mode" = native ]; then (cd "$root/web" && "$@"); else docker compose exec -T web "$@"; fi
}

if [ "$mode" = docker ]; then
  # web 容器只挂载 src，镜像里的 test 目录是构建时的旧副本；先清空再完整复制当前测试，
  # 避免旧副本或其他分支留下的测试文件混入本次检查。
  docker compose exec -T web rm -rf /app/test
  docker compose cp "$root/web/test" web:/app/test >/dev/null
fi

api uv run --group test pytest --import-mode=importlib \
  test/unit/company_due_diligence \
  test/unit/evaluation \
  test/unit/storage \
  test/unit/routers/test_system_router.py \
  test/unit/services/test_run_worker.py \
  test/unit/services/test_chat_service_sync.py \
  test/unit/services/test_conversation_queue_history.py \
  test/unit/backends/test_execute_status.py \
  test/unit/toolkits/test_kbs_tools.py \
  test/unit/services/test_run_submission_service.py \
  test/unit/services/test_agent_run_service.py \
  test/unit/backends/test_sandbox_backends.py \
  test/unit/services/test_skill_builtin_install_concurrency.py \
  -m "not slow"
api uv run --group dev ruff check server package
api uv run --group dev ruff format --check \
  package/yuxi/company_due_diligence/normalization.py \
  package/yuxi/company_due_diligence/models.py \
  package/yuxi/company_due_diligence/repositories/shareholder.py \
  package/yuxi/company_due_diligence/structured_query_service.py \
  package/yuxi/company_due_diligence/entity_mentions.py \
  package/yuxi/company_due_diligence/qa/graph.py \
  package/yuxi/company_due_diligence/qa/service.py \
  package/yuxi/agents/toolkits/kbs/tools.py \
  test/unit/company_due_diligence/test_normalization.py \
  test/unit/company_due_diligence/test_entity_mentions.py \
  test/unit/company_due_diligence/test_structured_query_service.py \
  test/unit/company_due_diligence/test_structured_repositories.py \
  test/unit/company_due_diligence/qa/test_qa_service.py \
  test/unit/company_due_diligence/qa/test_qa_workflow.py \
  test/unit/toolkits/test_kbs_tools.py \
  package/yuxi/agents/backends/composite.py \
  package/yuxi/services/conversation_service.py \
  test/unit/backends/test_execute_status.py \
  test/unit/services/test_chat_service_sync.py \
  test/unit/services/test_conversation_queue_history.py \
  test/unit/services/test_skill_builtin_install_concurrency.py \
  package/yuxi/agents/backends/sandbox/provider.py \
  package/yuxi/company_due_diligence/evidence/service.py \
  package/yuxi/company_due_diligence/graph/service.py \
  package/yuxi/company_relation/evaluation.py \
  package/yuxi/evaluation/agent_final_answer.py \
  package/yuxi/evaluation/fault_recovery.py \
  test/unit/evaluation/test_fault_recovery.py \
  package/yuxi/evaluation/rag_answer.py \
  test/unit/evaluation/test_rag_answer.py \
  package/yuxi/evaluation/llm_judge.py \
  test/unit/evaluation/test_llm_judge.py \
  package/yuxi/evaluation/online_sampling.py \
  test/unit/evaluation/test_online_sampling.py \
  package/yuxi/evaluation/agent_runner.py \
  package/yuxi/evaluation/experiment_service.py \
  package/yuxi/evaluation/snapshot.py \
  package/yuxi/repositories/platform_evaluation_repository.py \
  package/yuxi/services/agent_run_service.py \
  package/yuxi/services/chat_service.py \
  package/yuxi/services/run_queue_service.py \
  package/yuxi/services/run_worker.py \
  package/yuxi/services/task_service.py \
  package/yuxi/storage/postgres/manager.py \
  package/yuxi/storage/postgres/migrations.py \
  server/routers/evaluation_platform_router.py \
  server/routers/system_router.py \
  server/utils/lifespan.py
web pnpm test:unit
web pnpm exec prettier --check \
  test/unit/evaluationView.test.js \
  src/components/evaluation/EvaluationFailurePool.vue
web pnpm exec eslint src
web pnpm run build

# CI 的 checkout 是干净的，只检查工作区等于什么都没查；PR 上改为检查本次提交相对 base 分支的改动。
if [ "$mode" = native ] && [ -n "${GITHUB_BASE_REF:-}" ]; then
  git -C "$root" diff --check "origin/${GITHUB_BASE_REF}...HEAD"
else
  git -C "$root" diff --check
fi

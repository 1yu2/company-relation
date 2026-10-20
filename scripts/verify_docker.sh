#!/usr/bin/env bash

set -euo pipefail

# 只读门禁：不执行 format --fix、不修改缓存或源码。
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
  test/unit/services/test_run_submission_service.py \
  test/unit/services/test_agent_run_service.py \
  test/unit/backends/test_sandbox_backends.py \
  -m "not slow"
api uv run --group dev ruff check server package
api uv run --group dev ruff format --check \
  package/yuxi/company_due_diligence/evidence/service.py \
  package/yuxi/company_due_diligence/graph/service.py \
  package/yuxi/company_relation/evaluation.py \
  package/yuxi/services/run_queue_service.py \
  package/yuxi/services/run_worker.py \
  package/yuxi/services/task_service.py \
  package/yuxi/storage/postgres/manager.py \
  package/yuxi/storage/postgres/migrations.py \
  server/routers/system_router.py \
  server/utils/lifespan.py
web pnpm test:unit
web pnpm exec eslint src
web pnpm run build

git -C "$root" diff --check

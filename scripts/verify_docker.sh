#!/usr/bin/env bash

set -euo pipefail

# 只读门禁：不执行 format --fix、不修改缓存或源码。
api=(docker compose exec -T api)
web=(docker compose exec -T web)

"${api[@]}" uv run --group test pytest --import-mode=importlib \
  test/unit/company_due_diligence \
  test/unit/evaluation \
  test/unit/storage \
  test/unit/routers/test_system_router.py \
  test/unit/services/test_run_worker.py \
  -m "not slow"
"${api[@]}" uv run --group dev ruff check server package
"${api[@]}" uv run --group dev ruff format --check \
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
"${web[@]}" pnpm test:unit
"${web[@]}" pnpm exec eslint src
"${web[@]}" pnpm run build

git diff --check

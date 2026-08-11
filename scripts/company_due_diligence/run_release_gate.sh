#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENFORCE_FLAG=()
case "${1:-}" in
  "") ;;
  --enforce) ENFORCE_FLAG=(--enforce) ;;
  *)
    echo "用法: $0 [--enforce]" >&2
    exit 2
    ;;
esac

COMPOSE_ARGS=(-f docker-compose.yml)
if [[ -n "${COMPANY_DD_COMPOSE_OVERRIDE:-}" ]]; then
  COMPOSE_ARGS+=(-f "$COMPANY_DD_COMPOSE_OVERRIDE")
fi

compose() {
  docker compose "${COMPOSE_ARGS[@]}" "$@"
}

test_compose() {
  compose run --rm --no-deps -T \
    -e API_BASE_URL=http://api:5050 \
    -e TEST_BASE_URL=http://api:5050 \
    api "$@"
}

compose exec -T api uv run --no-sync pytest \
  test/unit/company_due_diligence \
  test/unit/routers/test_company_due_diligence_router.py \
  test/unit/routers/test_company_due_diligence_evaluation_router.py -q

compose exec -T api uv run --no-sync pytest test/integration/company_due_diligence -q

ADMIN_E2E_FLAG=()
if test_compose sh -lc 'test -n "${E2E_USERNAME:-${TEST_USERNAME:-}}" && test -n "${E2E_PASSWORD:-${TEST_PASSWORD:-}}"'; then
  ADMIN_E2E_FLAG=(--admin-e2e-passed)
fi
test_compose uv run --no-sync pytest test/e2e/company_due_diligence -m e2e -q

AKSHARE_RUN_DIR="saves/company_due_diligence/release/akshare/$(date -u +%Y%m%dT%H%M%SZ)-$$"
compose exec -T api uv run --no-sync python scripts/company_relation/run_evaluation.py \
  --skip-import \
  --output-dir "$AKSHARE_RUN_DIR"
AKSHARE_REPORT="$(
  compose exec -T api sh -lc \
    "find '$AKSHARE_RUN_DIR' -maxdepth 1 -type f -name 'company-relation-*.json' -print -quit"
)"
if [[ -z "$AKSHARE_REPORT" ]]; then
  echo "AkShare 回归报告未生成: $AKSHARE_RUN_DIR" >&2
  exit 1
fi

python3 backend/scripts/company_due_diligence/audit_sensitive_content.py
compose exec -T api uv run --no-sync python scripts/company_due_diligence/audit_sensitive_content.py
test_compose uv run --no-sync python scripts/company_due_diligence/generate_release_gate.py \
  --akshare-report "$AKSHARE_REPORT" \
  "${ADMIN_E2E_FLAG[@]}" \
  "${ENFORCE_FLAG[@]}"

compose run --rm --no-deps -v "$ROOT_DIR/web/test:/app/test" web sh -lc \
  'pnpm test:unit && pnpm exec eslint src test && pnpm build'

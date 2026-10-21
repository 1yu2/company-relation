
.PHONY: up up-lite down logs lint format seed reset verify eval-gate

PYTEST_ARGS ?=
BACKEND_PYTHON ?= $(shell cat backend/.python-version)

up:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose up -d

down:
	docker compose down

reset:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	docker compose down
	rm -rf docker/volumes
	docker compose up -d
	@echo "Waiting for api to be ready..."
	@until docker compose exec -T api true >/dev/null 2>&1; do sleep 2; done
	$(MAKE) seed

up-lite:
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Please create it from .env.template"; \
		exit 1; \
	fi
	LITE_MODE=true docker compose up -d postgres redis minio sandbox-provisioner api worker web

logs:
	@docker logs --tail=50 api-dev
	@echo "\n\nBranch: $$(git branch --show-current)"
	@echo "Commit ID: $$(git rev-parse HEAD)"
	@echo "System: $$(uname -a)"

seed:
	docker compose exec api uv run python scripts/seed_initial_users.py

######################
# LINTING AND FORMATTING
######################

verify:
	./scripts/verify_docker.sh

# 回归门禁：比较两个已完成的实验，任一规则失败时退出码为 1。用法：make eval-gate BASE=<基线实验 ID> CANDIDATE=<候选实验 ID>
eval-gate:
	docker compose exec -T api uv run python -m yuxi.evaluation.regression_gate $(BASE) $(CANDIDATE)

format:
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff format package
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check package --fix
	cd backend && UV_PYTHON=$(BACKEND_PYTHON) uv run ruff check --select I package --fix
	cd web && pnpm run format
	cd web && pnpm run lint

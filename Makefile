.PHONY: dev down logs test lint eval eval-fast

ENV_FILE ?= .env
EVAL_IMAGE ?= dss-eval
EVAL_TAG ?= latest
DRY_RUN ?= 0

# Build and start the full local stack.
dev:
	docker compose --env-file $(ENV_FILE) up --build -d

# Stop the local stack.
down:
	docker compose --env-file $(ENV_FILE) down

# Follow logs from all services.
logs:
	docker compose --env-file $(ENV_FILE) logs -f

# Run test suites inside each app container.
# These are placeholders until each app provides a test target.
test:
	docker compose --env-file $(ENV_FILE) exec backend pytest -q || true
	docker compose --env-file $(ENV_FILE) exec middleware pytest -q || true
	docker compose --env-file $(ENV_FILE) exec control-plane pytest -q || true
	docker compose --env-file $(ENV_FILE) exec chat-surface pytest -q || true

# Lint placeholder.
lint:
	@echo "Linting is not yet configured. Add ruff/mypy/pyright steps per app."

# Build and run the full v0.5 benchmark suite in a fresh container.
# Set DRY_RUN=1 (or use `make eval-fast`) for a deterministic CI smoke run.
eval:
	docker build -t $(EVAL_IMAGE):$(EVAL_TAG) -f eval/Dockerfile .
	docker run --rm \
		-v $(PWD)/eval/reports/benchmarks:/app/eval/reports/benchmarks \
		-e DRY_RUN=$(DRY_RUN) \
		-e SKIP_REAL_EMBEDDING=$(if $(filter 1,$(DRY_RUN)),1,0) \
		-e DSS_REFRESH_TOKEN=$(DSS_REFRESH_TOKEN) \
		-e OPENROUTER_API_KEY=$(OPENROUTER_API_KEY) \
		$(EVAL_IMAGE):$(EVAL_TAG)

# Fast deterministic smoke run (no model downloads, tiny corpora).
eval-fast:
	$(MAKE) eval DRY_RUN=1

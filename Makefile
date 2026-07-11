.PHONY: dev down logs test lint

ENV_FILE ?= .env

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

APP ?= ds-backend-new
FLY ?= fly
VOLUME ?= ledger_volume
VOLUME_ID ?=
REGION ?= syd
APP_HOST ?= https://$(APP).fly.dev
API_HOST ?= $(APP_HOST)
ADMIN_TOKEN ?=
ENTITY ?= chat-demo-session
ENTRY_ID ?= chat-demo-session:WX-EXAMPLE
CHAT_PROVIDER ?= openrouter
CHAT_MESSAGE ?= Hello from Makefile

PYTHON ?= python3
BACKEND_PORT ?= 8080
BACKEND_HOST ?= 0.0.0.0

.PHONY: help ensure-local-deps launch-local kill-local-port fly-status fly-logs fly-restart fly-deploy fly-ssh fly-secrets-list fly-secrets-set \
	fly-soft-reset fly-hard-reset api-health api-chat api-chat-stream api-ledger-history \
	api-ledger-read api-ledger-pin api-ledger-unpin api-web4-decode api-admin-reindex rollout-contract-check auth-compat-matrix

help:
	@echo "Local run helpers:"
	@echo "  make launch-local      # Run backend locally on BACKEND_PORT (default 8080)"
	@echo "  make ensure-local-deps # Create venv and install backend requirements"
	@echo "  make kill-local-port   # Free BACKEND_PORT if occupied"
	@echo "Fly admin helpers:"
	@echo "  make fly-status        # Show app status"
	@echo "  make fly-logs           # Tail app logs"
	@echo "  make fly-restart        # Restart the app"
	@echo "  make fly-deploy         # Deploy current branch"
	@echo "  make fly-ssh            # Open SSH console"
	@echo "  make fly-secrets-list   # List secrets"
	@echo "  make fly-secrets-set    # Set a secret: make fly-secrets-set KEY=... VALUE=..."
	@echo "  make fly-soft-reset     # Clear ledger via admin route (requires ADMIN_TOKEN)"
	@echo "  make fly-hard-reset     # Destroy + recreate volume (requires CONFIRM=1)"
	@echo "  make api-health         # GET /health"
	@echo "  make api-chat           # POST /chat"
	@echo "  make api-chat-stream    # POST /chat/stream (ndjson stream)"
	@echo "  make api-ledger-history # GET /ledger/chat_history"
	@echo "  make api-ledger-read    # GET /ledger/read/{entry_id}"
	@echo "  make api-ledger-pin     # POST /ledger/pin/{entry_id}"
	@echo "  make api-ledger-unpin   # POST /ledger/unpin/{entry_id}"
	@echo "  make api-web4-decode    # POST /web4/decode"
	@echo "  make api-admin-reindex  # GET /admin/reindex (requires ADMIN_TOKEN)"
	@echo "  make rollout-contract-check # Verify backend/middleware/frontend deployed contract"
	@echo "  make auth-compat-matrix # Run auth/context curl matrix against deployed backend"

fly-status:
	$(FLY) status -a $(APP)

fly-logs:
	$(FLY) logs -a $(APP)

fly-restart:
	$(FLY) apps restart $(APP)

fly-deploy:
	@if [ -n "$(GIT_SHA)" ]; then \
		$(FLY) secrets set -a $(APP) GIT_SHA=$(GIT_SHA); \
		$(FLY) deploy -a $(APP) --build-arg GIT_SHA=$(GIT_SHA); \
	else \
		$(FLY) deploy -a $(APP); \
	fi

fly-ssh:
	$(FLY) ssh console -a $(APP)

fly-secrets-list:
	$(FLY) secrets list -a $(APP)

fly-secrets-set:
	@if [ -z "$(KEY)" ] || [ -z "$(VALUE)" ]; then \
		echo "Usage: make fly-secrets-set KEY=... VALUE=..."; \
		exit 1; \
	fi
	$(FLY) secrets set -a $(APP) $(KEY)=$(VALUE)

fly-soft-reset:
	@if [ -z "$(ADMIN_TOKEN)" ]; then \
		echo "Usage: make fly-soft-reset ADMIN_TOKEN=... [APP_HOST=https://app.fly.dev] [ENTITY=chat-abc] [REINDEX=true]"; \
		exit 1; \
	fi
	@if [ -n "$(ENTITY)" ]; then \
		curl -sS -X POST "$(APP_HOST)/admin/clear_ledger?entity=$(ENTITY)&confirm=true&reindex=$(REINDEX)" \
			-H "x-admin-token: $(ADMIN_TOKEN)"; \
	else \
		curl -sS -X POST "$(APP_HOST)/admin/clear_ledger?confirm=true" \
			-H "x-admin-token: $(ADMIN_TOKEN)"; \
	fi

fly-hard-reset:
	@if [ "$(CONFIRM)" != "1" ]; then \
		echo "Refusing destructive reset. Re-run with CONFIRM=1 to proceed."; \
		exit 1; \
	fi
	$(FLY) volumes list -a $(APP)
	@VOL_ID="$(VOLUME_ID)"; \
	VM_ID=""; \
	if [ -z "$$VOL_ID" ]; then \
		VOL_ID=`$(FLY) volumes list -a $(APP) | awk 'NR>1 && $$3 == "$(VOLUME)" {print $$1; exit}'`; \
	fi; \
	if [ -z "$$VOL_ID" ]; then \
		echo "Usage: make fly-hard-reset CONFIRM=1 [VOLUME_ID=vol_...] [VOLUME=ledger_volume] [REGION=syd]"; \
		echo "Could not resolve volume ID for name $(VOLUME)."; \
		exit 1; \
	fi; \
	VM_ID=`$(FLY) volumes list -a $(APP) | awk -v vol="$$VOL_ID" 'NR>1 && $$1 == vol {print $$8; exit}'`; \
	if [ -z "$$VM_ID" ]; then \
		VM_ID=`$(FLY) volumes list -a $(APP) | awk -v name="$(VOLUME)" 'NR>1 && $$3 == name {print $$8; exit}'`; \
	fi; \
	if [ -n "$$VM_ID" ] && [ "$$VM_ID" != "-" ]; then \
		echo "Stopping machine $$VM_ID bound to volume $$VOL_ID"; \
		$(FLY) machine stop $$VM_ID -a $(APP) || true; \
		ATTEMPT=1; \
		while [ $$ATTEMPT -le 30 ]; do \
			STATE=`$(FLY) machine list -a $(APP) | awk -v vm="$$VM_ID" 'NR>1 && $$1 == vm {print tolower($$2); exit}'`; \
			if [ -z "$$STATE" ] || [ "$$STATE" = "stopped" ] || [ "$$STATE" = "suspended" ] || [ "$$STATE" = "destroyed" ]; then \
				break; \
			fi; \
			sleep 2; \
			ATTEMPT=`expr $$ATTEMPT + 1`; \
		done; \
		echo "Destroying machine $$VM_ID bound to volume $$VOL_ID"; \
		$(FLY) machine destroy $$VM_ID -a $(APP) --force || true; \
	fi; \
	echo "Destroying volume $$VOL_ID (name $(VOLUME))"; \
	$(FLY) volumes destroy $$VOL_ID -a $(APP)
	$(FLY) volumes create $(VOLUME) --app $(APP) --size 1 --region $(REGION)
	$(FLY) deploy -a $(APP)

api-health:
	curl -sS "$(API_HOST)/health"

api-chat:
	curl -sS -X POST "$(API_HOST)/chat" \
		-H "Content-Type: application/json" \
		-d "{\"session_id\":\"$(ENTITY)\",\"message\":\"$(CHAT_MESSAGE)\",\"history\":[],\"provider\":\"$(CHAT_PROVIDER)\",\"enable_ledger\":true}"

api-chat-stream:
	curl -N -sS -X POST "$(API_HOST)/chat/stream" \
		-H "Content-Type: application/json" \
		-d "{\"session_id\":\"$(ENTITY)\",\"message\":\"$(CHAT_MESSAGE)\",\"history\":[],\"provider\":\"$(CHAT_PROVIDER)\",\"enable_ledger\":true}"

api-ledger-history:
	curl -sS "$(API_HOST)/ledger/chat_history?entity=$(ENTITY)&limit=50"

api-ledger-read:
	curl -sS "$(API_HOST)/ledger/read/$(ENTRY_ID)"

api-ledger-pin:
	curl -sS -X POST "$(API_HOST)/ledger/pin/$(ENTRY_ID)"

api-ledger-unpin:
	curl -sS -X POST "$(API_HOST)/ledger/unpin/$(ENTRY_ID)"

api-web4-decode:
	curl -sS -X POST "$(API_HOST)/web4/decode" \
		-H "Content-Type: application/json" \
		-d "{\"coordinate\":\"$(ENTRY_ID)\"}"

api-admin-reindex:
	@if [ -z "$(ADMIN_TOKEN)" ]; then \
		echo "Usage: make api-admin-reindex ADMIN_TOKEN=... [ENTITY=chat-abc]"; \
		exit 1; \
	fi
	curl -sS "$(API_HOST)/admin/reindex?entity=$(ENTITY)" \
		-H "x-admin-token: $(ADMIN_TOKEN)"

rollout-contract-check:
	@$(PYTHON) backend/scripts/rollout_contract_check.py \
		--backend-url "https://ds-backend-new.fly.dev" \
		--middleware-url "https://ds-middleware-new.fly.dev" \
		--frontend-url "https://ds-frontend-local-new.vercel.app" \
		$(if $(INSECURE),--insecure,) \
		$(if $(GIT_SHA),--expected-backend-sha "$(GIT_SHA)",)

auth-compat-matrix:
	@BACKEND_URL="$(if $(BACKEND_URL),$(BACKEND_URL),https://ds-backend-new.fly.dev)" \
		backend/utils/ref/scripts/auth_compat_matrix.sh
ensure-local-deps:
	@echo "Ensuring backend Python environment dependencies..."
	@if [ ! -x ./.venv/bin/python ]; then \
		echo "Creating backend virtual environment at ./.venv..."; \
		$(PYTHON) -m venv .venv; \
	fi
	@./.venv/bin/python -m pip install --upgrade pip >/dev/null
	@./.venv/bin/python -m pip install -r requirements.txt

launch-local: ensure-local-deps
	@echo "Launching backend locally on $(BACKEND_HOST):$(BACKEND_PORT)..."
	@make kill-local-port BACKEND_PORT=$(BACKEND_PORT)
	@set -a; \
	if [ -f ./.env ]; then . ./.env; fi; \
	if [ -f ./.env.local ]; then . ./.env.local; fi; \
	set +a; \
	PYTHON_CMD="$(PYTHON)"; \
	if [ -x ./.venv/bin/python ]; then PYTHON_CMD="./.venv/bin/python"; fi; \
	DB_PATH=$${DB_PATH:-./data} PORT=$(BACKEND_PORT) $$PYTHON_CMD -m uvicorn backend.main:app --reload --host $(BACKEND_HOST) --port $(BACKEND_PORT)

kill-local-port:
	@echo "Releasing backend port $(BACKEND_PORT)..."
	@PIDS=$$(lsof -ti tcp:$(BACKEND_PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No process found listening on port $(BACKEND_PORT)."; \
	fi

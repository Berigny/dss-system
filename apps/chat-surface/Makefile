.DEFAULT_GOAL := install

# Variables
PYTHON = python3
VENV_DIR ?= .venv
PORT ?= 5050
MIDDLEWARE_DIR ?= /Users/davidberigny/Documents/GitHub/ds-middleware-local
MIDDLEWARE_PORT ?= 5001
BACKEND_DIR ?= /Users/davidberigny/Documents/GitHub/ds-backend-local
BACKEND_PORT ?= 8080
API_BASE ?= http://127.0.0.1:$(MIDDLEWARE_PORT)
OPENCLAW ?= openclaw
OPENCLAW_GATEWAY_PORT ?= 18789
ENABLE_OPENCLAW_BOOT ?= 0
SKIP_INSTALL ?= 0

.PHONY: install launch launch-ui daemon run-stack clean kill-port kill-all launch-all openclaw-gateway-url doctor-stack openclaw-desktop mcp-url

install:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "Creating virtual environment at $(VENV_DIR)..."; \
		$(PYTHON) -m venv $(VENV_DIR); \
	fi
	@if [ "$(SKIP_INSTALL)" = "1" ]; then \
		echo "Skipping dependency install (SKIP_INSTALL=1)."; \
	else \
		echo "Installing dependencies..."; \
		$(VENV_DIR)/bin/pip install -r requirements.txt; \
	fi

launch: install
	@echo "Launching application on port $(PORT)..."
	@make kill-port
	API_BASE=$(API_BASE) $(VENV_DIR)/bin/python -m uvicorn app:app --port $(PORT)

kill-port:
	@echo "Releasing port $(PORT)..."
	@PIDS=$$(lsof -ti tcp:$(PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing processes $$PIDS"; \
		kill $$PIDS >/dev/null 2>&1 || true; \
	else \
		echo "No process found listening on port $(PORT)."; \
	fi

kill-all:
	@echo "Releasing UI port $(PORT)..."
	@PIDS=$$(lsof -ti tcp:$(PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing UI processes $$PIDS"; \
		kill $$PIDS >/dev/null 2>&1 || true; \
	else \
		echo "No process found listening on UI port $(PORT)."; \
	fi
	@echo "Releasing middleware port $(MIDDLEWARE_PORT)..."
	@PIDS=$$(lsof -ti tcp:$(MIDDLEWARE_PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing middleware processes $$PIDS"; \
		kill $$PIDS >/dev/null 2>&1 || true; \
	else \
		echo "No process found listening on middleware port $(MIDDLEWARE_PORT)."; \
	fi
	@echo "Releasing backend port $(BACKEND_PORT)..."
	@PIDS=$$(lsof -ti tcp:$(BACKEND_PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing backend processes $$PIDS"; \
		kill $$PIDS >/dev/null 2>&1 || true; \
	else \
		echo "No process found listening on backend port $(BACKEND_PORT)."; \
	fi
	@echo "Stopping sync daemon..."
	@PIDS=$$(pgrep -f "[s]ync_daemon.py" || true); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing sync daemon processes $$PIDS"; \
		kill $$PIDS >/dev/null 2>&1 || true; \
	else \
		echo "No sync daemon process found."; \
	fi
	@if [ "$(ENABLE_OPENCLAW_BOOT)" = "1" ]; then \
		echo "Stopping OpenClaw gateway..."; \
		$(OPENCLAW) gateway stop >/dev/null 2>&1 || true; \
		echo "Releasing OpenClaw gateway port $(OPENCLAW_GATEWAY_PORT)..."; \
		PIDS=$$(lsof -ti tcp:$(OPENCLAW_GATEWAY_PORT)); \
		if [ -n "$$PIDS" ]; then \
			echo "Killing OpenClaw gateway processes $$PIDS"; \
			kill $$PIDS >/dev/null 2>&1 || true; \
		else \
			echo "No process found listening on OpenClaw gateway port $(OPENCLAW_GATEWAY_PORT)."; \
		fi; \
		if [ -f .openclaw.gateway.pid ]; then \
			GW_PID=$$(cat .openclaw.gateway.pid 2>/dev/null || true); \
			if [ -n "$$GW_PID" ] && kill -0 "$$GW_PID" 2>/dev/null; then \
				echo "Killing tracked OpenClaw gateway PID $$GW_PID"; \
				kill "$$GW_PID" 2>/dev/null || true; \
			fi; \
			rm -f .openclaw.gateway.pid; \
		fi; \
	else \
		echo "Skipping OpenClaw shutdown (ENABLE_OPENCLAW_BOOT=$(ENABLE_OPENCLAW_BOOT))."; \
	fi
	@echo "Invoking middleware stack cleanup (includes ngrok/MCP artifacts)..."
	@$(MAKE) --no-print-directory -C "$(MIDDLEWARE_DIR)" kill-all PORT=$(MIDDLEWARE_PORT) BACKEND_PORT=$(BACKEND_PORT) SKIP_OPENCLAW_GATEWAY=1 || true

clean:
	@echo "Cleaning up..."
	rm -rf $(VENV_DIR)
	rm -f .sesskey
	rm -rf __pycache__

launch-ui: launch

daemon: install
	@echo "Launching sync daemon..."
	LOCAL_API=$(API_BASE) $(VENV_DIR)/bin/python sync_daemon.py

mcp-url:
	@$(MAKE) --no-print-directory -C "$(MIDDLEWARE_DIR)" mcp-url

run-stack: install
	@echo "Starting UI and daemon in parallel..."
	@make kill-port PORT=$(PORT)
	@(API_BASE=$(API_BASE) $(VENV_DIR)/bin/python -m uvicorn app:app --port $(PORT) &) && LOCAL_API=$(API_BASE) $(VENV_DIR)/bin/python sync_daemon.py

openclaw-gateway-url:
	@$(OPENCLAW) gateway status --json 2>/dev/null | $(PYTHON) -c 'import json, sys; data = json.load(sys.stdin); gateway = (data.get("gateway") or {}).get("probeUrl"); rpc = (data.get("rpc") or {}).get("url"); print(gateway or rpc or "")'

openclaw-desktop:
	@echo "Configuring OpenClaw desktop access..."
	@$(OPENCLAW) gateway install >/dev/null 2>&1 || true
	@launchctl bootstrap "gui/$$(id -u)" "$$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" >/dev/null 2>&1 || true
	@launchctl kickstart -k "gui/$$(id -u)/ai.openclaw.gateway" >/dev/null 2>&1 || true
	@$(OPENCLAW) gateway start >/dev/null 2>&1 || true
	@echo "Gateway status:"
	@($(OPENCLAW) gateway status --json 2>/dev/null || echo '{}') | $(PYTHON) -c 'import json,sys; raw=sys.stdin.read().strip() or "{}"; d=json.loads(raw) if raw.startswith("{") else {}; rpc=d.get("rpc") or {}; gw=d.get("gateway") or {}; print("probeUrl:", gw.get("probeUrl") or rpc.get("url") or "unknown"); print("rpc.ok:", bool(rpc.get("ok"))); print("rpc.error:", rpc.get("error") or "none")' || true

doctor-stack:
	@set -e; \
	MIDDLEWARE_URL="http://127.0.0.1:$(MIDDLEWARE_PORT)"; \
	BACKEND_URL="http://127.0.0.1:$(BACKEND_PORT)"; \
	FRONTEND_URL="http://127.0.0.1:$(PORT)"; \
	echo "Checking middleware health at $$MIDDLEWARE_URL/health ..."; \
	MIDDLEWARE_HEALTH_HTTP=$$(curl -sS -o /tmp/ds_middleware_health.json -w "%{http_code}" "$$MIDDLEWARE_URL/health" || true); \
	if [ "$$MIDDLEWARE_HEALTH_HTTP" != "200" ]; then \
		echo "FAIL middleware health (HTTP $$MIDDLEWARE_HEALTH_HTTP)"; \
		exit 1; \
	fi; \
	if ! $(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_middleware_health.json")); assert d.get("status")=="ok"'; then \
		echo "FAIL middleware health payload"; \
		exit 1; \
	fi; \
	echo "PASS middleware health"; \
	echo "Checking frontend health at $$FRONTEND_URL/health ..."; \
	FRONTEND_HTTP=$$(curl -sS -o /tmp/ds_frontend_health.json -w "%{http_code}" "$$FRONTEND_URL/health" || true); \
	if [ "$$FRONTEND_HTTP" != "200" ]; then \
		echo "FAIL frontend health (HTTP $$FRONTEND_HTTP)"; \
		exit 1; \
	fi; \
	if ! $(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_frontend_health.json")); assert d.get("status")=="ok"'; then \
		echo "FAIL frontend health payload"; \
		exit 1; \
	fi; \
	echo "PASS frontend health"; \
	echo "Checking backend health at $$BACKEND_URL/health ..."; \
	BACKEND_HTTP=$$(curl -sS -o /tmp/ds_backend_health.json -w "%{http_code}" "$$BACKEND_URL/health" || true); \
	if [ "$$BACKEND_HTTP" != "200" ]; then \
		echo "FAIL backend health (HTTP $$BACKEND_HTTP)"; \
		exit 1; \
	fi; \
	if ! $(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_backend_health.json")); assert d.get("status")=="ok"'; then \
		echo "FAIL backend health payload"; \
		exit 1; \
	fi; \
	echo "PASS backend health"; \
	echo "Checking frontend /v1/chat/completions at $$FRONTEND_URL/v1/chat/completions ..."; \
	FRONTEND_CHAT_HTTP=$$(curl -sS -o /tmp/ds_frontend_chat.json -w "%{http_code}" "$$FRONTEND_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"llama3.2:latest","messages":[{"role":"user","content":"reply with exactly stack-ok"}],"stream":false}' || true); \
	if [ "$$FRONTEND_CHAT_HTTP" != "200" ]; then \
		echo "FAIL frontend chat completion (HTTP $$FRONTEND_CHAT_HTTP)"; \
		exit 1; \
	fi; \
	if ! $(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_frontend_chat.json")); assert isinstance(d.get("choices"), list) and len(d["choices"])>0'; then \
		echo "FAIL frontend completion payload"; \
		exit 1; \
	fi; \
	echo "PASS frontend /v1/chat/completions"; \
	echo "Checking OpenClaw gateway reachability ..."; \
	$(OPENCLAW) gateway status --json 2>/dev/null > /tmp/ds_openclaw_gateway.json || true; \
	if ! $(PYTHON) -c 'import json,sys; d=json.load(open("/tmp/ds_openclaw_gateway.json")); gw=(d.get("gateway") or {}).get("probeUrl") or (d.get("rpc") or {}).get("url"); ok=bool((d.get("rpc") or {}).get("ok")); print(gw or ""); sys.exit(0 if (gw and ok) else 1)'; then \
		echo "FAIL OpenClaw gateway not reachable"; \
		$(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_openclaw_gateway.json")); svc=d.get("service") or {}; rt=svc.get("runtime") or {}; rpc=d.get("rpc") or {}; print("gateway runtime:", rt.get("detail") or "unknown"); print("gateway rpc error:", rpc.get("error") or "unknown")'; \
		echo "Try: openclaw gateway install && openclaw gateway start"; \
		exit 1; \
	fi; \
	echo "PASS OpenClaw gateway reachable"; \
	echo "doctor-stack: all checks passed"

launch-all: install
	@echo "Starting middleware + frontend UI..."
	@make kill-all PORT=$(PORT) MIDDLEWARE_PORT=$(MIDDLEWARE_PORT) BACKEND_PORT=$(BACKEND_PORT)
	@if [ "$(ENABLE_OPENCLAW_BOOT)" = "1" ]; then \
		echo "Starting OpenClaw gateway..."; \
		$(MAKE) --no-print-directory openclaw-desktop; \
		$(OPENCLAW) gateway status --json 2>/dev/null | $(PYTHON) -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if bool((d.get("rpc") or {}).get("ok")) else 1)' >/dev/null 2>&1 || { \
			echo "OpenClaw service gateway not reachable; starting local gateway on port $(OPENCLAW_GATEWAY_PORT)..."; \
			$(OPENCLAW) gateway --force --port $(OPENCLAW_GATEWAY_PORT) >/tmp/openclaw-gateway.log 2>&1 & \
			echo $$! > .openclaw.gateway.pid; \
			sleep 2; \
		}; \
		GW_URL=$$($(MAKE) --no-print-directory openclaw-gateway-url || true); \
		if [ -n "$$GW_URL" ]; then \
			echo "Detected OpenClaw gateway: $$GW_URL"; \
			printf "OPENCLAW_GATEWAY_URL=%s\n" "$$GW_URL" > .openclaw.env || true; \
		else \
			echo "Could not resolve OpenClaw gateway URL; continuing."; \
		fi; \
		echo "Opening OpenClaw dashboard..."; \
		$(OPENCLAW) dashboard >/dev/null || true; \
	else \
		echo "Skipping OpenClaw bootstrap (ENABLE_OPENCLAW_BOOT=$(ENABLE_OPENCLAW_BOOT))."; \
	fi
	@set -m; \
	( cd "$(MIDDLEWARE_DIR)" && make launch-all PORT=$(MIDDLEWARE_PORT) BACKEND_PORT=$(BACKEND_PORT) SKIP_OPENCLAW_GATEWAY=1 ) & MID_STACK_PID=$$!; \
	( cd "$$(pwd)" && API_BASE=$(API_BASE) make launch-ui PORT=$(PORT) MIDDLEWARE_PORT=$(MIDDLEWARE_PORT) BACKEND_PORT=$(BACKEND_PORT) ) & UI_PID=$$!; \
	trap 'echo "\nStopping..."; kill $$MID_STACK_PID $$UI_PID 2>/dev/null || true' INT TERM EXIT; \
	wait

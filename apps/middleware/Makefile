.DEFAULT_GOAL := install

# Variables
PYTHON = python3
VENV_DIR ?= .venv
PORT ?= 5001
BACKEND_DIR ?= /Users/davidberigny/Documents/GitHub/ds-backend-local
BACKEND_PORT ?= 8080
API_BASE ?= http://127.0.0.1:$(BACKEND_PORT)
APP_MODE ?= legacy
APP_MODULE ?= app:app
OPENCLAW ?= openclaw
OPENCLAW_GATEWAY_PORT ?= 18789
SKIP_OPENCLAW_GATEWAY ?= 0
OPENCLAW_LOG_DIR ?= $(HOME)/.openclaw/logs
DUAL_LOG_FILE ?= $(OPENCLAW_LOG_DIR)/dualsubstrate.log
DUAL_BACKEND_LOG ?= $(OPENCLAW_LOG_DIR)/dualsubstrate-backend.log
DUAL_MIDDLEWARE_LOG ?= $(OPENCLAW_LOG_DIR)/dualsubstrate-middleware.log
NGROK ?= ngrok
ENABLE_NGROK ?= 1
NGROK_PORT ?= $(PORT)
NGROK_LOG_FILE ?= $(OPENCLAW_LOG_DIR)/dualsubstrate-ngrok.log
NGROK_PID_FILE ?= .ngrok.pid
MCP_URL_FILE ?= .mcp.url

.PHONY: install launch launch-fastapi launch-ui daemon run-stack clean kill-port kill-all launch-all openclaw-gateway-url doctor-stack openclaw-desktop mcp-url

ifeq ($(APP_MODE),fastapi)
APP_MODULE := fastapi_app:app
endif

install:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "Creating virtual environment at $(VENV_DIR)..."; \
		$(PYTHON) -m venv $(VENV_DIR); \
	fi
	@echo "Installing dependencies..."
	$(VENV_DIR)/bin/pip install -r requirements.txt

launch: install
	@echo "Launching application on port $(PORT)..."
	@make kill-port
	API_BASE=$(API_BASE) MCP_PUBLIC_BASE_URL=$(MCP_PUBLIC_BASE_URL) OPENCLAW_GATEWAY_URL=$(OPENCLAW_GATEWAY_URL) APP_MODE=$(APP_MODE) $(VENV_DIR)/bin/python -m uvicorn $(APP_MODULE) --port $(PORT)

launch-fastapi:
	@$(MAKE) launch APP_MODE=fastapi

kill-port:
	@echo "Releasing port $(PORT)..."
	@PIDS=$$(lsof -ti tcp:$(PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No process found listening on port $(PORT)."; \
	fi

kill-all:
	@echo "Releasing UI port $(PORT)..."
	@PIDS=$$(lsof -ti tcp:$(PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing UI processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No process found listening on UI port $(PORT)."; \
	fi
	@echo "Releasing backend port $(BACKEND_PORT)..."
	@PIDS=$$(lsof -ti tcp:$(BACKEND_PORT)); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing backend processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No process found listening on backend port $(BACKEND_PORT)."; \
	fi
	@echo "Stopping sync daemon..."
	@PIDS=$$(pgrep -f "[s]ync_daemon.py" || true); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing sync daemon processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No sync daemon process found."; \
	fi
	@echo "Stopping ngrok tunnel..."
	@PIDS=$$(pgrep -f "[n]grok http $(NGROK_PORT)" || true); \
	if [ -n "$$PIDS" ]; then \
		echo "Killing ngrok processes $$PIDS"; \
		kill $$PIDS; \
	else \
		echo "No ngrok process found for port $(NGROK_PORT)."; \
	fi
	@if [ -f "$(NGROK_PID_FILE)" ]; then \
		NG_PID=$$(cat "$(NGROK_PID_FILE)" 2>/dev/null || true); \
		if [ -n "$$NG_PID" ] && kill -0 "$$NG_PID" 2>/dev/null; then \
			echo "Killing tracked ngrok PID $$NG_PID"; \
			kill "$$NG_PID" 2>/dev/null || true; \
		fi; \
		rm -f "$(NGROK_PID_FILE)"; \
	fi
	@if [ -f "$(MCP_URL_FILE)" ]; then rm -f "$(MCP_URL_FILE)"; fi
	@if [ "$(SKIP_OPENCLAW_GATEWAY)" = "1" ]; then \
		echo "Skipping OpenClaw gateway stop (SKIP_OPENCLAW_GATEWAY=1)."; \
	else \
		echo "Stopping OpenClaw gateway..."; \
		$(OPENCLAW) gateway stop >/dev/null 2>&1 || true; \
		echo "Releasing OpenClaw gateway port $(OPENCLAW_GATEWAY_PORT)..."; \
		PIDS=$$(lsof -ti tcp:$(OPENCLAW_GATEWAY_PORT)); \
		if [ -n "$$PIDS" ]; then \
			echo "Killing OpenClaw gateway processes $$PIDS"; \
			kill $$PIDS; \
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
	fi

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
	@if [ -f "$(MCP_URL_FILE)" ]; then \
		cat "$(MCP_URL_FILE)"; \
	else \
		echo ""; \
	fi

run-stack: install
	@echo "Starting UI and daemon in parallel..."
	@make kill-port PORT=$(PORT)
	@(API_BASE=$(API_BASE) APP_MODE=$(APP_MODE) $(VENV_DIR)/bin/python -m uvicorn $(APP_MODULE) --port $(PORT) &) && LOCAL_API=$(API_BASE) $(VENV_DIR)/bin/python sync_daemon.py

openclaw-gateway-url:
	@$(OPENCLAW) gateway status --json 2>/dev/null | $(PYTHON) -c 'import json, sys; data = json.load(sys.stdin); gateway = (data.get("gateway") or {}).get("probeUrl"); rpc = (data.get("rpc") or {}).get("url"); print(gateway or rpc or "")'

openclaw-desktop:
	@echo "Configuring OpenClaw desktop access..."
	@$(OPENCLAW) gateway install >/dev/null 2>&1 || true
	@launchctl bootstrap "gui/$$(id -u)" "$$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" >/dev/null 2>&1 || true
	@launchctl kickstart -k "gui/$$(id -u)/ai.openclaw.gateway" >/dev/null 2>&1 || true
	@$(OPENCLAW) gateway start >/dev/null 2>&1 || true
	@echo "Gateway status:"
	@$(OPENCLAW) gateway status --json 2>/dev/null | $(PYTHON) -c 'import json; d=json.load(__import__("sys").stdin); rpc=d.get("rpc") or {}; gw=d.get("gateway") or {}; print("probeUrl:", gw.get("probeUrl") or rpc.get("url") or "unknown"); print("rpc.ok:", bool(rpc.get("ok"))); print("rpc.error:", rpc.get("error") or "none")'

doctor-stack:
	@set -e; \
	BACKEND_URL="http://127.0.0.1:$(BACKEND_PORT)"; \
	MIDDLEWARE_URL="http://127.0.0.1:$(PORT)"; \
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
	echo "Checking middleware /v1/chat/completions at $$MIDDLEWARE_URL/v1/chat/completions ..."; \
	MIDDLEWARE_HTTP=$$(curl -sS -o /tmp/ds_middleware_chat.json -w "%{http_code}" "$$MIDDLEWARE_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"llama3.2:latest","messages":[{"role":"user","content":"reply with exactly stack-ok"}],"stream":false}' || true); \
	if [ "$$MIDDLEWARE_HTTP" != "200" ]; then \
		echo "FAIL middleware chat completion (HTTP $$MIDDLEWARE_HTTP)"; \
		exit 1; \
	fi; \
	if ! $(PYTHON) -c 'import json; d=json.load(open("/tmp/ds_middleware_chat.json")); assert isinstance(d.get("choices"), list) and len(d["choices"])>0'; then \
		echo "FAIL middleware completion payload"; \
		exit 1; \
	fi; \
	echo "PASS middleware /v1/chat/completions"; \
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
	@echo "Starting backend + UI..."
	@mkdir -p "$(OPENCLAW_LOG_DIR)"
	@touch "$(DUAL_LOG_FILE)" "$(DUAL_BACKEND_LOG)" "$(DUAL_MIDDLEWARE_LOG)"
	@make kill-all PORT=$(PORT) BACKEND_PORT=$(BACKEND_PORT) SKIP_OPENCLAW_GATEWAY=$(SKIP_OPENCLAW_GATEWAY)
	@if [ "$(SKIP_OPENCLAW_GATEWAY)" = "1" ]; then \
		echo "Skipping OpenClaw gateway startup (SKIP_OPENCLAW_GATEWAY=1)."; \
	else \
		echo "Starting OpenClaw gateway..."; \
		$(MAKE) --no-print-directory openclaw-desktop; \
		$(OPENCLAW) gateway status --json 2>/dev/null | $(PYTHON) -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if bool((d.get("rpc") or {}).get("ok")) else 1)' >/dev/null 2>&1 || { \
			echo "OpenClaw service gateway not reachable; starting local gateway on port $(OPENCLAW_GATEWAY_PORT)..."; \
			$(OPENCLAW) gateway --force --port $(OPENCLAW_GATEWAY_PORT) >/tmp/openclaw-gateway.log 2>&1 & \
			echo $$! > .openclaw.gateway.pid; \
			sleep 2; \
		}; \
	fi
	@GW_URL=$$($(MAKE) --no-print-directory openclaw-gateway-url || true); \
	if [ -n "$$GW_URL" ]; then \
		echo "Detected OpenClaw gateway: $$GW_URL"; \
		printf "OPENCLAW_GATEWAY_URL=%s\n" "$$GW_URL" > .openclaw.env; \
	else \
		echo "Could not resolve OpenClaw gateway URL; continuing."; \
	fi
	@if [ "$(SKIP_OPENCLAW_GATEWAY)" = "1" ]; then \
		echo "Skipping OpenClaw dashboard open (SKIP_OPENCLAW_GATEWAY=1)."; \
	else \
		echo "Opening OpenClaw dashboard..."; \
		$(OPENCLAW) dashboard >/dev/null || true; \
	fi
	@set -m; \
	GW_URL=$$($(MAKE) --no-print-directory openclaw-gateway-url || true); \
	MCP_BASE=""; \
	if [ "$(ENABLE_NGROK)" = "1" ]; then \
		if command -v "$(NGROK)" >/dev/null 2>&1; then \
			echo "Starting ngrok tunnel on port $(NGROK_PORT)..."; \
			$(NGROK) http $(NGROK_PORT) >"$(NGROK_LOG_FILE)" 2>&1 & echo $$! > "$(NGROK_PID_FILE)"; \
			sleep 2; \
				MCP_BASE=$$(curl -sS http://127.0.0.1:4040/api/tunnels | $(PYTHON) -c 'import json,sys; d=json.load(sys.stdin); t=d.get("tunnels") or []; print(next((x.get("public_url","") for x in t if (x.get("public_url") or "").startswith("https://")), ""))'); \
			if [ -n "$$MCP_BASE" ]; then \
				echo "MCP public base: $$MCP_BASE"; \
				echo "$$MCP_BASE/mcp" > "$(MCP_URL_FILE)"; \
				echo "MCP URL saved to $(MCP_URL_FILE)"; \
			else \
				echo "Could not resolve ngrok HTTPS tunnel URL."; \
			fi; \
		else \
			echo "ngrok not found; skipping tunnel startup."; \
		fi; \
	else \
		echo "Skipping ngrok startup (ENABLE_NGROK=$(ENABLE_NGROK))."; \
	fi; \
	( cd "$(BACKEND_DIR)" && OPENCLAW_GATEWAY_URL="$$GW_URL" make launch-local BACKEND_PORT=$(BACKEND_PORT) 2>&1 | sed -e "s/^/[backend] /" | tee -a "$(DUAL_BACKEND_LOG)" "$(DUAL_LOG_FILE)" ) & BACK_PID=$$!; \
	( cd "$$(pwd)" && API_BASE=$(API_BASE) OPENCLAW_GATEWAY_URL="$$GW_URL" MCP_PUBLIC_BASE_URL="$$MCP_BASE" make launch-ui PORT=$(PORT) BACKEND_PORT=$(BACKEND_PORT) 2>&1 | sed -e "s/^/[middleware] /" | tee -a "$(DUAL_MIDDLEWARE_LOG)" "$(DUAL_LOG_FILE)" ) & UI_PID=$$!; \
	trap 'echo "\nStopping..."; kill $$BACK_PID $$UI_PID 2>/dev/null || true' INT TERM EXIT; \
	wait

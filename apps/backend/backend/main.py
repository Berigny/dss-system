"""FastAPI backend for Dual-Substrate live audio memory."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.websockets import WebSocketDisconnect
from openai import OpenAI
from prometheus_client import make_asgi_app
from rocksdict import Rdict

# --- FIX: Direct Router Imports ---
from backend.api import ledger as ledger_api
from backend.api import ui as ui_api
from backend.api.account import router as account_router
from backend.api.admin import control_plane_router, public_router, router as admin_router
from backend.api.auth import router as auth_router
from backend.api.chat import assess_router as chat_assess_router, router as chat_router
from backend.api.compat import router as compat_router
from backend.api.governance_routes import router as governance_router
from backend.api.billing import router as billing_router
from backend.api.resolver import router as resolver_router
from backend.api.ingest import router as ingest_router
from backend.api.enrich import router as enrich_router
from backend.api.assemble import router as assemble_router
from backend.api.stats import router as stats_router
from backend.api.sync import router as sync_router
from backend.api.projection import router as projection_router
from backend.api.wallet import router as wallet_router
from backend.api.wizard import router as wizard_router
from backend.api.voice import router as voice_router
from backend.api.telegram import router as telegram_router
from backend.services.session_tokens import apply_session_token_claims_or_raise
from backend.api.http import (
    router as ledger_router,
    search_router,
    web4_router,
)
# ----------------------------------

from backend.routers import qp_rest
from backend.metrics.store import close_telemetry_emitter


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)

WS_ROUTE = "/ws"
PCM_ROUTE = "/pcm"

DB_PATH_ENV = os.getenv("DB_PATH", "./data")
DB_FILE = "ledger.db"
GIT_SHA = os.getenv("GIT_SHA", "").strip() or "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open RocksDB on startup, close on shutdown."""
    db_base = DB_PATH_ENV or tempfile.gettempdir()
    db_full = os.path.join(db_base, DB_FILE)
    os.makedirs(os.path.dirname(db_full), exist_ok=True)
    db = Rdict(db_full)
    app.state.db = db
    LOGGER.info("Build info git_sha=%s", GIT_SHA)
    LOGGER.info("RocksDB opened at %s", db_full)
    try:
        yield
    finally:
        close_telemetry_emitter(db)
        db.close()
        LOGGER.info("RocksDB closed")


_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "backend": None,
    "headers": {},
    "threshold": 0.7,
    "baseline": False,
    "client": None,
}


def set_state(**updates: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(updates)


def get_state() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_STATE)


def configure(
    backend: str | None = None,
    api_key: str | None = None,
    openai_key: str | None = None,
    threshold: float | None = None,
    baseline: bool | None = None,
) -> None:
    updates: Dict[str, Any] = {}
    if backend:
        updates["backend"] = backend.rstrip("/")
    if api_key:
        updates["headers"] = {"Authorization": f"Bearer {api_key}"}
    if threshold is not None:
        updates["threshold"] = float(threshold)
    if baseline is not None:
        updates["baseline"] = bool(baseline)

    if openai_key:
        updates["client"] = OpenAI(api_key=openai_key)
    elif openai_key == "":
        updates["client"] = None

    if updates:
        set_state(**updates)


def configure_from_env() -> None:
    backend = os.getenv("FASTAPI_ROOT")
    api_key = os.getenv("DUALSUBSTRATE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    threshold_raw = os.getenv("SALIENCE_THRESHOLD")
    baseline_raw = os.getenv("BASELINE_MODE")

    threshold = float(threshold_raw) if threshold_raw else None
    baseline = None
    if baseline_raw:
        baseline = baseline_raw.strip().lower() in {"1", "true", "yes", "on"}

    configure(
        backend=backend,
        api_key=api_key,
        openai_key=openai_key,
        threshold=threshold,
        baseline=baseline,
    )


def _transcribe_chunk(data: bytes) -> str:
    state = get_state()
    client: OpenAI | None = state.get("client")
    if client is None:
        raise RuntimeError("OpenAI client not configured")

    audio = BytesIO(data)
    audio.name = "chunk.webm"
    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
    return (transcript.text or "").strip()


def _call_salience(
    backend: str,
    headers: Dict[str, str],
    text: str,
    threshold: float,
    timestamp: float,
) -> Dict[str, Any]:
    payload = {"utterance": text, "timestamp": timestamp, "threshold": threshold}
    try:
        response = requests.post(
            f"{backend}/salience", json=payload, headers=headers, timeout=15
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {
            "stored": False,
            "text": text,
            "error": str(exc),
            "score": None,
            "timestamp": timestamp,
        }
    
    data.setdefault("timestamp", timestamp)
    data.setdefault("text", text)
    return data


app = FastAPI(lifespan=lifespan)

_cors_origins_raw = os.getenv("MIDDLEWARE_CORS_ORIGINS", "").strip()
BACKEND_CORS_ORIGINS = [
    origin.strip()
    for origin in _cors_origins_raw.split(",")
    if origin and origin.strip()
]
BACKEND_CORS_ORIGIN_REGEX = os.getenv(
    "BACKEND_CORS_ORIGIN_REGEX",
    r"https://(ds-frontend-local-new.*\.vercel\.app|([a-z0-9-]+\.)?dualsubstrate\.com)",
).strip() or None


@app.get("/version")
def get_version() -> dict[str, str]:
    return {"git_sha": GIT_SHA}
app.add_middleware(
    CORSMiddleware,
    allow_origins=BACKEND_CORS_ORIGINS,
    allow_origin_regex=BACKEND_CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.middleware("http")
async def session_token_claim_middleware(request, call_next):
    try:
        apply_session_token_claims_or_raise(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)

# Include Routers
app.include_router(qp_rest.router)
app.include_router(ledger_router)
app.include_router(search_router)
app.include_router(web4_router)
app.include_router(ingest_router, prefix="/api")
app.include_router(enrich_router)
app.include_router(assemble_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(public_router)
app.include_router(control_plane_router)
app.include_router(auth_router)
app.include_router(governance_router)
app.include_router(compat_router)
app.include_router(chat_router)
app.include_router(chat_assess_router)
app.include_router(stats_router)
app.include_router(billing_router)
app.include_router(resolver_router)
app.include_router(sync_router)
app.include_router(projection_router)
app.include_router(wallet_router)
app.include_router(wizard_router)
app.include_router(ui_api.router)
app.include_router(ledger_api.router, prefix="/ledger", tags=["ledger"])
app.include_router(voice_router)
app.include_router(telegram_router)


@app.get("/health", include_in_schema=False)
def health() -> Dict[str, str]:
    return {"status": "ok", "git_sha": GIT_SHA}

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

@app.websocket(WS_ROUTE)
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                payload = await websocket.receive_bytes()
            except WebSocketDisconnect:
                break

            try:
                text = await loop.run_in_executor(None, _transcribe_chunk, payload)
            except Exception as exc:
                await websocket.send_text(json.dumps({"stored": False, "error": str(exc)}))
                continue

            if not text:
                continue

            state = get_state()
            backend = state.get("backend")
            headers = state.get("headers", {})
            threshold = float(state.get("threshold", 0.7))
            baseline = bool(state.get("baseline", False))
            timestamp = time.time()

            if baseline or not backend:
                await websocket.send_text(
                    json.dumps({
                        "stored": False, 
                        "text": text, 
                        "score": None, 
                        "timestamp": timestamp,
                        "baseline": baseline,
                        "reason": "baseline" if baseline else "backend-unset"
                    })
                )
                continue

            result = await loop.run_in_executor(
                None, _call_salience, backend, headers, text, threshold, timestamp
            )
            await websocket.send_text(json.dumps(result))
    finally:
        await websocket.close()


@app.websocket(PCM_ROUTE)
async def pcm_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            try:
                data = await websocket.receive_bytes()
            except WebSocketDisconnect:
                break
            await websocket.send_bytes(data)
    finally:
        await websocket.close()


@app.get("/exact/{key_hex}")
def proxy_exact(key_hex: str) -> JSONResponse:
    state = get_state()
    backend = state.get("backend")
    headers = state.get("headers", {})
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not configured")
    try:
        response = requests.get(f"{backend}/exact/{key_hex}", headers=headers, timeout=10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return JSONResponse(response.json())


configure_from_env()

__all__ = [
    "PCM_ROUTE",
    "WS_ROUTE",
    "app",
    "configure",
    "configure_from_env",
    "get_state",
    "set_state",
]

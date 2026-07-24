"""Telegram surface v0.1 for LOAM.

Exposes:
  POST /v1/telegram/webhook          — Telegram Bot API webhook
  POST /v1/telegram/pairing-code     — mint a one-time pairing code (admin)

Flow:
1. Admin calls /v1/telegram/pairing-code with a principal_did.
2. Control Plane shows the code to the user.
3. User sends `/start <code>` to the Telegram bot.
4. Backend binds chat_id -> principal_did.
5. Subsequent messages from that chat are forwarded to the LOAM chat endpoint
   and the reply is sent back via Telegram sendMessage.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services.session_tokens import (
    apply_auth_claim_overrides,
    mint_surface_session_bundle,
)
from backend.services.surface_scope import assert_surface_ledger_access

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_ADMIN_SECRET = os.getenv("TELEGRAM_ADMIN_SECRET", "")
TELEGRAM_SURFACE_ID = os.getenv("TELEGRAM_SURFACE_ID", "surface:telegram:primary")
TELEGRAM_LEDGER_ID = os.getenv("TELEGRAM_LEDGER_ID", "pilot")
TELEGRAM_CHAT_MODEL = os.getenv("TELEGRAM_CHAT_MODEL", "gpt-4o-mini")
PAIRING_CODE_TTL_SECONDS = int(os.getenv("TELEGRAM_PAIRING_CODE_TTL_SECONDS", "600"))
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

# In-memory pairing-code cache. Codes are short-lived (10 min default).
_pairing_codes: dict[str, dict[str, Any]] = {}

# RocksDB key for persistent chat -> principal bindings.
_TELEGRAM_BINDINGS_KEY = b"__telegram_bindings_v1__"

# RocksDB key for recent update_ids used for deduplication.
_TELEGRAM_UPDATE_IDS_KEY = b"__telegram_update_ids_v1__"


class PairingCodeRequest(BaseModel):
    principal_did: str = Field(..., min_length=1)


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: int


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict[str, Any] | None = None
    edited_message: dict[str, Any] | None = None
    channel_post: dict[str, Any] | None = None


TelegramUpdate.model_rebuild()


def _db(request: Request) -> Any:
    return getattr(getattr(request, "app", None), "state", None).db


def _load_bindings(db: Any) -> dict[str, str]:
    """Return chat_id -> principal_did bindings from RocksDB."""
    raw = db.get(_TELEGRAM_BINDINGS_KEY)
    if raw is None:
        return {}
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        data = json.loads(decoded)
    except Exception:
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
    return {}


def _save_bindings(db: Any, bindings: dict[str, str]) -> None:
    db[_TELEGRAM_BINDINGS_KEY] = json.dumps(bindings, separators=(",", ":"), sort_keys=True).encode()


def _load_update_ids(db: Any) -> dict[str, int]:
    """Return recent update_id -> timestamp map."""
    raw = db.get(_TELEGRAM_UPDATE_IDS_KEY)
    if raw is None:
        return {}
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        data = json.loads(decoded)
    except Exception:
        return {}
    if isinstance(data, dict):
        return {str(k): int(v) for k, v in data.items()}
    return {}


def _save_update_ids(db: Any, update_ids: dict[str, int]) -> None:
    db[_TELEGRAM_UPDATE_IDS_KEY] = json.dumps(update_ids, separators=(",", ":"), sort_keys=True).encode()


def _prune_update_ids(update_ids: dict[str, int], window_seconds: int = 86400) -> dict[str, int]:
    cutoff = int(time.time()) - window_seconds
    return {uid: ts for uid, ts in update_ids.items() if ts > cutoff}


def _extract_chat_id_and_text(update: TelegramUpdate) -> tuple[int | None, str | None]:
    message = update.message or update.edited_message or update.channel_post
    if not message:
        return None, None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or message.get("caption")
    return (int(chat_id) if chat_id is not None else None), text


async def _send_telegram_message(chat_id: int, text: str) -> None:
    """Send a text reply via the Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        LOGGER.warning("TELEGRAM_BOT_TOKEN not set; would reply to %s: %s", chat_id, text[:200])
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:MAX_TELEGRAM_MESSAGE_LENGTH],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            resp.raise_for_status()
    except Exception as exc:
        LOGGER.exception("Failed to send Telegram message to %s", chat_id)
        raise HTTPException(status_code=502, detail=f"Telegram send failed: {exc}") from exc


async def _call_chat(request: Request, principal_did: str, message: str) -> str:
    """Call the internal /chat endpoint as the paired principal and return reply text."""
    bundle = mint_surface_session_bundle(
        principal_did=principal_did,
        ledger_ids=[TELEGRAM_LEDGER_ID],
        access_ttl_seconds=300,
    )
    token = bundle["session"]["token"]

    chat_payload = {
        "session_id": f"telegram-{principal_did}",
        "message": message,
        "principal_did": principal_did,
        "ledger_id": TELEGRAM_LEDGER_ID,
        "entity": f"telegram-{principal_did}",
        "provider": TELEGRAM_CHAT_MODEL,
        "enable_ledger": True,
        "persist_conversation": True,
    }

    try:
        transport = httpx.ASGITransport(app=request.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json=chat_payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        LOGGER.exception("Internal chat call failed for principal %s", principal_did)
        raise HTTPException(status_code=502, detail=f"Chat call failed: {exc}") from exc

    return str(data.get("text") or "")


@router.post("/pairing-code", response_model=PairingCodeResponse)
async def create_pairing_code(request: Request, req: PairingCodeRequest) -> dict[str, Any]:
    """Mint a one-time pairing code for a principal. Called by Control Plane / admin."""
    admin_secret = request.headers.get("x-telegram-admin-secret", "")
    if TELEGRAM_ADMIN_SECRET and admin_secret != TELEGRAM_ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid Telegram admin secret")

    code = secrets.token_urlsafe(8)
    expires_at = int(time.time()) + PAIRING_CODE_TTL_SECONDS
    _pairing_codes[code] = {
        "principal_did": req.principal_did.strip(),
        "expires_at": expires_at,
    }
    LOGGER.info("Telegram pairing code minted for %s", req.principal_did)
    return {"code": code, "expires_at": expires_at}


@router.post("/webhook")
async def telegram_webhook(request: Request, update: TelegramUpdate) -> dict[str, bool]:
    """Receive updates from Telegram Bot API."""
    if TELEGRAM_WEBHOOK_SECRET:
        secret = request.headers.get("x-telegram-bot-api-secret-token", "")
        if secret != TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    db = _db(request)

    # Deduplicate by update_id.
    update_ids = _load_update_ids(db)
    if str(update.update_id) in update_ids:
        LOGGER.debug("Duplicate Telegram update_id %s ignored", update.update_id)
        return {"ok": True}
    update_ids = _prune_update_ids(update_ids)
    update_ids[str(update.update_id)] = int(time.time())
    _save_update_ids(db, update_ids)

    chat_id, text = _extract_chat_id_and_text(update)
    if chat_id is None:
        return {"ok": True}

    bindings = _load_bindings(db)
    chat_id_str = str(chat_id)

    if text and text.strip().startswith("/start "):
        code = text.strip().split(" ", 1)[1].strip()
        pairing = _pairing_codes.pop(code, None)
        if pairing is None or int(pairing.get("expires_at", 0)) < time.time():
            await _send_telegram_message(chat_id, "Invalid or expired pairing code. Please request a new one from the Control Plane.")
            return {"ok": True}

        principal_did = pairing["principal_did"]
        bindings[chat_id_str] = principal_did
        _save_bindings(db, bindings)
        LOGGER.info("Telegram chat %s paired with principal %s", chat_id, principal_did)
        await _send_telegram_message(chat_id, "This chat is now paired with your DSS account. You can start messaging LOAM.")
        return {"ok": True}

    principal_did = bindings.get(chat_id_str)
    if not principal_did:
        await _send_telegram_message(
            chat_id,
            "This chat is not paired. Send `/start <code>` using the pairing code from the Control Plane.",
        )
        return {"ok": True}

    if not text:
        return {"ok": True}

    # Authorize through surface scope by setting the paired principal on the request.
    request.state.auth_claim_principal_did = principal_did
    request.state.auth_claim_principal_key_id = None
    request.state.auth_claim_session_jti = None
    apply_auth_claim_overrides(
        request,
        principal_did=principal_did,
        principal_key_id=None,
        session_jti=None,
    )
    assert_surface_ledger_access(request, TELEGRAM_SURFACE_ID, TELEGRAM_LEDGER_ID)

    try:
        reply = await _call_chat(request, principal_did, text)
    except HTTPException as exc:
        # Surface abstention / refusal verbatim.
        detail = exc.detail
        if isinstance(detail, dict):
            reply = detail.get("detail") or detail.get("error") or json.dumps(detail)
        else:
            reply = str(detail)
        if not reply:
            reply = "Sorry, I could not process that message."

    if reply:
        await _send_telegram_message(chat_id, reply)

    return {"ok": True}

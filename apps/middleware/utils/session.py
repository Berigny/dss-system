"""Simple in-memory session management."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict

from config.settings import DEFAULT_LEDGER_ID, DEFAULT_SESSION_ID

SessionState = Dict[str, Any]

sessions: Dict[str, SessionState] = {}

def hash_ledger_id(ledger_id: str) -> str:
    return hashlib.sha256(ledger_id.encode("utf-8")).hexdigest()[:8]


def hash_session_id(ledger_id: str, session_id: str) -> str:
    session_seed = f"{ledger_id}:{session_id}"
    return hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:8]


def build_entity_namespace(ledger_id: str, session_id: str) -> str:
    """Resolve the middleware entity namespace.

    Default behavior matches backend LEDGER_NAMESPACE_SOURCE=ledger_id so
    history reads/writes stay in the same canonical namespace.
    Set MIDDLEWARE_ENTITY_MODE=session_hash to restore legacy per-session hash mode.
    """

    mode = str(os.getenv("MIDDLEWARE_ENTITY_MODE", "ledger") or "ledger").strip().lower()
    ledger_value = str(ledger_id or DEFAULT_LEDGER_ID).strip() or DEFAULT_LEDGER_ID
    if mode == "session_hash":
        ledger_hash = hash_ledger_id(ledger_value)
        session_hash = hash_session_id(ledger_value, session_id)
        return f"{ledger_hash}:{session_hash}"
    return ledger_value


def get_session(session_id: str = DEFAULT_SESSION_ID) -> SessionState:
    """Fetch or initialize a chat session state."""

    if session_id not in sessions:
        entity = build_entity_namespace(DEFAULT_LEDGER_ID, session_id)
        sessions[session_id] = {
            "messages": [],
            "entity": entity,
            "ledger_id": DEFAULT_LEDGER_ID,
            "total_cost": 0.0,
            "memory_count": 0,
            "last_latency_ms": 0,
        }
    else:
        session = sessions[session_id]
        ledger_id = session.get("ledger_id", DEFAULT_LEDGER_ID)
        session["entity"] = build_entity_namespace(str(ledger_id), session_id)
    return sessions[session_id]


def update_session(session_id: str, session: SessionState) -> None:
    """Persist the latest session state in memory."""

    sessions[session_id] = session

def clear_session(session_id: str) -> None:
    """Clear the conversation history for a session."""

    if session_id in sessions:
        sessions[session_id]["messages"] = []
        sessions[session_id]["total_cost"] = 0.0
        sessions[session_id]["last_latency_ms"] = 0

"""Assurance envelope helpers for model-authenticated turn writes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _hash_history(history: list[dict[str, Any]] | None) -> str:
    safe_history = history if isinstance(history, list) else []
    return hashlib.sha256(_canonical_json({"history": safe_history}).encode("utf-8")).hexdigest()


def _model_key_map() -> dict[str, str]:
    raw = (os.getenv("ASSURANCE_MODEL_KEYS_JSON") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    mapping: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            mapping[key.strip()] = value.strip()
    return mapping


def _resolve_secret(model_id: str, provider: str) -> tuple[str, str]:
    model = (model_id or "").strip()
    prov = (provider or "").strip()
    key_map = _model_key_map()

    # Exact model match first.
    if model and model in key_map:
        secret = key_map[model]
        return secret, f"model:{model}"

    # Prefix wildcard match, e.g. "openai/*".
    for pattern, secret in key_map.items():
        if not pattern.endswith("*"):
            continue
        prefix = pattern[:-1]
        if model.startswith(prefix):
            return secret, f"pattern:{pattern}"

    # Provider-scoped key.
    provider_key = f"provider:{prov}"
    if prov and provider_key in key_map:
        secret = key_map[provider_key]
        return secret, provider_key

    # Shared secret fallback.
    shared = (os.getenv("ASSURANCE_SHARED_SECRET") or "").strip()
    if shared:
        return shared, "shared"

    # Last-resort deterministic fallback from existing runtime secrets.
    for env_name in ("OPENROUTER_API_KEY", "DUALSUBSTRATE_API_KEY", "LLM_API_KEY"):
        candidate = (os.getenv(env_name) or "").strip()
        if candidate and candidate.lower() != "dummy":
            return candidate, f"env:{env_name}"
    return "", "none"


def build_assurance_envelope(
    *,
    issuer_model: str,
    issuer_provider: str,
    entity: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    history: list[dict[str, Any]] | None,
    prev_signature: str | None,
    challenge: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build nonce + signature envelope for out-of-band turn assurance."""
    challenge_payload = challenge if isinstance(challenge, dict) else {}
    nonce = str(challenge_payload.get("nonce") or "").strip() or secrets.token_urlsafe(24)
    issued_at_raw = challenge_payload.get("issued_at")
    try:
        issued_at = int(issued_at_raw)
    except (TypeError, ValueError):
        issued_at = int(time.time())
    history_hash = _hash_history(history)

    unsigned_payload: dict[str, Any] = {
        "version": 1,
        "nonce": nonce,
        "issued_at": issued_at,
        "issuer_model": str(issuer_model or "").strip(),
        "issuer_provider": str(issuer_provider or "").strip(),
        "entity": str(entity or "").strip(),
        "session_id": str(session_id or "").strip(),
        "history_hash": history_hash,
        "user_hash": _hash_text(user_message),
        "assistant_hash": _hash_text(assistant_reply),
        "prev_signature": str(prev_signature or ""),
    }

    secret, key_ref = _resolve_secret(unsigned_payload["issuer_model"], unsigned_payload["issuer_provider"])
    signature = (
        hmac.new(secret.encode("utf-8"), _canonical_json(unsigned_payload).encode("utf-8"), hashlib.sha256).hexdigest()
        if secret
        else ""
    )

    envelope = {
        **unsigned_payload,
        "signature": signature,
        "key_ref": key_ref,
    }
    diagnostics = {
        "enabled": bool(secret),
        "key_ref": key_ref,
        "history_hash": history_hash,
        "challenge_used": bool(challenge_payload),
    }
    return envelope, diagnostics


def issue_assurance_challenge(*, session_id: str, turn_count: int, ttl_sec: int) -> dict[str, Any]:
    now = int(time.time())
    ttl = max(int(ttl_sec), 30)
    return {
        "nonce": secrets.token_urlsafe(24),
        "issued_at": now,
        "expires_at": now + ttl,
        "session_id": str(session_id or "").strip(),
        "turn_count": int(turn_count),
    }

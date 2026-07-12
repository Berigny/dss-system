"""Backend assurance verification for model-authenticated turn writes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def hash_history_from_metadata(history_hash: Any) -> str:
    return str(history_hash or "").strip()


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


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

    if model and model in key_map:
        secret = key_map[model]
        return secret, f"model:{model}"

    for pattern, secret in key_map.items():
        if not pattern.endswith("*"):
            continue
        prefix = pattern[:-1]
        if model.startswith(prefix):
            return secret, f"pattern:{pattern}"

    provider_key = f"provider:{prov}"
    if prov and provider_key in key_map:
        return key_map[provider_key], provider_key

    shared = (os.getenv("ASSURANCE_SHARED_SECRET") or "").strip()
    if shared:
        return shared, "shared"

    for env_name in ("OPENROUTER_API_KEY", "DUALSUBSTRATE_API_KEY", "LLM_API_KEY"):
        candidate = (os.getenv(env_name) or "").strip()
        if candidate and candidate.lower() != "dummy":
            return candidate, f"env:{env_name}"
    return "", "none"


def verify_assurance_envelope(
    *,
    envelope: dict[str, Any],
    entity: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    history_hash: str,
    expected_prev_signature: str,
    expected_prev_nonce: str,
    expected_challenge: dict[str, Any] | None = None,
    challenge_required: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    """Verify nonce signature, chain continuity, and tamper-sensitive fields."""
    issuer_model = str(envelope.get("issuer_model") or "").strip()
    issuer_provider = str(envelope.get("issuer_provider") or "").strip()
    signature = str(envelope.get("signature") or "").strip()
    nonce = str(envelope.get("nonce") or "").strip()
    prev_signature = str(envelope.get("prev_signature") or "").strip()

    if not nonce or not signature:
        return False, "missing_signature_fields", {}

    # Replay guard (cheap latest-turn check).
    if expected_prev_nonce and nonce == expected_prev_nonce:
        return False, "nonce_replay_detected", {"nonce": nonce}

    issued_at_raw = envelope.get("issued_at")
    issued_at = _parse_int(issued_at_raw)
    if issued_at is None:
        return False, "invalid_issued_at", {"issued_at": issued_at_raw}
    max_age = int(os.getenv("ASSURANCE_MAX_AGE_SEC", "900"))
    now = int(time.time())
    if issued_at < (now - max_age) or issued_at > (now + 30):
        return False, "stale_or_future_nonce", {"issued_at": issued_at, "now": now}

    # Continuity check against latest persisted turn.
    if expected_prev_signature and prev_signature != expected_prev_signature:
        return False, "previous_signature_mismatch", {
            "expected_prev_signature": expected_prev_signature,
            "provided_prev_signature": prev_signature,
        }

    if challenge_required:
        if not isinstance(expected_challenge, dict):
            return False, "missing_challenge_context", {}
        challenge_nonce = str(expected_challenge.get("nonce") or "").strip()
        if not challenge_nonce:
            return False, "missing_challenge_nonce", {}
        if nonce != challenge_nonce:
            return False, "challenge_nonce_mismatch", {}
        challenge_issued_raw = expected_challenge.get("issued_at")
        challenge_issued = _parse_int(challenge_issued_raw)
        if challenge_issued is None:
            return False, "invalid_challenge_issued_at", {}
        if issued_at != challenge_issued:
            return False, "challenge_issued_at_mismatch", {}
        challenge_expires_raw = expected_challenge.get("expires_at")
        challenge_expires = _parse_int(challenge_expires_raw)
        if challenge_expires is None:
            return False, "invalid_challenge_expires_at", {}
        if now > challenge_expires:
            return False, "challenge_expired", {"expires_at": challenge_expires, "now": now}

    if str(envelope.get("entity") or "").strip() != str(entity or "").strip():
        return False, "entity_mismatch", {}
    if str(envelope.get("session_id") or "").strip() != str(session_id or "").strip():
        return False, "session_mismatch", {}
    if str(envelope.get("history_hash") or "").strip() != str(history_hash or "").strip():
        return False, "history_hash_mismatch", {}
    if str(envelope.get("user_hash") or "").strip() != hash_text(user_message):
        return False, "user_hash_mismatch", {}
    if str(envelope.get("assistant_hash") or "").strip() != hash_text(assistant_reply):
        return False, "assistant_hash_mismatch", {}

    secret, key_ref = _resolve_secret(issuer_model, issuer_provider)
    if not secret:
        return False, "missing_assurance_secret", {"key_ref": key_ref}

    unsigned_payload = {
        "version": envelope.get("version"),
        "nonce": nonce,
        "issued_at": issued_at,
        "issuer_model": issuer_model,
        "issuer_provider": issuer_provider,
        "entity": str(entity or "").strip(),
        "session_id": str(session_id or "").strip(),
        "history_hash": str(history_hash or "").strip(),
        "user_hash": hash_text(user_message),
        "assistant_hash": hash_text(assistant_reply),
        "prev_signature": prev_signature,
    }
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _canonical_json(unsigned_payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return False, "signature_mismatch", {"key_ref": key_ref}

    return True, "verified", {
        "key_ref": key_ref,
        "issuer_model": issuer_model,
        "issuer_provider": issuer_provider,
        "history_hash": history_hash,
    }

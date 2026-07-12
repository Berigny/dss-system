"""Shared coordinate schemas and normalisation utilities."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


class Coordinate(BaseModel):
    """A namespaced coordinate identifier."""

    namespace: str
    identifier: str

    def as_path(self) -> str:
        """Return a stable ``namespace:identifier`` path."""
        return f"{self.namespace}:{self.identifier}"


class LedgerEntrySchema(BaseModel):
    """Minimal ledger entry schema used across apps."""

    coord: Coordinate
    metadata: dict


# ---------------------------------------------------------------------------
# BigInt-safe coordinate encoding
# ---------------------------------------------------------------------------

# JavaScript Number.MAX_SAFE_INTEGER (2^53 - 1). Any integer with an absolute
# value larger than this cannot be represented losslessly as a JSON number in
# standard JavaScript consumers or many SQL numeric types.
JS_MAX_SAFE_INTEGER = 2**53 - 1

# Coordinate fields that are known to hold potentially-large integers. These are
# always emitted as decimal strings on the wire so that receivers never need to
# guess whether a bare number is safe.
_BIGINT_COORDINATE_KEYS: frozenset[str] = frozenset(
    {
        "prime_multiplicative_value",
        "token_prime_product",
        "body_prime",
        "numerator",
        "denominator",
    }
)

# Decimal integer pattern including optional leading minus.
_BIGINT_STRING_PATTERN = re.compile(r"^-?\d+$")


def bigint_str(value: int) -> str:
    """Return a decimal string representation of an arbitrary-precision int."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"bigint_str expects an int, got {type(value).__name__}")
    return str(value)


def parse_bigint(value: Any) -> int:
    """Parse a value that may be an int, a decimal string, or a whole float.

    Raises ``TypeError``/``ValueError`` for non-numeric or lossy inputs.
    """
    if isinstance(value, bool):
        raise TypeError("parse_bigint does not accept booleans")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not _BIGINT_STRING_PATTERN.match(stripped):
            raise ValueError(f"parse_bigint received non-integer string: {value!r}")
        return int(stripped)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"parse_bigint received non-integer float: {value!r}")
    raise TypeError(f"parse_bigint cannot convert {type(value).__name__}")


def _is_unsafe_int(value: Any) -> bool:
    """Return True if ``value`` is a non-bool int outside the JS safe range."""
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) > JS_MAX_SAFE_INTEGER


def sanitize_coordinate_metadata(obj: Any) -> Any:
    """Recursively stringify big-integer coordinate fields for JSON safety.

    - Known coordinate scalar keys are always emitted as strings when their
      value is an int.
    - Any other int value outside the JavaScript safe range is also stringified.
    - All other values pass through unchanged.
    """
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key in _BIGINT_COORDINATE_KEYS:
                if isinstance(value, int) and not isinstance(value, bool):
                    result[key] = str(value)
                else:
                    result[key] = sanitize_coordinate_metadata(value)
            else:
                result[key] = sanitize_coordinate_metadata(value)
        return result
    if isinstance(obj, list):
        return [sanitize_coordinate_metadata(item) for item in obj]
    if _is_unsafe_int(obj):
        return str(obj)
    return obj


def normalize_coordinate_metadata(obj: Any) -> Any:
    """Recursively parse string-encoded coordinate integers back to Python ints.

    Only known coordinate scalar keys are converted; other strings are left as
    strings to avoid accidentally coercing identifiers.
    """
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key in _BIGINT_COORDINATE_KEYS:
                if isinstance(value, str):
                    try:
                        result[key] = int(value)
                    except ValueError:
                        result[key] = value
                else:
                    result[key] = normalize_coordinate_metadata(value)
            else:
                result[key] = normalize_coordinate_metadata(value)
        return result
    if isinstance(obj, list):
        return [normalize_coordinate_metadata(item) for item in obj]
    return obj


def _parse_timestamp(raw_timestamp: Any) -> datetime | None:
    """Parse assorted timestamp representations into a UTC ``datetime``.

    Returns ``None`` when the input cannot be parsed.
    """

    if raw_timestamp in (None, ""):
        return None

    # Try numeric timestamps first (seconds or milliseconds).
    try:
        timestamp_val = float(raw_timestamp)
    except (TypeError, ValueError):
        timestamp_val = None

    if timestamp_val is not None:
        if timestamp_val > 1_000_000_000_000:  # handle millisecond timestamps
            timestamp_val /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp_val, tz=timezone.utc)
        except (OSError, ValueError):
            return None

    # Fall back to parsing string timestamps.
    if isinstance(raw_timestamp, str):
        try:
            normalized = raw_timestamp.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    return None


def _format_timestamp(raw_timestamp: Any) -> tuple[str, str]:
    """Return display and stable timestamp strings.

    The display string is used in the UI, while the stable string is used when
    synthesising coordinates.
    """

    parsed = _parse_timestamp(raw_timestamp)
    if parsed:
        display = parsed.strftime("%d/%m/%Y %H:%M")
        stable = parsed.isoformat()
    else:
        display = "unknown"
        stable = "unknown" if raw_timestamp in (None, "") else str(raw_timestamp)

    return display, stable


def format_coordinate(
    *,
    timestamp: Any,
    coordinate: str | None,
    message_id: Any,
    content: str,
) -> tuple[str, str]:
    """Build coordinate metadata using a provided coordinate or a deterministic mock.

    Returns a 2-tuple of ``(timestamp_display, coordinate_value)``. The
    coordinate value is copied directly if provided, otherwise a mock value is
    generated from the message id, timestamp, and a short content hash to keep
    it stable across renders.
    """

    timestamp_display, timestamp_stable = _format_timestamp(timestamp)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    coordinate_value = coordinate or f"{message_id}:{timestamp_stable}:{content_hash}"

    return timestamp_display, coordinate_value


def _as_dict(value: Any) -> dict:
    """Return ``value`` if it's a dict, otherwise return an empty dict."""
    return value if isinstance(value, dict) else {}


def _coerce_claims(value: Any) -> list:
    """Normalise claims into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_payload_text(payload: dict) -> str | None:
    blobs = payload.get("blobs")
    segments = payload.get("segments")
    if not isinstance(blobs, dict) or not isinstance(segments, list):
        return None
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        blob_ref = segment.get("blob_ref")
        if blob_ref and isinstance(blobs.get(blob_ref), str):
            return blobs[blob_ref]
    return None


def normalize_coordinate_payload(decoded: dict) -> dict:
    """Normalise a backend decoder payload into a stable structure.

    Returns a dict with ``type``, ``coherence``, ``summary``, and ``claims``
    keys (plus ``meta`` and ``content`` for convenience) regardless of the
    incoming shape.
    """

    if not isinstance(decoded, dict):
        raise TypeError("decoded payload must be a dict")

    payload = decoded
    if isinstance(decoded.get("data"), dict):
        payload = decoded["data"]
    elif isinstance(decoded.get("result"), dict):
        payload = decoded["result"]

    meta = _as_dict(payload.get("meta") or decoded.get("meta"))
    content = _as_dict(payload.get("content") or decoded.get("content"))
    skim = _as_dict(payload.get("skim") or decoded.get("skim"))
    interpretation = _as_dict(payload.get("interpretation") or decoded.get("interpretation"))
    governance = _as_dict(payload.get("governance") or decoded.get("governance"))

    normalized_type = (
        payload.get("type")
        or meta.get("type")
        or content.get("type")
    )
    coherence = (
        governance.get("appraisal", {}).get("coherence")
        if isinstance(governance.get("appraisal"), dict)
        else None
    )
    if coherence is None:
        coherence = meta.get("coherence") or payload.get("coherence") or content.get("coherence")

    summary = skim.get("one_line") or content.get("summary") or payload.get("summary")
    payload_text = None
    if isinstance(payload.get("payload"), dict):
        payload_text = _extract_payload_text(payload.get("payload", {}))
    if not summary and payload_text:
        summary = payload_text

    claims = _coerce_claims(interpretation.get("claims") or content.get("claims") or payload.get("claims"))

    policy_version = governance.get("policy_version") or meta.get("policy_version") or "mmf-gov-v2"
    risk_class = governance.get("risk_class") or meta.get("risk_class") or "medium"
    claim_source = governance.get("claim_source") or meta.get("claim_source") or "inferred"
    policy_decision = governance.get("policy_decision") or meta.get("policy_decision") or "allow"

    return {
        "type": normalized_type,
        "coherence": coherence,
        "summary": summary,
        "claims": claims,
        "meta": meta,
        "content": content,
        "governance_contract": {
            "policy_version": str(policy_version),
            "risk_class": str(risk_class),
            "claim_source": str(claim_source),
            "policy_decision": str(policy_decision),
            "grounding_coverage": governance.get("grounding_coverage") or meta.get("grounding_coverage"),
        },
    }

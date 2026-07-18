"""Ledger-scope canonicalization for middleware decode/chat paths.

DSS-280: legacy aliases such as ``loam-root-01``, ``LOAM`` and
``ledger:loam`` must resolve to the canonical short ledger id (``loam``)
before backend calls.  The canonical DID is exposed separately for UI
diagnostics.
"""

from __future__ import annotations


# Canonical DID for well-known ledgers.  The backend still uses the short
# ledger id as its registry key, so middleware forwards ``loam`` to backend
# APIs while displaying the DID in diagnostics.
_LEDGER_CANONICAL_DIDS: dict[str, str] = {
    "loam": "did:web:legacy.local:ledgers:ledger-loam",
}

# Known aliases that must collapse to the canonical short ledger id.
_LEDGER_ALIASES: dict[str, str] = {
    "loam-root-01": "loam",
    "ledger:loam": "loam",
    "ledger:loam-root-01": "loam",
    "loam": "loam",
}


def canonicalize_ledger_scope(value: str | None) -> str:
    """Return the canonical short ledger id for a ledger scope value.

    Strips any ``ledger:`` prefix, lower-cases, and resolves known aliases.
    Unknown values pass through unchanged after normalization.
    """
    text = str(value or "").strip()
    while text.startswith("ledger:"):
        text = text[len("ledger:") :].strip()
    text = text.lower()
    return _LEDGER_ALIASES.get(text, text)


def ledger_scope_to_canonical_did(canonical_id: str) -> str | None:
    """Return the canonical DID for a short ledger id, if known."""
    return _LEDGER_CANONICAL_DIDS.get(canonical_id)


def canonicalize_coordinate_namespace(coordinate: str) -> str:
    """Rewrite a coordinate's namespace through the ledger canonicalizer.

    Only namespace-qualified coordinates are touched; special forms such as
    ``W4-...`` or ``EV-WALK-...`` are returned unchanged.
    """
    raw = str(coordinate or "").strip()
    if ":" not in raw:
        return raw
    namespace, identifier = raw.split(":", 1)
    canonical_namespace = canonicalize_ledger_scope(namespace)
    return f"{canonical_namespace}:{identifier}"

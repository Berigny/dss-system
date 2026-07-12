"""Helpers for normalizing coordinate decoder payloads.

Assumed schema (aligned with backend v2 output):

- The decoder returns a dict in v2 shape, optionally wrapped in ``data`` or ``result``.
- v2 payload includes ``coord``, ``type``, ``skim``, ``payload``, ``interpretation``,
  ``governance``, and ``meta``.
- Legacy shape may include ``meta`` and ``content`` mappings.

Normalization prefers ``decoded['data']`` then ``decoded['result']`` when
present, but safely falls back to the top-level keys. Missing keys yield
``None`` (or an empty list for ``claims``), ensuring stable output for
formatting in the UI.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict:
    """Return ``value`` if it's a dict, otherwise return an empty dict."""

    return value if isinstance(value, dict) else {}


def _coerce_claims(value: Any) -> list:
    """Normalize claims into a list."""

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
    """Normalize a decoder payload into a stable structure.

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

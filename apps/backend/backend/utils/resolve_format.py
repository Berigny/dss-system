from __future__ import annotations

import math
from typing import Any, Iterable

import logging
import os
import re

from backend.api.schemas import ResolveResponseSchema
from backend.utils.coord import normalise_coord
from shared_types.coord_schema import bigint_str, parse_bigint

logger = logging.getLogger(__name__)


def _normalize_part_coord(coord: str) -> str:
    normalized = normalise_coord(coord)
    bare = normalized.get("bare") or coord
    if normalized.get("kind") != "part":
        return coord
    updated = re.sub(r"-P(\d{3})$", r"-T\1", bare)
    if updated == bare:
        return coord
    namespace = normalized.get("namespace")
    return f"{namespace}:{updated}" if namespace else updated


def _tokens_estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _unique_coords(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def coord_type(coord: str) -> str:
    normalized = normalise_coord(coord)
    bare = normalized.get("bare") or coord
    kind = normalized.get("kind")
    modality = normalized.get("modality")
    if kind == "coord_walk":
        return "EV-WALK"
    if kind == "turn":
        return "WX"
    if kind == "event":
        return "EV"
    if kind == "attachment":
        return "ATT"
    if kind == "part":
        if modality == "text":
            return "ATT-T"
        if modality == "image":
            return "ATT-I"
        if modality == "audio":
            return "ATT-A"
        if modality == "video":
            return "ATT-V"
        if modality == "data":
            return "ATT-D"
        if "-P" in bare:
            return "ATT-T"
        return "ATT-T"
    if kind == "overlay":
        if bare.startswith("PL-Conv-"):
            return "PL-Conv"
        if bare.startswith("PL-Claim-"):
            return "PL-Claim"
        if bare.startswith("PL-Taxon-"):
            return "PL-Taxon"
        return "PL"
    if kind == "meta":
        if bare.startswith("MD-Rule-"):
            return "MD-Rule"
        if bare.startswith("MD-Run-"):
            return "MD-Run"
        if bare.startswith("MD-Reset-"):
            return "MD-Reset"
        return "MD"
    if kind == "web4":
        return "W4"
    return "UNK"


def build_refs(
    *,
    coord: str,
    metadata: dict[str, Any] | None = None,
    walk_ids: Iterable[str] | None = None,
    inputs: dict[str, Any] | None = None,
    resolved_coords: Iterable[str] | None = None,
    knowledge_tree: Iterable[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    refs: dict[str, list[dict[str, str]]] = {
        "inputs": [],
        "evidence": [],
        "context": [],
        "overlays": [],
        "governance": [],
        "walk_traces": [],
        "web4": [],
    }

    def _bucket(target: str, value: str) -> None:
        normalized_value = _normalize_part_coord(value)
        refs[target].append({"coord": normalized_value, "type": coord_type(normalized_value)})

    if inputs:
        attachments = inputs.get("attachments") if isinstance(inputs, dict) else None
        parts = inputs.get("parts_used") if isinstance(inputs, dict) else None
        if isinstance(attachments, list):
            for item in attachments:
                if isinstance(item, str):
                    _bucket("inputs", item)
        if isinstance(parts, list):
            for item in parts:
                if isinstance(item, str):
                    _bucket("evidence", item)

    for source in (resolved_coords, knowledge_tree):
        if not source:
            continue
        for item in source:
            if isinstance(item, str):
                ctype = coord_type(item)
                if ctype.startswith("ATT-") and ctype != "ATT":
                    _bucket("evidence", item)
                elif ctype == "ATT":
                    _bucket("inputs", item)
                elif ctype.startswith("PL-"):
                    _bucket("overlays", item)
                elif ctype.startswith("MD-"):
                    _bucket("governance", item)
                elif ctype.startswith("EV"):
                    _bucket("context", item)
                else:
                    _bucket("context", item)

    if walk_ids:
        for item in walk_ids:
            if isinstance(item, str):
                _bucket("walk_traces", item)

    appraisal = (metadata or {}).get("appraisal")
    if isinstance(appraisal, dict):
        source = appraisal.get("source")
        if isinstance(source, str):
            _bucket("governance", source)

    # Deduplicate per bucket
    for key, values in refs.items():
        coords = _unique_coords([item["coord"] for item in values])
        refs[key] = [{"coord": value, "type": coord_type(value)} for value in coords]

    return refs


def build_interpretation(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = metadata or {}
    topics = []
    for item in meta.get("topics") or []:
        if item:
            topics.append({"label": str(item), "score": 0.78})
    claim_source = str(meta.get("claim_source") or (meta.get("appraisal") or {}).get("claim_source") or "inferred")
    grounding_coverage = _coerce_float(meta.get("grounding_coverage"))
    evidence_candidates = _unique_coords(
        str(item).strip()
        for group in (
            meta.get("opened_payload_coords") if isinstance(meta.get("opened_payload_coords"), list) else [],
            meta.get("source_coords") if isinstance(meta.get("source_coords"), list) else [],
            meta.get("resolved_coords") if isinstance(meta.get("resolved_coords"), list) else [],
            meta.get("knowledge_tree") if isinstance(meta.get("knowledge_tree"), list) else [],
            meta.get("walk_ids") if isinstance(meta.get("walk_ids"), list) else [],
        )
        for item in group
        if isinstance(item, str) and str(item).strip()
    )
    if not evidence_candidates and isinstance(meta.get("coord"), str) and str(meta.get("coord")).strip():
        evidence_candidates = [str(meta.get("coord")).strip()]
    claims = []
    for item in meta.get("claims") or []:
        if not item:
            continue
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("claim") or "").strip()
            claim_id = str(item.get("id") or "").strip()
            confidence = _coerce_float(item.get("confidence"))
            explicit_path = item.get("evidence_path") if isinstance(item.get("evidence_path"), list) else []
            evidence_path = _unique_coords(
                str(coord).strip() for coord in explicit_path if isinstance(coord, str) and str(coord).strip()
            ) or list(evidence_candidates[:4])
        else:
            label = str(item).strip()
            claim_id = ""
            confidence = None
            evidence_path = list(evidence_candidates[:4])
        if not label:
            continue
        claim_confidence = confidence if confidence is not None else 0.99
        grounding_status = "grounded"
        if not evidence_path:
            grounding_status = "incomplete"
            claim_confidence = min(claim_confidence, 0.49)
        elif grounding_coverage is not None and grounding_coverage < 0.5:
            grounding_status = "partial"
            claim_confidence = min(claim_confidence, 0.74)
        claims.append(
            {
                "id": claim_id,
                "label": label,
                "confidence": round(claim_confidence, 2),
                "evidence_path": evidence_path,
                "grounding_status": grounding_status,
            }
        )
    tags = [str(item) for item in meta.get("tags") or [] if item]
    return {
        "topics": topics,
        "claims": claims,
        "tags": tags,
        "claim_source": claim_source,
        "grounding_coverage": grounding_coverage,
    }


def build_governance(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = metadata or {}
    appraisal = meta.get("appraisal") if isinstance(meta.get("appraisal"), dict) else {}

    law_raw = appraisal.get("law_score") if appraisal.get("law_score") is not None else appraisal.get("law")
    drift_raw = appraisal.get("drift")
    try:
        law_val = float(law_raw) if law_raw is not None else None
    except (TypeError, ValueError):
        law_val = None
    try:
        drift_val = float(drift_raw) if drift_raw is not None else None
    except (TypeError, ValueError):
        drift_val = None

    # Conservative defaults for MMF governance contract v2.
    policy_version = str(meta.get("policy_version") or appraisal.get("policy_version") or "mmf-gov-v2")
    claim_source = str(meta.get("claim_source") or appraisal.get("claim_source") or "inferred")
    if claim_source not in {"observed", "inferred", "speculative"}:
        claim_source = "inferred"

    risk_class = meta.get("risk_class") or appraisal.get("risk_class")
    if not isinstance(risk_class, str):
        if law_val is None:
            risk_class = "medium"
        elif law_val < 0.4:
            risk_class = "high"
        elif law_val < 0.6:
            risk_class = "medium"
        else:
            risk_class = "low"

    policy_decision = meta.get("policy_decision") or appraisal.get("policy_decision")
    if not isinstance(policy_decision, str):
        if isinstance(meta.get("governance_error"), dict):
            policy_decision = "block"
        elif law_val is not None and law_val < 0.6:
            policy_decision = "degrade"
        else:
            policy_decision = "allow"

    result = {
        "appraisal": {
            "score": appraisal.get("score"),
            "law": appraisal.get("law_score") or appraisal.get("law"),
            "grace": appraisal.get("grace_score") or appraisal.get("grace"),
            "drift": appraisal.get("drift"),
            "coherence": meta.get("coherence"),
            "gravity_cost": meta.get("gravity_cost"),
            "safety_score": meta.get("safety_score"),
            "source": appraisal.get("source"),
        },
        "policy_version": policy_version,
        "risk_class": str(risk_class),
        "claim_source": claim_source,
        "policy_decision": policy_decision,
        "grounding_coverage": meta.get("grounding_coverage"),
    }

    if not appraisal:
        result["appraisal"] = {}
    return result


def build_coord_meta(metadata: dict[str, Any] | None, base_meta: dict[str, Any]) -> dict[str, Any]:
    meta = dict(base_meta or {})
    source = metadata or {}
    runtime_identity = source.get("runtime_identity") if isinstance(source.get("runtime_identity"), dict) else {}
    coord_meta_payload = source.get("coord_meta") if isinstance(source.get("coord_meta"), dict) else {}
    if coord_meta_payload:
        meta.update({
            key: value
            for key, value in coord_meta_payload.items()
            if value is not None and value != ""
        })
    if runtime_identity:
        ledger_canonical_subject = runtime_identity.get("ledger_canonical_subject")
        if isinstance(ledger_canonical_subject, str) and ledger_canonical_subject.strip():
            meta.setdefault("canonical_subject", ledger_canonical_subject.strip())
            meta.setdefault("canonical_subject_source", "did:web:ledger")
        runtime_namespace = runtime_identity.get("runtime_namespace")
        if isinstance(runtime_namespace, str) and runtime_namespace.strip():
            meta.setdefault("runtime_namespace", runtime_namespace.strip())
        principal_canonical_subject = runtime_identity.get("principal_canonical_subject")
        if isinstance(principal_canonical_subject, str) and principal_canonical_subject.strip():
            meta.setdefault("principal_canonical_subject", principal_canonical_subject.strip())
        principal_did = runtime_identity.get("principal_did")
        if isinstance(principal_did, str) and principal_did.strip():
            meta.setdefault("principal_did", principal_did.strip())
        vc_refs = runtime_identity.get("vc_refs")
        if isinstance(vc_refs, dict) and vc_refs:
            meta.setdefault("vc_refs", dict(vc_refs))
        library_boundary = runtime_identity.get("library_boundary")
        if isinstance(library_boundary, dict) and library_boundary:
            meta.setdefault("library_boundary", dict(library_boundary))
    taxonomy_provenance = source.get("taxonomy_provenance") if isinstance(source.get("taxonomy_provenance"), dict) else {}
    prime_multiplicative = source.get("token_prime_product")
    token_primes = source.get("token_primes")
    lattice_exponents = source.get("prime_lattice_exponents")
    normalized_primes: list[int] = []
    if isinstance(token_primes, list):
        for value in token_primes:
            if isinstance(value, (int, float, str)) and str(value).lstrip("-").isdigit():
                try:
                    normalized_primes.append(parse_bigint(value))
                except (TypeError, ValueError):
                    continue
    elif isinstance(lattice_exponents, dict):
        for p in lattice_exponents.keys():
            try:
                normalized_primes.append(parse_bigint(p))
            except (TypeError, ValueError):
                continue
    if normalized_primes:
        meta["token_primes"] = normalized_primes
    if prime_multiplicative is None and normalized_primes:
        prime_multiplicative = math.prod(normalized_primes)
    elif isinstance(prime_multiplicative, (int, float, str)):
        try:
            prime_multiplicative = parse_bigint(prime_multiplicative)
        except (TypeError, ValueError):
            prime_multiplicative = None
    if isinstance(prime_multiplicative, int):
        meta["prime_multiplicative_value"] = bigint_str(prime_multiplicative)
        meta["token_prime_product"] = bigint_str(prime_multiplicative)
    body_prime = source.get("body_prime")
    if isinstance(body_prime, (int, float, str)):
        try:
            body_prime = parse_bigint(body_prime)
        except (TypeError, ValueError):
            body_prime = None
    if isinstance(body_prime, int):
        meta["body_prime"] = bigint_str(body_prime)
    taxonomy_topology_ref = source.get("taxonomy_topology_ref")
    if not (isinstance(taxonomy_topology_ref, str) and taxonomy_topology_ref.strip()):
        taxonomy_topology_ref = taxonomy_provenance.get("topology_ref")
    if isinstance(taxonomy_topology_ref, str) and taxonomy_topology_ref.strip():
        meta["taxonomy_topology_ref"] = taxonomy_topology_ref.strip()
    taxonomy_mode = source.get("taxonomy_mode")
    if not (isinstance(taxonomy_mode, str) and taxonomy_mode.strip()):
        taxonomy_mode = taxonomy_provenance.get("taxonomy_mode")
    if isinstance(taxonomy_mode, str) and taxonomy_mode.strip():
        meta["taxonomy_mode"] = taxonomy_mode.strip()
    configurational_foresight = source.get("configurational_foresight")
    if isinstance(configurational_foresight, dict) and configurational_foresight:
        meta["configurational_foresight"] = dict(configurational_foresight)
    return meta


def build_skim(
    *,
    coord: str,
    metadata: dict[str, Any] | None,
    refs_count: int,
    coord_type_value: str,
) -> dict[str, Any]:
    meta = metadata or {}
    summary = meta.get("summary") or meta.get("attachment_summary") or ""
    text = meta.get("content") or meta.get("text") or summary or ""
    preview = str(text).strip()
    if not summary and meta.get("summary_pending"):
        one_line = "Summary pending. Open attachment parts for details."
    else:
        one_line = preview[:200] if preview else "No summary available."
    relevance = 0.0
    appraisal = meta.get("appraisal")
    if isinstance(appraisal, dict) and isinstance(appraisal.get("score"), (int, float)):
        relevance = float(appraisal["score"])
    elif isinstance(meta.get("relevance"), (int, float)):
        relevance = float(meta["relevance"])
    else:
        relevance = 0.5

    recommended: list[str] = []
    if coord_type_value == "ATT":
        recommended.append("open:att_parts")
    elif refs_count:
        recommended.append("walk")
    if relevance >= 0.7:
        recommended.append("open:answer")
    if not recommended:
        recommended.append("skip")

    return {
        "one_line": one_line,
        "relevance": round(relevance, 4),
        "reasons": [f"type:{coord_type_value}", f"refs:{refs_count}"],
        "recommended": recommended,
        "budgets": {"walk_k": 3, "max_tokens_load": 350},
    }


def build_payload_for_text(coord_type_value: str, text: str) -> dict[str, Any]:
    segment_id = "ANS-01"
    blob_ref = f"BLOB:{coord_type_value}:{segment_id}"
    return {
        "segments": [
            {
                "id": segment_id,
                "kind": "answer",
                "blob_ref": blob_ref,
                "tokens_est": _tokens_estimate(text),
            }
        ],
        "blobs": {blob_ref: text},
    }


def build_payload_for_parts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"parts": parts}


def build_payload_for_blob(text: str, *, coordinate: str | None = None) -> dict[str, Any]:
    """Return a payload envelope for the intact full-payload blob tier."""
    return {
        "type": "blob_full",
        "coordinate": coordinate,
        "text": text,
        "tokens_est": _tokens_estimate(text),
    }


def build_payload_for_projections(projections: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a payload envelope for the kernel semantic projection tier."""
    return {
        "type": "kernel_projections",
        "projections": list(projections),
        "count": len(projections),
    }


def resolve_response(
    *,
    coord: str,
    metadata: dict[str, Any] | None,
    payload: dict[str, Any],
    refs: dict[str, list[dict[str, str]]],
    walk: dict[str, Any] | None,
    interpretation: dict[str, Any],
    governance: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    normalized_coord = _normalize_part_coord(coord)
    coord_type_value = coord_type(normalized_coord)
    refs_count = sum(len(values) for values in refs.values())
    skim = build_skim(
        coord=normalized_coord,
        metadata=metadata,
        refs_count=refs_count,
        coord_type_value=coord_type_value,
    )
    response = {
        "coord": normalized_coord,
        "type": coord_type_value,
        "skim": skim,
        "walk": walk,
        "refs": refs,
        "payload": payload,
        "interpretation": interpretation,
        "governance": governance,
        "meta": build_coord_meta(metadata, meta),
    }
    validate_resolve_response(response)
    return response


def validate_resolve_response(response: dict[str, Any]) -> None:
    strict = os.getenv("RESOLVE_SCHEMA_STRICT", "").lower() in {"1", "true", "yes", "on"}
    try:
        ResolveResponseSchema.model_validate(response)
    except Exception as exc:
        if strict:
            raise
        logger.warning("Resolve response schema validation failed: %s", exc)

"""Projection lane endpoints for MMF kernel/domain evaluation."""

from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.fieldx_kernel.eval_ladder import evaluate_eq_ladder
from backend.fieldx_kernel.mmf_foundation import (
    CANONICAL_MMF_PRIME_TOPOLOGIES,
    MMF_DOMAIN_SET,
    MMF_KERNEL_NODES,
    PacketBoundary,
    evaluate_e6_decision,
    mmf_required_extension_prime_count,
)

router = APIRouter(prefix="/projection", tags=["projection"])


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _claim_from_request(request: Request, *, state_attr: str, headers: tuple[str, ...]) -> str:
    state = getattr(request, "state", None)
    if state is not None:
        value = getattr(state, state_attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for header in headers:
        value = request.headers.get(header)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _projection_override_authorized(
    request: Request,
    payload: "MMFProjectionEvaluateRequest",
) -> tuple[bool, dict[str, Any]]:
    policy_allow = _bool_env("PROJECTION_POLICY_ALLOW_CLIENT_OVERRIDES", False)
    authz = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token_present = authz.strip().lower().startswith("bearer ")

    principal_did = _claim_from_request(
        request,
        state_attr="auth_claim_principal_did",
        headers=("x-principal-did", "x-did"),
    ) or str(payload.principal_did or "").strip()

    session_jti = _claim_from_request(
        request,
        state_attr="auth_claim_session_jti",
        headers=("x-session-jti", "x-auth-jti"),
    ) or str(payload.session_jti or "").strip()

    principal_key_id = _claim_from_request(
        request,
        state_attr="auth_claim_principal_key_id",
        headers=("x-principal-key-id", "x-key-id"),
    ) or str(payload.principal_key_id or "").strip()

    override_authorized = bool(policy_allow and token_present and principal_did and session_jti)
    claims = {
        "principal_did": principal_did or None,
        "principal_key_id": principal_key_id or None,
        "session_jti": session_jti or None,
    }
    return override_authorized, claims


def _trust_class_from_projection_claims(
    *,
    provenance_status: str,
    replay_protected: bool,
) -> str:
    status = str(provenance_status or "").strip().lower()
    if status == "session_token":
        return "T3" if replay_protected else "T2"
    if status == "principal_only":
        return "T2"
    return "T0"


def _eq9_posture_class_from_contract(eval_contract: Dict[str, Any]) -> str:
    if bool(eval_contract.get("blocked")):
        return "P0"
    eq9 = eval_contract.get("eq9_metrics")
    if not isinstance(eq9, dict):
        return "P1"
    try:
        ypt = float(eq9.get("yield_per_token") or 0.0)
    except (TypeError, ValueError):
        ypt = 0.0
    try:
        prov_conf = float(eq9.get("provenance_confidence") or 0.0)
    except (TypeError, ValueError):
        prov_conf = 0.0
    replay = bool(eq9.get("replay_protected"))
    if ypt < 0.001:
        return "P1"
    if ypt < 0.01:
        return "P2"
    return "P3" if prov_conf >= 0.9 and replay else "P2"


def _build_projection_posture_policy(
    *,
    eval_contract: Dict[str, Any],
    trust_class: str,
) -> Dict[str, Any]:
    failed_eq = eval_contract.get("failed_eq")
    reason_code = "baseline_satisfied"
    decision = "allow"
    if bool(eval_contract.get("blocked")):
        decision = "deny"
        reason_code = f"eq_blocked:{failed_eq}" if isinstance(failed_eq, str) and failed_eq else "eq_blocked"
    elif trust_class == "T0":
        decision = "degrade"
        reason_code = "trust_floor_degraded"
    repairs = []
    raw_repairs = eval_contract.get("repair_actions")
    if isinstance(raw_repairs, list):
        for item in raw_repairs:
            if isinstance(item, dict):
                text = str(item.get("action") or "").strip()
                if text:
                    repairs.append(text)
    return {
        "policy_gate_version": "policy-gate-v1",
        "policy_decision": decision,
        "reason_code": reason_code,
        "trust_class": trust_class,
        "eq9_posture_class": _eq9_posture_class_from_contract(eval_contract),
        "failed_eq": failed_eq,
        "repair_actions": repairs,
        "action": "projection.evaluate",
    }


class MMFProjectionEvaluateRequest(BaseModel):
    domain: str
    node: str = "S1-N0"
    mode: int = Field(default=2, ge=0, le=3)
    K: int = Field(default=1)
    P: int = Field(default=1)
    E: int = Field(default=1)
    V_q: int = Field(default=0)
    momentum_min: int = Field(default=0)
    seq: int = Field(default=0)
    t_ms: int | None = None
    dW: int = Field(default=0)
    projection_version: str = "mmf-projection-v1"
    source_event_id: str | None = None
    payload_hash: str | None = None
    output_tokens_est: int | None = None
    law_score: float = 1.0
    grace_score: float = 1.0
    principal_did: str | None = None
    principal_key_id: str | None = None
    session_jti: str | None = None

    @field_validator("domain")
    def _validate_domain(cls, value: str) -> str:
        domain = str(value).strip().lower()
        if domain not in MMF_DOMAIN_SET:
            raise ValueError(f"unsupported domain '{value}'")
        return domain

    @field_validator("node")
    def _validate_node(cls, value: str) -> str:
        node = str(value).strip()
        if node not in MMF_KERNEL_NODES:
            raise ValueError(f"unsupported node '{value}'")
        return node

    @field_validator("K", "P", "E")
    def _validate_gate_flag(cls, value: int) -> int:
        ivalue = int(value)
        if ivalue not in (0, 1):
            raise ValueError("gate flags must be 0 or 1")
        return ivalue


@router.get("/mmf/topologies")
def mmf_topologies() -> Dict[str, Any]:
    topologies = CANONICAL_MMF_PRIME_TOPOLOGIES
    return {
        "projection_version": "mmf-projection-v1",
        "domain_count": len(MMF_DOMAIN_SET),
        "topology_count": len(topologies),
        "required_domain_primes": mmf_required_extension_prime_count(
            domains=6, anchors_per_domain=0, cube_size=8
        ),
        "taxonomy_mode": "indefeasible",
        "topologies": {
            key: {
                "topology": value.topology,
                "anchor_nodes": list(value.anchor_nodes),
                "anchor_primes": list(value.anchor_primes),
                "extension_primes": list(value.extension_primes),
                "cube_primes": list(value.cube_primes),
            }
            for key, value in topologies.items()
        },
    }


@router.post("/mmf/evaluate")
def mmf_evaluate(payload: MMFProjectionEvaluateRequest, request: Request) -> Dict[str, Any]:
    topologies = CANONICAL_MMF_PRIME_TOPOLOGIES
    domain_topology = topologies.get(payload.domain)
    if domain_topology is None:
        raise HTTPException(status_code=400, detail="invalid domain topology")

    override_authorized, claims = _projection_override_authorized(request, payload)
    rejected_overrides: list[str] = []

    effective_mode = int(payload.mode)
    if effective_mode != 2 and not override_authorized:
        rejected_overrides.append("projection_mode_override_rejected")
        effective_mode = 2

    effective_K = int(payload.K)
    if effective_K == 0 and not override_authorized:
        rejected_overrides.append("projection_K_override_rejected")
        effective_K = 1

    effective_P = int(payload.P)
    if effective_P == 0 and not override_authorized:
        rejected_overrides.append("projection_P_override_rejected")
        effective_P = 1

    effective_E = int(payload.E)
    if effective_E == 0 and not override_authorized:
        rejected_overrides.append("projection_E_override_rejected")
        effective_E = 1

    node_index = MMF_KERNEL_NODES.index(payload.node)
    decision = evaluate_e6_decision(
        mode=effective_mode,
        K=effective_K,
        P=effective_P,
        E=effective_E,
        V_q=payload.V_q,
        momentum_min=payload.momentum_min,
    )

    packetizer = PacketBoundary(default_node=node_index)
    t_ms = int(payload.t_ms if payload.t_ms is not None else int(time.time() * 1000) % 0x1000000)
    header = packetizer.pack(decision=decision, seq=payload.seq, t_ms=t_ms, dW=payload.dW, node=node_index)
    header_decoded = packetizer.unpack(header)

    has_principal = bool(claims.get("principal_did"))
    has_session = bool(claims.get("session_jti"))
    if has_principal and has_session:
        provenance_status = "session_token"
        provenance_confidence = 1.0
    elif has_principal:
        provenance_status = "principal_only"
        provenance_confidence = 0.7
    else:
        provenance_status = "anonymous"
        provenance_confidence = 0.3

    eval_contract = evaluate_eq_ladder(
        mode=effective_mode,
        K=effective_K,
        P=effective_P,
        E=effective_E,
        V_q=int(payload.V_q),
        momentum_min=int(payload.momentum_min),
        dW=int(payload.dW),
        output_tokens_est=payload.output_tokens_est,
        law_score=float(payload.law_score),
        grace_score=float(payload.grace_score),
        provenance_confidence=provenance_confidence,
        replay_protected=has_session,
        provenance_status=provenance_status,
    )
    trust_class = _trust_class_from_projection_claims(
        provenance_status=provenance_status,
        replay_protected=has_session,
    )
    posture_policy = _build_projection_posture_policy(
        eval_contract=eval_contract,
        trust_class=trust_class,
    )

    policy_controls = {
        "override_authorized": override_authorized,
        "requested_mode": int(payload.mode),
        "effective_mode": effective_mode,
        "requested_gates": {"K": int(payload.K), "P": int(payload.P), "E": int(payload.E)},
        "effective_gates": {"K": effective_K, "P": effective_P, "E": effective_E},
        "rejected_overrides": rejected_overrides,
        "auth_claims": claims,
    }

    return {
        "projection_version": payload.projection_version,
        "domain": payload.domain,
        "node": payload.node,
        "source_event_id": payload.source_event_id,
        "payload_hash": payload.payload_hash,
        "topology": {
            "anchor_nodes": list(domain_topology.anchor_nodes),
            "anchor_primes": list(domain_topology.anchor_primes),
            "extension_primes": list(domain_topology.extension_primes),
            "cube_primes": list(domain_topology.cube_primes),
            "taxonomy_mode": "indefeasible",
        },
        "decision": decision,
        "eval_contract": eval_contract,
        "posture_policy": posture_policy,
        "policy_controls": policy_controls,
        "header128": header.hex(),
        "header": header_decoded,
        "commit": bool(decision.get("commit")),
    }

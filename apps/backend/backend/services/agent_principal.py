"""Agent principal bootstrap and principal-connection graph for pilot onboarding."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.services.pilot_onboarding import (
    _current_principal_did_or_raise,
    _load_pilot_signups,
    _signup_for_principal,
)
from backend.services.pilot_provisioning import get_provisioning_job_for_record
from backend.services.model_library import _load_model_principals


PILOT_AGENT_PRINCIPALS_V1_KEY = b"__pilot_agent_principals_v1__"
PILOT_PRINCIPAL_CONNECTIONS_V1_KEY = b"__pilot_principal_connections_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_agent_principals(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(PILOT_AGENT_PRINCIPALS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            out[str(key)] = dict(record)
    return out


def _persist_agent_principals(
    db: Any, records: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[PILOT_AGENT_PRINCIPALS_V1_KEY] = json.dumps(
        {"version": 1, "agents": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_principal_connections(db: Any) -> dict[str, list[dict[str, Any]]]:
    raw = db.get(PILOT_PRINCIPAL_CONNECTIONS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("connections") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for key, record_list in records.items():
        if isinstance(record_list, list):
            out[str(key)] = [dict(r) for r in record_list if isinstance(r, dict)]
    return out


def _persist_principal_connections(
    db: Any, records: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    canonical: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(records.keys()):
        record_list = records.get(key)
        if isinstance(record_list, list):
            canonical[key] = [dict(r) for r in record_list if isinstance(r, dict)]
    db[PILOT_PRINCIPAL_CONNECTIONS_V1_KEY] = json.dumps(
        {"version": 1, "connections": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _agent_principal_id(account_id: str) -> str:
    digest = hashlib.sha256(f"{account_id}:agent".encode("utf-8")).hexdigest()[:16]
    return f"agent_principal:{digest}"


def _edge_id(source: str, target: str, relation: str) -> str:
    digest = hashlib.sha256(f"{source}:{target}:{relation}".encode("utf-8")).hexdigest()[:16]
    return f"conn:{digest}"


def _get_provisioning_step(job: dict[str, Any] | None, step_id: str) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
    for step in steps:
        if isinstance(step, dict) and step.get("step_id") == step_id:
            return step
    return None


def bootstrap_agent_principal(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signup_id, record = _signup_for_principal(db, principal_did)
    account_id = str(record.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=409, detail={"error": "account_id_missing"})

    job = get_provisioning_job_for_record(db, record)
    if not isinstance(job, dict):
        raise HTTPException(status_code=409, detail={"error": "provisioning_not_complete"})

    # Extract owner principal id from provisioning job
    owner_step = _get_provisioning_step(job, "owner_human_principal")
    owner_principal_id = owner_step.get("resource_id") if isinstance(owner_step, dict) else None
    if not owner_principal_id:
        raise HTTPException(status_code=409, detail={"error": "owner_principal_missing"})

    # Extract ledger id from provisioning job
    ledger_step = _get_provisioning_step(job, "ledger_runtime")
    ledger_id = ledger_step.get("resource_id") if isinstance(ledger_step, dict) else None
    if not ledger_id:
        raise HTTPException(status_code=409, detail={"error": "ledger_missing"})

    # Find the active model principal
    model_principals = _load_model_principals(db).get(account_id, [])
    active_model = next(
        (mp for mp in model_principals if mp.get("status") == "active"),
        None,
    )
    if not active_model:
        raise HTTPException(status_code=409, detail={"error": "model_principal_not_selected"})
    model_principal_id = active_model.get("principal_id")

    # Idempotent agent principal creation
    agents = _load_agent_principals(db)
    existing = agents.get(account_id)
    if isinstance(existing, dict):
        return {
            "status": "ok",
            "agent_principal": _agent_principal_summary(existing),
            "idempotent_replay": True,
        }

    agent_principal_id = _agent_principal_id(account_id)
    now = _now_iso()
    agent = {
        "principal_id": agent_principal_id,
        "principal_type": "agent",
        "owner_principal_id": owner_principal_id,
        "model_principal_id": model_principal_id,
        "account_id": account_id,
        "ledger_id": ledger_id,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    agents[account_id] = agent
    _persist_agent_principals(db, agents)

    # Build connection graph
    connections = _load_principal_connections(db)
    account_connections = connections.get(account_id, [])

    # Extract surface ids from provisioning job
    chat_step = _get_provisioning_step(job, "chat_surface")
    share_step = _get_provisioning_step(job, "share_surface")
    chat_surface_id = chat_step.get("resource_id") if isinstance(chat_step, dict) else None
    share_surface_id = share_step.get("resource_id") if isinstance(share_step, dict) else None

    new_edges = [
        {
            "edge_id": _edge_id(owner_principal_id, agent_principal_id, "owns"),
            "source_principal_id": owner_principal_id,
            "target_principal_id": agent_principal_id,
            "relation_type": "owns",
            "account_id": account_id,
            "ledger_id": ledger_id,
            "status": "active",
        },
        {
            "edge_id": _edge_id(agent_principal_id, model_principal_id, "acts_through"),
            "source_principal_id": agent_principal_id,
            "target_principal_id": model_principal_id,
            "relation_type": "acts_through",
            "account_id": account_id,
            "ledger_id": ledger_id,
            "status": "active",
        },
        {
            "edge_id": _edge_id(agent_principal_id, ledger_id, "bound_to"),
            "source_principal_id": agent_principal_id,
            "target_principal_id": ledger_id,
            "relation_type": "bound_to",
            "account_id": account_id,
            "ledger_id": ledger_id,
            "status": "active",
        },
    ]
    if chat_surface_id:
        new_edges.append(
            {
                "edge_id": _edge_id(agent_principal_id, chat_surface_id, "can_use"),
                "source_principal_id": agent_principal_id,
                "target_principal_id": chat_surface_id,
                "relation_type": "can_use",
                "account_id": account_id,
                "ledger_id": ledger_id,
                "status": "active",
                "surface_type": "chat",
            }
        )
    if share_surface_id:
        new_edges.append(
            {
                "edge_id": _edge_id(agent_principal_id, share_surface_id, "can_use"),
                "source_principal_id": agent_principal_id,
                "target_principal_id": share_surface_id,
                "relation_type": "can_use",
                "account_id": account_id,
                "ledger_id": ledger_id,
                "status": "active",
                "surface_type": "share_decode",
            }
        )

    # Replace existing edges for this agent to maintain idempotence
    existing_edge_ids = {e["edge_id"] for e in account_connections}
    for edge in new_edges:
        if edge["edge_id"] not in existing_edge_ids:
            account_connections.append(edge)

    connections[account_id] = account_connections
    _persist_principal_connections(db, connections)

    return {
        "status": "ok",
        "agent_principal": _agent_principal_summary(agent),
        "idempotent_replay": False,
    }


def _agent_principal_summary(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal_id": agent.get("principal_id"),
        "principal_type": agent.get("principal_type"),
        "owner_principal_id": agent.get("owner_principal_id"),
        "model_principal_id": agent.get("model_principal_id"),
        "account_id": agent.get("account_id"),
        "ledger_id": agent.get("ledger_id"),
        "status": agent.get("status"),
        "created_at": agent.get("created_at"),
    }


def get_principal_connections(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    account_id = str(record.get("account_id") or "").strip()

    connections = _load_principal_connections(db).get(account_id, [])
    agents = _load_agent_principals(db)
    agent = agents.get(account_id)

    return {
        "status": "ok",
        "account_id": account_id,
        "agent_principal": _agent_principal_summary(agent) if isinstance(agent, dict) else None,
        "connections": connections,
    }


def get_current_principals_with_agent(request: Request, db: Any) -> dict[str, Any]:
    """Return all principals including owner, model, and agent principals."""
    from backend.services.model_library import get_current_principals as get_model_principals

    result = get_model_principals(request, db)
    account_id = result.get("account_id")
    agents = _load_agent_principals(db)
    agent = agents.get(account_id) if account_id else None
    if isinstance(agent, dict):
        result["principals"].append(_agent_principal_summary(agent))
    return result

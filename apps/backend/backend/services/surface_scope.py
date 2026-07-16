"""Surface-to-ledger and principal-to-surface authority helpers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request

from backend.services.authz import Principal, principal_from_request

SURFACE_REGISTRY_V1_KEY = b"__surfaces_v1__"
RELATIONSHIP_REGISTRY_V1_KEY = b"__relationships_v1__"


def _decode_json(raw: Any) -> Any:
    if raw is None:
        return None
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_surfaces(request: Request) -> dict[str, dict[str, Any]]:
    db = getattr(getattr(request, "app", None), "state", None)
    store = getattr(db, "db", None) if db is not None else None
    if store is None:
        return {}
    raw = store.get(SURFACE_REGISTRY_V1_KEY)
    decoded = _decode_json(raw)
    records = decoded.get("surfaces") if isinstance(decoded, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(surface_id): dict(record) for surface_id, record in records.items() if isinstance(record, dict)}


def _load_relationships(request: Request) -> list[dict[str, Any]]:
    db = getattr(getattr(request, "app", None), "state", None)
    store = getattr(db, "db", None) if db is not None else None
    if store is None:
        return []
    raw = store.get(RELATIONSHIP_REGISTRY_V1_KEY)
    decoded = _decode_json(raw)
    records = decoded.get("relationships") if isinstance(decoded, dict) else None
    if isinstance(records, list):
        return [dict(record) for record in records if isinstance(record, dict)]
    if isinstance(records, dict):
        return [dict(record) for record in records.values() if isinstance(record, dict)]
    return []


def _is_active(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    enabled = str(record.get("enabled_state") or "enabled").strip().lower()
    return status in {"active", "approved", "accepted"} and enabled in {"enabled", "active"}


def _surface_bound_to_ledger(surface: dict[str, Any], ledger_id: str) -> bool:
    surface_ledger = str(surface.get("ledger_id") or "").strip()
    if surface_ledger and surface_ledger.lower() == ledger_id.lower():
        return True
    return False


def _principal_can_access_surface(
    principal: Principal,
    surface: dict[str, Any],
    relationships: list[dict[str, Any]],
) -> bool:
    surface_principal = str(surface.get("principal_did") or "").strip()
    if surface_principal and principal.principal_did and surface_principal == principal.principal_did:
        return True
    surface_id = str(surface.get("surface_id") or "").strip()
    principal_did = principal.principal_did or ""
    principal_id = principal.principal_id or ""
    for rel in relationships:
        if not _is_active(rel):
            continue
        subject_type = str(rel.get("subject_entity_type") or "").strip().lower()
        subject_id = str(rel.get("subject_entity_id") or "").strip()
        object_type = str(rel.get("object_entity_type") or "").strip().lower()
        object_id = str(rel.get("object_entity_id") or "").strip()
        rel_type = str(rel.get("relationship_type") or "").strip().lower()
        if subject_type != "principal" or object_type != "surface":
            continue
        if object_id != surface_id:
            continue
        if rel_type not in {"accesses", "can_access_surface", "member_of"}:
            continue
        if subject_id and subject_id in {principal_did, principal_id}:
            return True
    return False


def assert_surface_ledger_access(
    request: Request,
    surface_id: str,
    ledger_id: str,
) -> None:
    """Raise 403 if the caller is not authorised for this surface/ledger pair.

    Authorisation requires:
    - the surface is active and bound to the requested ledger; and
    - the authenticated principal can access the surface.

    If the surface record does not exist, this is a no-op so that deployments
    without explicit surface registry entries keep working.
    """
    surfaces = _load_surfaces(request)
    surface = surfaces.get(surface_id)
    if surface is None:
        return

    if not _is_active(surface):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "surface_inactive",
                "surface_id": surface_id,
                "reason": "Surface is not active or enabled.",
            },
        )

    if not _surface_bound_to_ledger(surface, ledger_id):
        relationships = _load_relationships(request)
        found_surface_ledger_link = False
        for rel in relationships:
            if not _is_active(rel):
                continue
            subject_type = str(rel.get("subject_entity_type") or "").strip().lower()
            subject_id = str(rel.get("subject_entity_id") or "").strip()
            object_type = str(rel.get("object_entity_type") or "").strip().lower()
            object_id = str(rel.get("object_entity_id") or "").strip()
            rel_type = str(rel.get("relationship_type") or "").strip().lower()
            if subject_type != "surface" or object_type != "ledger":
                continue
            if subject_id != surface_id:
                continue
            if rel_type not in {"belongs_to", "surface_bound_to_ledger", "member_of"}:
                continue
            if object_id.lower() == ledger_id.lower():
                found_surface_ledger_link = True
                break
        if not found_surface_ledger_link:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "surface_not_bound_to_ledger",
                    "surface_id": surface_id,
                    "ledger_id": ledger_id,
                    "reason": "Surface is not bound to the requested ledger.",
                },
            )

    principal = principal_from_request(request)
    if not principal.principal_did:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "decode_requires_authenticated_principal",
                "surface_id": surface_id,
                "reason": "Decode through a surface requires an authenticated principal.",
            },
        )

    relationships = _load_relationships(request)
    if not _principal_can_access_surface(principal, surface, relationships):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "principal_not_authorized_for_surface",
                "surface_id": surface_id,
                "principal_did": principal.principal_did,
                "reason": "Principal is not linked to the surface.",
            },
        )

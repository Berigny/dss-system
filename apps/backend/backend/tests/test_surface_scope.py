from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.services.surface_scope import (
    SURFACE_REGISTRY_V1_KEY,
    RELATIONSHIP_REGISTRY_V1_KEY,
    assert_surface_ledger_access,
)


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    db: dict[bytes, bytes] | None = None,
) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("utf-8"), str(value).encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat/web4/decode",
        "query_string": b"",
        "headers": raw_headers,
        "app": SimpleNamespace(state=SimpleNamespace(db=db or {})),
    }
    return Request(scope)


def _surface_registry(*surfaces: dict[str, object]) -> dict[bytes, bytes]:
    payload = {
        "version": 1,
        "surfaces": {surface["surface_id"]: surface for surface in surfaces},
    }
    return {SURFACE_REGISTRY_V1_KEY: json.dumps(payload).encode("utf-8")}


def _relationship_registry(*relationships: dict[str, object]) -> dict[bytes, bytes]:
    payload = {
        "version": 1,
        "relationships": list(relationships),
    }
    return {RELATIONSHIP_REGISTRY_V1_KEY: json.dumps(payload).encode("utf-8")}


def test_missing_surface_record_is_no_op() -> None:
    req = _make_request(headers={"x-principal-did": "did:key:z6MkOwner"})
    assert_surface_ledger_access(req, "surface:coord-demo", "loam")


def test_surface_owner_bound_to_ledger_is_allowed() -> None:
    db = _surface_registry(
        {
            "surface_id": "surface:coord-demo",
            "ledger_id": "loam",
            "principal_did": "did:key:z6MkOwner",
            "status": "active",
            "enabled_state": "enabled",
        }
    )
    req = _make_request(headers={"x-principal-did": "did:key:z6MkOwner"}, db=db)
    assert_surface_ledger_access(req, "surface:coord-demo", "loam")


def test_surface_linked_to_ledger_via_relationship_is_allowed() -> None:
    db = {
        **_surface_registry(
            {
                "surface_id": "surface:coord-demo",
                "status": "active",
                "enabled_state": "enabled",
            }
        ),
        **_relationship_registry(
            {
                "subject_entity_type": "surface",
                "subject_entity_id": "surface:coord-demo",
                "object_entity_type": "ledger",
                "object_entity_id": "loam",
                "relationship_type": "belongs_to",
                "status": "active",
                "enabled_state": "enabled",
            },
            {
                "subject_entity_type": "principal",
                "subject_entity_id": "did:key:z6MkUser",
                "object_entity_type": "surface",
                "object_entity_id": "surface:coord-demo",
                "relationship_type": "accesses",
                "status": "active",
                "enabled_state": "enabled",
            },
        ),
    }
    req = _make_request(headers={"x-principal-did": "did:key:z6MkUser"}, db=db)
    assert_surface_ledger_access(req, "surface:coord-demo", "loam")


def test_principal_linked_via_relationship_is_allowed() -> None:
    db = {
        **_surface_registry(
            {
                "surface_id": "surface:coord-demo",
                "ledger_id": "loam",
                "status": "active",
                "enabled_state": "enabled",
            }
        ),
        **_relationship_registry(
            {
                "subject_entity_type": "principal",
                "subject_entity_id": "did:key:z6MkUser",
                "object_entity_type": "surface",
                "object_entity_id": "surface:coord-demo",
                "relationship_type": "accesses",
                "status": "active",
                "enabled_state": "enabled",
            }
        ),
    }
    req = _make_request(headers={"x-principal-did": "did:key:z6MkUser"}, db=db)
    assert_surface_ledger_access(req, "surface:coord-demo", "loam")


def test_inactive_surface_is_rejected() -> None:
    db = _surface_registry(
        {
            "surface_id": "surface:coord-demo",
            "ledger_id": "loam",
            "status": "pending",
            "enabled_state": "enabled",
        }
    )
    req = _make_request(headers={"x-principal-did": "did:key:z6MkOwner"}, db=db)
    with pytest.raises(HTTPException) as exc_info:
        assert_surface_ledger_access(req, "surface:coord-demo", "loam")
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail.get("error") == "surface_inactive"


def test_surface_not_bound_to_ledger_is_rejected() -> None:
    db = _surface_registry(
        {
            "surface_id": "surface:coord-demo",
            "status": "active",
            "enabled_state": "enabled",
        }
    )
    req = _make_request(headers={"x-principal-did": "did:key:z6MkOwner"}, db=db)
    with pytest.raises(HTTPException) as exc_info:
        assert_surface_ledger_access(req, "surface:coord-demo", "loam")
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail.get("error") == "surface_not_bound_to_ledger"


def test_anonymous_principal_is_rejected() -> None:
    db = _surface_registry(
        {
            "surface_id": "surface:coord-demo",
            "ledger_id": "loam",
            "status": "active",
            "enabled_state": "enabled",
        }
    )
    req = _make_request(db=db)
    with pytest.raises(HTTPException) as exc_info:
        assert_surface_ledger_access(req, "surface:coord-demo", "loam")
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail.get("error") == "decode_requires_authenticated_principal"


def test_unauthorized_principal_is_rejected() -> None:
    db = _surface_registry(
        {
            "surface_id": "surface:coord-demo",
            "ledger_id": "loam",
            "principal_did": "did:key:z6MkOwner",
            "status": "active",
            "enabled_state": "enabled",
        }
    )
    req = _make_request(headers={"x-principal-did": "did:key:z6MkOther"}, db=db)
    with pytest.raises(HTTPException) as exc_info:
        assert_surface_ledger_access(req, "surface:coord-demo", "loam")
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail.get("error") == "principal_not_authorized_for_surface"

"""Central authorization hook for ledger-scoped actions.

Default behavior is allow-all to preserve backward compatibility while
authorization plumbing is rolled out across routes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request

from backend.services.demo_mode import demo_god_mode_enabled


LedgerAction = Literal[
    "ledger.read",
    "ledger.write",
    "ledger.pin",
    "ledger.feedback",
    "sync.pull",
    "sync.push",
    "sync.checkpoint.read",
    "sync.checkpoint.write",
]


@dataclass(frozen=True)
class Principal:
    principal_id: str
    principal_type: str
    principal_did: str | None = None
    principal_key_id: str | None = None
    session_jti: str | None = None
    source: str = "legacy_header"


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    reason: str


_LEDGER_REGISTRY_KEY = b"__ledgers__"
_LEDGER_REGISTRY_V1_KEY = b"__ledgers_v1__"
_PRINCIPAL_REGISTRY_V1_KEY = b"__principals_v1__"


def principal_from_request(request: Request) -> Principal:
    # Delegated CLI requests (e.g. chat-surface Codex/Kimi turns) explicitly
    # identify the prompt principal via headers. When a delegated header is
    # present it should override the ambient session token claims for that
    # field; otherwise fall back to the session token as usual.
    delegated_cli_request = str(
        request.headers.get("x-delegated-cli-request") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}

    if delegated_cli_request:
        principal_did = _clean_str(
            request.headers.get("x-principal-did") or request.headers.get("x-did")
        ) or _request_claim_value(
            request,
            state_attr="auth_claim_principal_did",
            headers=(),
        )
        principal_key_id = _clean_str(
            request.headers.get("x-principal-key-id") or request.headers.get("x-key-id")
        ) or _request_claim_value(
            request,
            state_attr="auth_claim_principal_key_id",
            headers=(),
        )
        session_jti = _clean_str(
            request.headers.get("x-session-jti") or request.headers.get("x-auth-jti")
        ) or _request_claim_value(
            request,
            state_attr="auth_claim_session_jti",
            headers=(),
        )
    else:
        principal_did = _request_claim_value(
            request,
            state_attr="auth_claim_principal_did",
            headers=("x-principal-did", "x-did"),
        )
        principal_key_id = _request_claim_value(
            request,
            state_attr="auth_claim_principal_key_id",
            headers=("x-principal-key-id", "x-key-id"),
        )
        session_jti = _request_claim_value(
            request,
            state_attr="auth_claim_session_jti",
            headers=("x-session-jti", "x-auth-jti"),
        )
    principal_id = _clean_str(
        request.headers.get("x-principal-id")
        or request.headers.get("x-user-id")
    )
    source = "legacy_header" if principal_id else "anonymous_default"
    if principal_did:
        source = "did_header"
    if not principal_id:
        principal_id = principal_did or "anonymous"
    principal_type = request.headers.get("x-principal-type") or "service"
    return Principal(
        principal_id=str(principal_id),
        principal_type=str(principal_type),
        principal_did=principal_did,
        principal_key_id=principal_key_id,
        session_jti=session_jti,
        source=source,
    )


def _normalize_principal_key_reference(value: str | None) -> str:
    return str(value or "").strip().lower()


def _find_principal_record_by_key_ref(
    registry: dict[str, dict[str, Any]],
    *,
    principal_key_id: str | None,
) -> dict[str, Any] | None:
    normalized = _normalize_principal_key_reference(principal_key_id)
    if not normalized:
        return None
    for principal_did in sorted(registry.keys()):
        row = registry.get(principal_did)
        if not isinstance(row, dict):
            continue
        refs = (
            row.get("principal_key_refs")
            if isinstance(row.get("principal_key_refs"), list)
            else row.get("key_references")
            if isinstance(row.get("key_references"), list)
            else []
        )
        if normalized in {
            _normalize_principal_key_reference(item)
            for item in refs
            if _normalize_principal_key_reference(item)
        }:
            return dict(row)
    return None


def _canonicalize_principal_from_registry(request: Request, principal: Principal) -> Principal:
    registry = _load_principal_records(request)
    record = None
    if principal.principal_did:
        candidate = registry.get(str(principal.principal_did).strip())
        if isinstance(candidate, dict):
            record = dict(candidate)
    if record is None and principal.principal_key_id:
        record = _find_principal_record_by_key_ref(
            registry,
            principal_key_id=principal.principal_key_id,
        )
    if not isinstance(record, dict):
        return principal

    canonical_did = str(record.get("principal_did") or principal.principal_did or "").strip() or None
    if canonical_did and canonical_did != principal.principal_did:
        apply_auth_claim_overrides(
            request,
            principal_did=canonical_did,
            principal_key_id=principal.principal_key_id,
            session_jti=principal.session_jti,
        )
    return Principal(
        principal_id=principal.principal_id,
        principal_type=principal.principal_type,
        principal_did=canonical_did,
        principal_key_id=principal.principal_key_id,
        session_jti=principal.session_jti,
        source=principal.source,
    )


def apply_auth_claim_overrides(
    request: Request,
    *,
    principal_did: str | None = None,
    principal_key_id: str | None = None,
    session_jti: str | None = None,
) -> None:
    state = getattr(request, "state", None)
    if state is None:
        return
    try:
        if principal_did is not None:
            state.auth_claim_principal_did = _clean_str(principal_did) or None  # type: ignore[attr-defined]
        if principal_key_id is not None:
            state.auth_claim_principal_key_id = _clean_str(principal_key_id) or None  # type: ignore[attr-defined]
        if session_jti is not None:
            state.auth_claim_session_jti = _clean_str(session_jti) or None  # type: ignore[attr-defined]
    except Exception:
        return


def evaluate_authorization(
    *,
    principal: Principal,
    ledger_id: str,
    action: LedgerAction,
    request: Request | None = None,
) -> AuthzDecision:
    if demo_god_mode_enabled():
        # Even in demo god mode, run the delegated-principal contract so that
        # stream metadata (e.g. delegated_prompt_path) is populated for
        # delegated chat-surface turns.
        if request is not None:
            _evaluate_delegated_principal_contract(
                request=request,
                principal=principal,
                ledger_id=ledger_id,
            )
        return AuthzDecision(allowed=True, reason="demo_god_mode")

    delegated_reason: str | None = None
    if request is not None:
        delegated_decision = _evaluate_delegated_principal_contract(
            request=request,
            principal=principal,
            ledger_id=ledger_id,
        )
        if delegated_decision is not None:
            if not delegated_decision.allowed:
                return delegated_decision
            delegated_reason = delegated_decision.reason

    mode = os.getenv("LEDGER_AUTHZ_MODE", "allow_all").strip().lower()
    if mode in {"allow_all", "", "off", "disabled"}:
        return AuthzDecision(allowed=True, reason=delegated_reason or "allow_all_mode")

    if mode not in {"registry", "tenant_owner", "enforce", "policy"}:
        return AuthzDecision(allowed=True, reason=delegated_reason or "unknown_mode_allow")

    principal_mode = _auth_principal_mode()
    if principal_mode == "did_strict" and not (principal.principal_did or "").strip():
        return AuthzDecision(allowed=False, reason="did_principal_required")

    if request is None:
        return AuthzDecision(allowed=True, reason="missing_request_allow")
    if _is_default_ledger(ledger_id):
        return AuthzDecision(allowed=True, reason="default_ledger_allow")

    registry = _load_registry_records(request)
    record = registry.get(str(ledger_id))
    if not isinstance(record, dict):
        unknown_policy = os.getenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "allow").strip().lower()
        if unknown_policy in {"allow", "compat", ""}:
            return AuthzDecision(allowed=True, reason="unknown_ledger_allow")
        return AuthzDecision(allowed=False, reason="unknown_ledger")

    owner_principal_id = str(record.get("owner_principal_id") or "").strip()
    tenant_id = str(record.get("tenant_id") or "").strip()
    metadata = record.get("metadata")
    shared_read = bool(metadata.get("shared_read")) if isinstance(metadata, dict) else False
    request_tenant = _request_tenant_id(request, principal)
    is_admin_principal = principal.principal_type.lower() in _admin_principal_types()
    path = str(getattr(request.url, "path", "") or "")
    is_admin_path = path.startswith("/admin")

    if is_admin_path and not is_admin_principal:
        return AuthzDecision(allowed=False, reason="admin_principal_required")

    tenant_match = bool(tenant_id and request_tenant and tenant_id == request_tenant)
    owner_match = bool(owner_principal_id and principal.principal_id == owner_principal_id)
    has_read_privilege = is_admin_principal or owner_match or tenant_match or shared_read
    has_write_privilege = is_admin_principal or owner_match or tenant_match
    context_allowed = _context_allowed_for_record(request=request, record=record)

    read_actions: set[LedgerAction] = {"ledger.read", "sync.pull", "sync.checkpoint.read"}
    write_actions: set[LedgerAction] = {
        "ledger.write",
        "ledger.pin",
        "ledger.feedback",
        "sync.push",
        "sync.checkpoint.write",
    }

    if action in read_actions:
        if has_read_privilege:
            return AuthzDecision(allowed=True, reason="read_privilege_granted")
        return AuthzDecision(allowed=False, reason="read_requires_owner_or_tenant")

    if action in write_actions:
        if has_write_privilege and context_allowed:
            return AuthzDecision(allowed=True, reason="write_privilege_granted")
        if has_write_privilege and not context_allowed:
            return AuthzDecision(allowed=False, reason="context_not_allowed")
        return AuthzDecision(allowed=False, reason="write_requires_owner_or_tenant")

    return AuthzDecision(allowed=False, reason="unsupported_action")


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_registry_records(request: Request) -> dict[str, dict[str, Any]]:
    db = getattr(getattr(request, "app", None), "state", None)
    if db is None or not hasattr(db, "db"):
        return {}
    store = getattr(db, "db", None)
    if store is None:
        return {}

    raw_v1 = store.get(_LEDGER_REGISTRY_V1_KEY)
    decoded_v1 = _decode_json(raw_v1)
    records = decoded_v1.get("ledgers") if isinstance(decoded_v1, dict) else None
    if isinstance(records, dict):
        out: dict[str, dict[str, Any]] = {}
        for ledger_id, record in records.items():
            if isinstance(record, dict):
                out[str(ledger_id)] = dict(record)
        if out:
            return out

    raw_legacy = store.get(_LEDGER_REGISTRY_KEY)
    decoded_legacy = _decode_json(raw_legacy)
    if isinstance(decoded_legacy, list):
        migrated: dict[str, dict[str, Any]] = {}
        for item in decoded_legacy:
            ledger_id = str(item).strip()
            if not ledger_id:
                continue
            migrated[ledger_id] = {
                "ledger_id": ledger_id,
                "tenant_id": "tenant:legacy",
                "owner_principal_id": "legacy",
                "owner_principal_type": "legacy",
                "policy_profile": "legacy",
                "status": "active",
                "metadata": {},
            }
        return migrated
    return {}


def _load_principal_records(request: Request) -> dict[str, dict[str, Any]]:
    db = getattr(getattr(request, "app", None), "state", None)
    if db is None or not hasattr(db, "db"):
        return {}
    store = getattr(db, "db", None)
    if store is None:
        return {}

    raw = store.get(_PRINCIPAL_REGISTRY_V1_KEY)
    decoded = _decode_json(raw)
    records = decoded.get("principals") if isinstance(decoded, dict) else None
    if not isinstance(records, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for principal_did, record in records.items():
        if isinstance(record, dict):
            out[str(principal_did)] = dict(record)
    return out


def _coerce_scope_values(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return set()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return {str(item).strip() for item in parsed if str(item).strip()}
        return {part.strip() for part in raw.split(",") if part.strip()}
    return set()


def _header_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = _clean_str(value)
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evaluate_delegated_principal_contract(
    *,
    request: Request,
    principal: Principal,
    ledger_id: str,
) -> AuthzDecision | None:
    principal_type = str(principal.principal_type or "").strip().lower()
    delegated_cli_request = _header_truthy(request.headers.get("x-delegated-cli-request"))
    if principal_type != "agent" and not delegated_cli_request:
        return None

    principal_did = str(principal.principal_did or "").strip()
    registry = _load_principal_records(request)
    record = registry.get(principal_did) if principal_did else None
    if not isinstance(record, dict):
        return AuthzDecision(allowed=False, reason="delegated_principal_unregistered")

    status = str(record.get("status") or "").strip().lower()
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    actor_type = str(metadata.get("actor_type") or principal_type).strip().lower()
    delegated_authority = (
        metadata.get("delegated_authority")
        if isinstance(metadata.get("delegated_authority"), dict)
        else {}
    )
    delegation_mode = str(delegated_authority.get("delegation_mode") or "").strip().lower()

    state = getattr(request, "state", None)
    if state is not None:
        try:
            state.authz_principal_registry_status = status or "active"  # type: ignore[attr-defined]
            state.authz_principal_registry_source = "backend_registry"  # type: ignore[attr-defined]
        except Exception:
            pass

    if actor_type != "agent" or delegation_mode != "delegated_only":
        return None

    if status and status != "active":
        return AuthzDecision(allowed=False, reason="delegated_principal_inactive")
    if not delegated_cli_request:
        return AuthzDecision(allowed=False, reason="delegated_cli_request_required")

    delegated_by_principal_did = _clean_str(request.headers.get("x-delegated-by-principal-did"))
    delegated_by_principal_id = _clean_str(request.headers.get("x-delegated-by-principal-id"))
    expected_delegator_did = _clean_str(delegated_authority.get("delegated_by_principal_did"))
    if expected_delegator_did and delegated_by_principal_did != expected_delegator_did:
        return AuthzDecision(allowed=False, reason="delegated_by_principal_mismatch")

    expected_ledger_scope = _coerce_scope_values(delegated_authority.get("ledger_scope"))
    request_ledger_scope = _coerce_scope_values(request.headers.get("x-delegated-ledger-scope"))
    if expected_ledger_scope and ledger_id not in expected_ledger_scope:
        return AuthzDecision(allowed=False, reason="delegated_ledger_scope_mismatch")
    if request_ledger_scope and ledger_id not in request_ledger_scope:
        return AuthzDecision(allowed=False, reason="delegated_request_ledger_scope_mismatch")

    expected_surface_scope = _coerce_scope_values(delegated_authority.get("surface_scope"))
    request_surface_scope = _coerce_scope_values(request.headers.get("x-delegated-surface-scope"))
    request_surface_id = _clean_str(request.headers.get("x-surface-id"))
    if expected_surface_scope:
        if not request_surface_id:
            return AuthzDecision(allowed=False, reason="delegated_surface_scope_required")
        if request_surface_id not in expected_surface_scope:
            return AuthzDecision(allowed=False, reason="delegated_surface_scope_mismatch")
    if request_surface_scope and request_surface_id and request_surface_id not in request_surface_scope:
        return AuthzDecision(allowed=False, reason="delegated_request_surface_scope_mismatch")

    expires_at = _parse_iso_datetime(request.headers.get("x-delegation-expires-at"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return AuthzDecision(allowed=False, reason="delegation_expired")

    if state is not None:
        try:
            state.authz_delegated_prompt_path_active = True  # type: ignore[attr-defined]
            state.authz_delegated_cli_request = delegated_cli_request  # type: ignore[attr-defined]
            state.authz_delegated_by_principal_did = delegated_by_principal_did  # type: ignore[attr-defined]
            state.authz_delegated_by_principal_id = delegated_by_principal_id  # type: ignore[attr-defined]
            state.authz_delegation_mode = delegation_mode or "delegated_only"  # type: ignore[attr-defined]
            state.authz_delegated_surface_id = request_surface_id  # type: ignore[attr-defined]
            state.authz_delegated_ledger_scope = ",".join(sorted(request_ledger_scope or expected_ledger_scope))  # type: ignore[attr-defined]
            state.authz_delegated_surface_scope = ",".join(sorted(request_surface_scope or expected_surface_scope))  # type: ignore[attr-defined]
            state.authz_delegation_expires_at = expires_at.isoformat() if expires_at is not None else None  # type: ignore[attr-defined]
        except Exception:
            pass

    return AuthzDecision(allowed=True, reason="delegated_cli_prompt_granted")


def _request_tenant_id(request: Request, principal: Principal) -> str:
    tenant = request.headers.get("x-tenant-id")
    if isinstance(tenant, str) and tenant.strip():
        return tenant.strip()
    principal_id = (principal.principal_id or "").strip()
    if principal_id and principal_id != "anonymous":
        return f"tenant:{principal_id}"
    return "tenant:default"


def _request_claim_value(request: Request, *, state_attr: str, headers: tuple[str, ...]) -> str:
    state = getattr(request, "state", None)
    if state is not None:
        value = _clean_str(getattr(state, state_attr, None))
        if value:
            return value
    for header in headers:
        value = _clean_str(request.headers.get(header))
        if value:
            return value
    return ""


def _clean_str(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _auth_principal_mode() -> str:
    mode = os.getenv("AUTH_PRINCIPAL_MODE", "compat").strip().lower()
    if mode in {"did", "did_strict", "strict"}:
        return "did_strict"
    return "compat"


def _admin_principal_types() -> set[str]:
    raw = os.getenv("LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES", "admin,service")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _is_default_ledger(ledger_id: str) -> bool:
    normalized = (ledger_id or "").strip().lower()
    return normalized in {"", "default", "chat-default"}


def _request_context_id(request: Request) -> str:
    state = getattr(request, "state", None)
    if state is not None:
        value = getattr(state, "context_id", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    header_context = request.headers.get("x-context-id")
    if isinstance(header_context, str) and header_context.strip():
        return header_context.strip()
    query_context = request.query_params.get("context_id")
    if isinstance(query_context, str) and query_context.strip():
        return query_context.strip()
    return ""


def _record_allowed_contexts(record: dict[str, Any]) -> set[str]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return set()
    raw = metadata.get("allowed_context_ids")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _context_binding_mode() -> str:
    mode = os.getenv("LEDGER_CONTEXT_BINDING_MODE", "compat").strip().lower()
    if mode in {"off", "disabled"}:
        return "off"
    if mode in {"enforce", "strict"}:
        return "enforce"
    return "compat"


def _context_allowed_for_record(*, request: Request, record: dict[str, Any]) -> bool:
    mode = _context_binding_mode()
    if mode == "off":
        return True

    allowed = _record_allowed_contexts(record)
    if not allowed:
        return True

    context_id = _request_context_id(request)
    if not context_id:
        return False
    return context_id in allowed


def _request_has_explicit_ledger_context(request: Request) -> bool:
    for header in ("x-ledger-id", "x-ledger", "x-ledger-id-h64"):
        value = request.headers.get(header)
        if isinstance(value, str) and value.strip():
            return True
    for param in ("ledger_id", "ledger", "ledger_id_h64"):
        value = request.query_params.get(param)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _enforce_explicit_ledger_context_or_raise(
    request: Request,
    *,
    ledger_id: str,
    explicit_context: bool,
) -> None:
    if demo_god_mode_enabled():
        return
    mode = os.getenv("LEDGER_CONTEXT_MODE", "compat").strip().lower()
    if mode in {"off", "disabled"}:
        return
    if _is_default_ledger(ledger_id):
        return
    if explicit_context or _request_has_explicit_ledger_context(request):
        return
    if mode == "compat":
        return
    raise HTTPException(
        status_code=422,
        detail={
            "error": "ledger_context_required",
            "ledger_id": str(ledger_id),
            "hint": "provide explicit ledger context via payload/path or x-ledger-id header",
        },
    )


def authorize_or_raise(
    request: Request,
    *,
    ledger_id: str,
    action: LedgerAction,
    explicit_context: bool = False,
) -> AuthzDecision:
    _enforce_explicit_ledger_context_or_raise(
        request,
        ledger_id=str(ledger_id),
        explicit_context=bool(explicit_context),
    )
    principal = _canonicalize_principal_from_registry(request, principal_from_request(request))
    decision = evaluate_authorization(
        principal=principal,
        ledger_id=str(ledger_id),
        action=action,
        request=request,
    )
    if hasattr(request, "state"):
        try:
            request.state.authz_reason = decision.reason  # type: ignore[attr-defined]
            request.state.authz_action = str(action)  # type: ignore[attr-defined]
            request.state.authz_principal_source = principal.source  # type: ignore[attr-defined]
            request.state.authz_principal_mode = _auth_principal_mode()  # type: ignore[attr-defined]
            request.state.authz_principal_did = principal.principal_did  # type: ignore[attr-defined]
            request.state.authz_principal_key_id = principal.principal_key_id  # type: ignore[attr-defined]
            request.state.authz_session_jti = principal.session_jti  # type: ignore[attr-defined]
            request.state.authz_ledger_id = str(ledger_id)  # type: ignore[attr-defined]
        except Exception:
            pass
    if decision.allowed:
        return decision
    raise HTTPException(
        status_code=403,
        detail={
            "error": "forbidden",
            "action": action,
            "ledger_id": str(ledger_id),
            "principal_id": principal.principal_id,
            "principal_mode": _auth_principal_mode(),
            "reason": decision.reason,
        },
    )


def authz_diagnostics_from_request(request: Request) -> dict[str, Any]:
    state = getattr(request, "state", None)
    context_id = ""
    if state is not None:
        context_id = _clean_str(getattr(state, "context_id", None))
    if not context_id:
        context_id = _clean_str(request.headers.get("x-context-id"))
    principal_registry_status = (
        _clean_str(getattr(state, "authz_principal_registry_status", None))
        or _clean_str(request.headers.get("x-principal-registry-status"))
        or "unknown"
    )
    principal_registry_source = (
        _clean_str(getattr(state, "authz_principal_registry_source", None))
        or _clean_str(request.headers.get("x-principal-registry-source"))
        or "none"
    )
    return {
        "principal_source": _clean_str(getattr(state, "authz_principal_source", None)) or "unknown",
        "principal_mode": _clean_str(getattr(state, "authz_principal_mode", None)) or _auth_principal_mode(),
        "principal_did_present": bool(_clean_str(getattr(state, "authz_principal_did", None))),
        "principal_key_id_present": bool(_clean_str(getattr(state, "authz_principal_key_id", None))),
        "session_jti_present": bool(_clean_str(getattr(state, "authz_session_jti", None))),
        "principal_registry_status": principal_registry_status,
        "principal_registry_source": principal_registry_source,
        "delegated_prompt_path_active": bool(getattr(state, "authz_delegated_prompt_path_active", False)),
        "delegated_cli_request": bool(getattr(state, "authz_delegated_cli_request", False)),
        "delegated_by_principal_did": _clean_str(getattr(state, "authz_delegated_by_principal_did", None)) or None,
        "delegated_by_principal_id": _clean_str(getattr(state, "authz_delegated_by_principal_id", None)) or None,
        "delegation_mode": _clean_str(getattr(state, "authz_delegation_mode", None)) or None,
        "delegated_surface_id": _clean_str(getattr(state, "authz_delegated_surface_id", None)) or None,
        "delegated_ledger_scope": _clean_str(getattr(state, "authz_delegated_ledger_scope", None)) or None,
        "delegated_surface_scope": _clean_str(getattr(state, "authz_delegated_surface_scope", None)) or None,
        "delegation_expires_at": _clean_str(getattr(state, "authz_delegation_expires_at", None)) or None,
        "context_id": context_id or None,
        "authz_reason": _clean_str(getattr(state, "authz_reason", None)) or "unknown",
    }

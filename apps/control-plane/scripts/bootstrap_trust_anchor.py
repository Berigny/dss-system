from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _headers(admin_token: str, principal_id: str, principal_type: str, context_id: str, ledger_id: str) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-admin-token": admin_token,
        "x-principal-id": principal_id,
        "x-principal-type": principal_type,
        "x-context-id": context_id,
    }
    if ledger_id:
        headers["x-ledger-id"] = ledger_id
    return headers


def _issuer_payload(issuer_did: str, did_document_url: str, public_base_url: str) -> dict[str, Any]:
    trust_bundle_url = f"{public_base_url}/.well-known/trust-anchor.json"
    status_url = f"{public_base_url}/api/trust-anchor/status"
    return {
        "issuer": "dss-trust-anchor",
        "issuer_class": "service",
        "allowed_event_types": ["identity.assertion", "trust.anchor.publish"],
        "evidence_requirement": "required",
        "credential_ref": "issuer-authority:dss-trust-anchor",
        "issuer_did": issuer_did,
        "identity_anchor_ref": did_document_url,
        "trust_basis": issuer_did,
        "policy_ref": f"{trust_bundle_url}#issuer-policy",
        "policy_verdict": "allow",
        "policy_scope": ["identity.assertion", "trust.anchor.publish", "trust_anchor"],
        "verifier_policy_ref": trust_bundle_url,
        "verification_state": "anchored",
        "vc_type": "IssuerAuthorityCredential",
        "vc_id": f"{trust_bundle_url}#issuer-authority-credential",
        "vc_envelope": {
            "id": f"{trust_bundle_url}#issuer-authority-credential",
            "type": ["VerifiableCredential", "IssuerAuthorityCredential"],
            "issuer": issuer_did,
            "credentialStatus": {"id": status_url},
        },
        "credential_status_ref": status_url,
        "credential_status_state": "active",
        "vc_verification_method": "did_document_check",
        "vc_verification_status": "verified",
        "vc_verification_proof_ref": did_document_url,
        "status": "active",
        "notes": "Bootstrap issuer authority for DSS public trust anchor.",
    }


def _identity_payload(issuer_did: str, did_document_url: str) -> dict[str, Any]:
    return {
        "subject_ref": issuer_did,
        "subject_type": "issuer",
        "resolver_ref": did_document_url,
        "resolution_status": "verified",
        "resolved_identity": issuer_did,
        "authority_binding_ref": "issuer-authority:dss-trust-anchor",
        "identity_anchor_ref": did_document_url,
        "checked_at": _now_iso(),
        "trust_root_ref": did_document_url,
        "evidence_ref": did_document_url,
        "notes": "Bootstrap live identity check for DSS public trust anchor.",
    }


def main() -> int:
    backend_base = _env("BACKEND_BASE_URL", "").rstrip("/")
    public_base_url = _env("PUBLIC_BASE_URL", "").rstrip("/")
    issuer_did = _env("ISSUER_DID", "")
    admin_token = _env("TRUST_ANCHOR_ADMIN_TOKEN", _env("ADMIN_TOKEN"))
    principal_id = _env("TRUST_ANCHOR_ADMIN_PRINCIPAL_ID", "ops-admin")
    principal_type = _env("TRUST_ANCHOR_ADMIN_PRINCIPAL_TYPE", "admin")
    context_id = _env("TRUST_ANCHOR_CONTEXT_ID", "ctx:dashboard:trust-anchor-bootstrap")
    ledger_id = _env("TRUST_ANCHOR_LEDGER_ID", "default")
    dry_run = _env("DRY_RUN", "0").lower() in {"1", "true", "yes", "on"}

    if not admin_token:
        raise SystemExit("TRUST_ANCHOR_ADMIN_TOKEN or ADMIN_TOKEN is required")

    did_document_url = f"{public_base_url}/.well-known/did.json"
    headers = _headers(admin_token, principal_id, principal_type, context_id, ledger_id)
    issuer_payload = _issuer_payload(issuer_did, did_document_url, public_base_url)
    identity_payload = _identity_payload(issuer_did, did_document_url)

    plan = {
        "backend_base": backend_base,
        "issuer_authority_url": f"{backend_base}/admin/issuer-authorities",
        "live_identity_check_url": f"{backend_base}/admin/live-identity-checks",
        "issuer_payload": issuer_payload,
        "identity_payload": identity_payload,
        "dry_run": dry_run,
    }
    print(json.dumps(plan, indent=2))

    if dry_run:
        return 0

    with httpx.Client(timeout=10.0) as client:
        issuer_resp = client.post(f"{backend_base}/admin/issuer-authorities", headers=headers, json=issuer_payload)
        issuer_resp.raise_for_status()
        identity_resp = client.post(f"{backend_base}/admin/live-identity-checks", headers=headers, json=identity_payload)
        identity_resp.raise_for_status()
        result = {
            "issuer_authority": issuer_resp.json(),
            "live_identity_check": identity_resp.json(),
        }
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

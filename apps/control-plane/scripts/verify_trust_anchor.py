from __future__ import annotations

import json
import os
from typing import Any

import httpx


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _fetch_json(client: httpx.Client, url: str) -> dict[str, Any] | None:
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def main() -> int:
    public_base_url = _env("PUBLIC_BASE_URL", "").rstrip("/")
    middleware_base_url = _env("MIDDLEWARE_BASE_URL", "").rstrip("/")
    issuer_did = _env("ISSUER_DID", "")

    did_document_url = f"{public_base_url}/.well-known/did.json"
    trust_bundle_url = f"{public_base_url}/.well-known/trust-anchor.json"
    public_status_url = f"{public_base_url}/api/trust-anchor/status"
    middleware_status_url = f"{middleware_base_url}/api/trust-anchor/status"

    with httpx.Client(timeout=10.0) as client:
        did_document = _fetch_json(client, did_document_url)
        trust_bundle = _fetch_json(client, trust_bundle_url)
        public_status = _fetch_json(client, public_status_url)
        middleware_status = _fetch_json(client, middleware_status_url)

    services = did_document.get("service") if isinstance(did_document, dict) else None
    service_endpoints = {
        str(service.get("serviceEndpoint") or "").strip()
        for service in (services or [])
        if isinstance(service, dict)
    }

    checks = {
        "did_document_id_match": str(did_document.get("id") or "").strip() == issuer_did,
        "did_service_has_public_status": public_status_url in service_endpoints,
        "did_service_has_trust_bundle": trust_bundle_url in service_endpoints,
        "bundle_issuer_did_match": str(trust_bundle.get("issuer_did") or "").strip() == issuer_did,
        "bundle_did_document_url_match": str(trust_bundle.get("did_document_url") or "").strip() == did_document_url,
        "middleware_status_ok": str(middleware_status.get("status") or "").strip().lower() == "ok",
        "public_status_ok": str(public_status.get("status") or "").strip().lower() == "ok",
    }

    result = {
        "issuer_did": issuer_did,
        "public_base_url": public_base_url,
        "middleware_base_url": middleware_base_url,
        "did_document_url": did_document_url,
        "trust_bundle_url": trust_bundle_url,
        "checks": checks,
        "did_document": did_document,
        "trust_bundle": trust_bundle,
        "public_status": public_status,
        "middleware_status": middleware_status,
    }
    print(json.dumps(result, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

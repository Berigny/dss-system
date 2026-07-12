"""Tests for DSS-144: Wallet provider picker and OIDC4VCI cross-wallet interoperability."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import router as auth_router
from backend.api.wallet import router as wallet_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    app.include_router(wallet_router)
    return TestClient(app)


def test_wallet_providers_lists_supported_providers() -> None:
    client = _make_client()
    resp = client.get("/wallet/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    providers = {p["provider_id"] for p in body["providers"]}
    assert "microsoft_authenticator" in providers
    assert "mattr" in providers


def test_wallet_did_document_returns_valid_did_json() -> None:
    client = _make_client()
    resp = client.get("/wallet/mattr/did.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/did+json"
    assert resp.headers["access-control-allow-origin"] == "*"
    body = resp.json()
    assert body["@context"] == ["https://www.w3.org/ns/did/v1"]
    assert body["id"] == "did:web:id.dualsubstrate.com"
    assert len(body["verificationMethod"]) == 1


def test_wallet_did_document_rejects_unknown_provider() -> None:
    client = _make_client()
    resp = client.get("/wallet/unknown_wallet/did.json")
    assert resp.status_code == 404


def test_wallet_credential_offer_returns_oidc4vci_format() -> None:
    client = _make_client()
    resp = client.get("/wallet/credential-offer?session_id=test-123&wallet_provider=mattr")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["wallet_provider"] == "mattr"
    offer = body["credential_offer"]
    assert offer["credential_issuer"] == "did:web:id.dualsubstrate.com"
    assert "DssSupplyChainIdentity" in offer["credential_configuration_ids"]
    assert "urn:ietf:params:oauth:grant-type:pre-authorized_code" in offer["grants"]
    tx_code = offer["grants"]["urn:ietf:params:oauth:grant-type:pre-authorized_code"]["tx_code"]
    assert tx_code["length"] == 4
    assert tx_code["input_mode"] == "numeric"


def test_wallet_credential_offer_defaults_to_microsoft() -> None:
    client = _make_client()
    resp = client.get("/wallet/credential-offer?session_id=test-456")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wallet_provider"] == "microsoft_authenticator"
    offer = body["credential_offer"]
    assert offer.get("_microsoft_entra_hint") is True


def test_wallet_credential_offer_rejects_unsupported_provider() -> None:
    client = _make_client()
    resp = client.get("/wallet/credential-offer?session_id=test-789&wallet_provider=apple_wallet")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "unsupported_wallet_provider"


def test_wallet_cors_preflight() -> None:
    client = _make_client()
    resp = client.options("/wallet/mattr/did.json")
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "GET" in resp.headers["access-control-allow-methods"]

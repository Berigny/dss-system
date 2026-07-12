from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import router as auth_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    return TestClient(app)


def test_auth_challenge_normalizes_dualsubstrate_subdomain_to_root_rp_id(monkeypatch) -> None:
    monkeypatch.delenv("AUTH_WEBAUTHN_RP_ID", raising=False)
    monkeypatch.setenv(
        "AUTH_WEBAUTHN_ALLOWED_ORIGINS",
        "https://id.dualsubstrate.com,https://chat.dualsubstrate.com",
    )
    client = _make_client()
    principal_did = "did:key:z6MkDualSubstratePasskey"
    _seed_active_principal(client, principal_did)

    response = client.post(
        "/auth/challenge",
        json={
            "principal_did": principal_did,
            "origin": "https://id.dualsubstrate.com",
            "rp_id": "id.dualsubstrate.com",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["origin"] == "https://id.dualsubstrate.com"
    assert payload["rp_id"] == "dualsubstrate.com"
    assert payload["request_options"]["rpId"] == "dualsubstrate.com"


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _seed_active_principal(client: TestClient, principal_did: str) -> None:
    payload = {
        "version": 1,
        "principals": {
            principal_did: {
                "principal_did": principal_did,
                "status": "active",
                "key_references": [f"{principal_did}#k1"],
            }
        },
    }
    client.app.state.db[b"__principals_v1__"] = json.dumps(payload).encode()


def _build_assertion(
    *,
    private_key: ec.EllipticCurvePrivateKey,
    challenge: str,
    origin: str,
    rp_id: str,
    sign_count: int,
) -> dict[str, str]:
    client_data = {
        "type": "webauthn.get",
        "challenge": challenge,
        "origin": origin,
        "crossOrigin": False,
    }
    client_data_json = json.dumps(client_data, separators=(",", ":")).encode("utf-8")
    rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    flags = bytes([0x05])  # user present + user verified
    auth_data = rp_id_hash + flags + int(sign_count).to_bytes(4, "big")
    signed_payload = auth_data + hashlib.sha256(client_data_json).digest()
    signature = private_key.sign(signed_payload, ec.ECDSA(hashes.SHA256()))
    return {
        "client_data_json_b64u": _b64u_encode(client_data_json),
        "authenticator_data_b64u": _b64u_encode(auth_data),
        "signature_b64u": _b64u_encode(signature),
    }


def _build_register_payload(
    *,
    challenge: str,
    origin: str,
    rp_id: str,
    sign_count: int = 0,
    principal_key_id: str | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, dict[str, str]]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    client_data = {
        "type": "webauthn.create",
        "challenge": challenge,
        "origin": origin,
        "crossOrigin": False,
    }
    client_data_json = json.dumps(client_data, separators=(",", ":")).encode("utf-8")
    rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    flags = bytes([0x45])  # UP + UV + AT
    auth_data = rp_id_hash + flags + int(sign_count).to_bytes(4, "big")
    spki_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = {
        "client_data_json_b64u": _b64u_encode(client_data_json),
        "authenticator_data_b64u": _b64u_encode(auth_data),
        "public_key_spki_b64u": _b64u_encode(spki_der),
    }
    if principal_key_id:
        payload["principal_key_id"] = principal_key_id
    return private_key, payload


def test_auth_challenge_and_verify_binds_credential_and_allows_incrementing_sign_count(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkPasskeyFlow"
    credential_id = "cred-passkey-1"
    _seed_active_principal(client, principal_did)

    challenge_1 = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert challenge_1.status_code == 200
    challenge_payload_1 = challenge_1.json()

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    assertion_1 = _build_assertion(
        private_key=private_key,
        challenge=challenge_payload_1["challenge"],
        origin=challenge_payload_1["origin"],
        rp_id=challenge_payload_1["rp_id"],
        sign_count=1,
    )

    verify_1 = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge_payload_1["challenge_id"],
            "credential_id": credential_id,
            "principal_did": principal_did,
            "public_key_pem": public_key_pem,
            **assertion_1,
        },
    )
    assert verify_1.status_code == 200
    body_1 = verify_1.json()
    assert body_1["status"] == "ok"
    assert body_1["binding_created"] is True
    assert body_1["sign_count"] == 1
    session_1 = body_1.get("session")
    assert isinstance(session_1, dict)
    assert isinstance(session_1.get("token"), str) and session_1.get("token")
    assert session_1.get("token_type") == "Bearer"

    challenge_2 = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert challenge_2.status_code == 200
    challenge_payload_2 = challenge_2.json()
    assertion_2 = _build_assertion(
        private_key=private_key,
        challenge=challenge_payload_2["challenge"],
        origin=challenge_payload_2["origin"],
        rp_id=challenge_payload_2["rp_id"],
        sign_count=2,
    )
    verify_2 = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge_payload_2["challenge_id"],
            "credential_id": credential_id,
            **assertion_2,
        },
    )
    assert verify_2.status_code == 200
    body_2 = verify_2.json()
    assert body_2["binding_created"] is False
    assert body_2["sign_count"] == 2
    session_2 = body_2.get("session")
    assert isinstance(session_2, dict)
    assert isinstance(session_2.get("token"), str) and session_2.get("token")


def test_auth_register_challenge_and_verify_enables_followup_login(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkRegisterFlow"
    credential_id = "cred-register-1"

    register_challenge = client.post(
        "/auth/register/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert register_challenge.status_code == 200
    register_payload = register_challenge.json()
    private_key, reg = _build_register_payload(
        challenge=register_payload["challenge"],
        origin=register_payload["origin"],
        rp_id=register_payload["rp_id"],
        sign_count=1,
        principal_key_id=f"{principal_did}#k1",
    )
    verify_register = client.post(
        "/auth/register/verify",
        json={
            "challenge_id": register_payload["challenge_id"],
            "credential_id": credential_id,
            **reg,
        },
    )
    assert verify_register.status_code == 200
    assert verify_register.json().get("status") == "ok"

    challenge_login = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    assertion = _build_assertion(
        private_key=private_key,
        challenge=challenge_login["challenge"],
        origin=challenge_login["origin"],
        rp_id=challenge_login["rp_id"],
        sign_count=2,
    )
    verify_login = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge_login["challenge_id"],
            "credential_id": credential_id,
            **assertion,
        },
    )
    assert verify_login.status_code == 200
    assert verify_login.json().get("status") == "ok"


def test_auth_register_challenge_includes_exclude_credentials_after_binding(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkExcludeCreds"
    credential_id = "cred-exclude-1"

    register_challenge = client.post(
        "/auth/register/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert register_challenge.status_code == 200
    register_payload = register_challenge.json()
    _, reg = _build_register_payload(
        challenge=register_payload["challenge"],
        origin=register_payload["origin"],
        rp_id=register_payload["rp_id"],
        sign_count=1,
        principal_key_id=f"{principal_did}#k1",
    )
    verify_register = client.post(
        "/auth/register/verify",
        json={
            "challenge_id": register_payload["challenge_id"],
            "credential_id": credential_id,
            **reg,
        },
    )
    assert verify_register.status_code == 200

    register_challenge_2 = client.post(
        "/auth/register/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert register_challenge_2.status_code == 200
    creation_options = register_challenge_2.json().get("creation_options") or {}
    exclude_credentials = creation_options.get("excludeCredentials") or []
    assert isinstance(exclude_credentials, list)
    assert any(item.get("id") == credential_id for item in exclude_credentials if isinstance(item, dict))


def test_auth_challenge_includes_allow_credentials_for_principal(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkAllowCreds"
    credential_id = "cred-allow-1"

    register_challenge = client.post(
        "/auth/register/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert register_challenge.status_code == 200
    register_payload = register_challenge.json()
    _, reg = _build_register_payload(
        challenge=register_payload["challenge"],
        origin=register_payload["origin"],
        rp_id=register_payload["rp_id"],
        sign_count=1,
        principal_key_id=f"{principal_did}#k1",
    )
    verify_register = client.post(
        "/auth/register/verify",
        json={
            "challenge_id": register_payload["challenge_id"],
            "credential_id": credential_id,
            **reg,
        },
    )
    assert verify_register.status_code == 200

    challenge_login_resp = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    )
    assert challenge_login_resp.status_code == 200
    challenge_login = challenge_login_resp.json()
    allow_credentials = challenge_login.get("allow_credentials") or []
    assert isinstance(allow_credentials, list)
    assert any(item.get("id") == credential_id for item in allow_credentials if isinstance(item, dict))
    request_options = challenge_login.get("request_options") or {}
    assert request_options.get("rpId") == challenge_login.get("rp_id")
    assert isinstance(request_options.get("allowCredentials"), list)


def test_auth_verify_rejects_sign_count_replay(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkReplay"
    credential_id = "cred-replay-1"
    _seed_active_principal(client, principal_did)

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    challenge_ok = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    assertion_ok = _build_assertion(
        private_key=private_key,
        challenge=challenge_ok["challenge"],
        origin=challenge_ok["origin"],
        rp_id=challenge_ok["rp_id"],
        sign_count=5,
    )
    ok_resp = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge_ok["challenge_id"],
            "credential_id": credential_id,
            "principal_did": principal_did,
            "public_key_pem": public_key_pem,
            **assertion_ok,
        },
    )
    assert ok_resp.status_code == 200

    challenge_replay = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    assertion_replay = _build_assertion(
        private_key=private_key,
        challenge=challenge_replay["challenge"],
        origin=challenge_replay["origin"],
        rp_id=challenge_replay["rp_id"],
        sign_count=5,
    )
    replay_resp = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge_replay["challenge_id"],
            "credential_id": credential_id,
            **assertion_replay,
        },
    )
    assert replay_resp.status_code == 401
    detail = replay_resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "sign_count_replay_detected"


def test_auth_verify_rejects_register_flow_challenge(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkFlowMismatch"
    _seed_active_principal(client, principal_did)
    credential_id = "cred-flow-mismatch-1"
    challenge = client.post(
        "/auth/register/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    private_key = ec.generate_private_key(ec.SECP256R1())
    assertion = _build_assertion(
        private_key=private_key,
        challenge=challenge["challenge"],
        origin=challenge["origin"],
        rp_id=challenge["rp_id"],
        sign_count=1,
    )
    resp = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "credential_id": credential_id,
            "public_key_pem": private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8"),
            **assertion,
        },
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "challenge_flow_invalid"


def test_auth_verify_rejects_origin_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkOrigin"
    credential_id = "cred-origin-1"
    _seed_active_principal(client, principal_did)

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    challenge = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    assertion = _build_assertion(
        private_key=private_key,
        challenge=challenge["challenge"],
        origin="https://evil.example.test",
        rp_id=challenge["rp_id"],
        sign_count=1,
    )
    resp = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "credential_id": credential_id,
            "principal_did": principal_did,
            "public_key_pem": public_key_pem,
            **assertion,
        },
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "origin_mismatch"


def test_auth_verify_rejects_rp_id_hash_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("AUTH_WEBAUTHN_RP_ID", "app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkRpId"
    credential_id = "cred-rpid-1"
    _seed_active_principal(client, principal_did)

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    challenge = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "https://app.example.test"},
    ).json()
    assertion = _build_assertion(
        private_key=private_key,
        challenge=challenge["challenge"],
        origin=challenge["origin"],
        rp_id="other.example.test",
        sign_count=1,
    )
    resp = client.post(
        "/auth/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "credential_id": credential_id,
            "principal_did": principal_did,
            "public_key_pem": public_key_pem,
            **assertion,
        },
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "rp_id_hash_mismatch"


def test_auth_token_endpoint_issues_session_token_for_active_principal(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkTokenEndpoint"
    _seed_active_principal(client, principal_did)

    resp = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "principal_key_id": f"{principal_did}#k1",
            "roles": ["writer"],
            "allowed_context_ids": ["ctx:test"],
            "ledger_ids": ["chat-team-b"],
            "ttl_seconds": 600,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"
    session = body.get("session")
    assert isinstance(session, dict)
    assert isinstance(session.get("token"), str) and session.get("token")
    claims = session.get("claims")
    assert isinstance(claims, dict)
    assert claims.get("sub") == principal_did


def test_auth_session_reports_token_claims(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkSessionProbe"
    _seed_active_principal(client, principal_did)

    token_issue = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "principal_key_id": f"{principal_did}#k1",
        },
    )
    assert token_issue.status_code == 200
    token = token_issue.json()["session"]["token"]

    session_resp = client.get(
        "/auth/session",
        headers={"x-session-token": token},
    )
    assert session_resp.status_code == 200
    body = session_resp.json()
    assert body.get("authenticated") is True
    assert body.get("principal_did") == principal_did
    assert body.get("session_jti")
    assert body.get("auth_method") == "passkey"
    assert body.get("principal_status") == "active"


def test_auth_session_verify_reports_missing_session_token() -> None:
    client = _make_client()
    response = client.get("/auth/session/verify")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("verified") is False
    assert payload.get("reason") == "missing_session_token"


def test_auth_session_verify_reports_verified_claims(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkSessionVerify"
    _seed_active_principal(client, principal_did)

    token_issue = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "principal_key_id": f"{principal_did}#k1",
        },
    )
    assert token_issue.status_code == 200
    token = token_issue.json()["session"]["token"]

    verify_resp = client.get(
        "/auth/session/verify",
        headers={"x-session-token": token},
    )
    assert verify_resp.status_code == 200
    payload = verify_resp.json()
    assert payload.get("verified") is True
    assert payload.get("reason") == "verified"
    assert payload.get("principal_did") == principal_did
    assert payload.get("session_jti")
    assert payload.get("auth_method") == "passkey"


def test_auth_session_refresh_reissues_token_for_active_principal(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkSessionRefresh"
    _seed_active_principal(client, principal_did)

    token_issue = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "principal_key_id": f"{principal_did}#k1",
        },
    )
    assert token_issue.status_code == 200
    token = token_issue.json()["session"]["token"]
    refresh_token = token_issue.json()["refresh_session"]["token"]
    original_jti = token_issue.json()["session"]["jti"]

    refresh_resp = client.post(
        "/auth/session/refresh",
        headers={
            "x-session-token": token,
            "x-refresh-token": refresh_token,
        },
    )

    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload.get("refreshed") is True
    assert payload.get("principal_did") == principal_did
    refreshed_session = payload.get("session") or {}
    refreshed_refresh_session = payload.get("refresh_session") or {}
    assert refreshed_session.get("token")
    assert refreshed_session.get("jti")
    assert refreshed_session.get("jti") != original_jti
    assert int(refreshed_session.get("expires_at") or 0) >= int(refreshed_session.get("issued_at") or 0)
    assert refreshed_refresh_session.get("token")
    assert refreshed_refresh_session.get("jti")


def test_auth_session_refresh_accepts_refresh_token_without_access_token(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", "https://app.example.test")
    client = _make_client()
    principal_did = "did:key:z6MkRefreshOnly"
    _seed_active_principal(client, principal_did)

    token_issue = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "principal_key_id": f"{principal_did}#k1",
        },
    )
    assert token_issue.status_code == 200
    refresh_token = token_issue.json()["refresh_session"]["token"]

    refresh_resp = client.post(
        "/auth/session/refresh",
        headers={"x-refresh-token": refresh_token},
    )

    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload.get("refreshed") is True
    assert payload.get("principal_did") == principal_did
    assert (payload.get("session") or {}).get("token")
    assert (payload.get("refresh_session") or {}).get("token")

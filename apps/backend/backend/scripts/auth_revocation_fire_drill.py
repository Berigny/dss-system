"""Non-prod auth revocation fire-drill.

Runs an isolated in-memory DB scenario to validate:
1) credential revocation blocks token-authenticated writes
2) session JTI revocation blocks token-authenticated writes
"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi.testclient import TestClient


def _detail_reason(resp) -> str:
    try:
        payload = resp.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return ""
    return str(detail.get("reason") or "")


def main() -> int:
    os.environ["DB_PATH"] = tempfile.mkdtemp(prefix="auth_revoke_drill_")
    os.environ["ADMIN_TOKEN"] = "test-admin-token"

    from backend.main import app

    result: dict[str, object] = {}
    with TestClient(app) as client:
        principal_did = "did:key:z6MkDrill"
        credential_id = "cred-drill-1"

        client.app.state.db[b"__principals_v1__"] = json.dumps(
            {
                "version": 1,
                "principals": {
                    principal_did: {
                        "principal_did": principal_did,
                        "status": "active",
                    }
                },
            }
        ).encode()
        client.app.state.db[b"__passkey_bindings_v1__"] = json.dumps(
            {
                "version": 1,
                "bindings": {
                    credential_id: {
                        "credential_id": credential_id,
                        "principal_did": principal_did,
                        "status": "active",
                        "public_key_pem": "pem",
                        "sign_count": 3,
                    }
                },
            }
        ).encode()

        issue = client.post(
            "/auth/token",
            json={
                "principal_did": principal_did,
                "credential_id": credential_id,
                "ttl_seconds": 900,
            },
        )
        if issue.status_code != 200:
            raise RuntimeError(f"token issue failed: {issue.status_code} {issue.text}")
        session = issue.json().get("session") or {}
        token = str(session.get("token") or "")
        jti = str(session.get("jti") or "")

        base_write = client.post(
            "/api/chat/commit-answer",
            json={
                "entity": "chat-team-a",
                "ledger_id": "chat-team-b",
                "context_id": "ctx:test",
                "user_message": "u",
                "assistant_reply": "a",
                "metadata": {"session_id": "s1", "turn_id": "t1"},
            },
            headers={
                "x-ledger-id": "chat-team-b",
                "authorization": f"Bearer {token}",
            },
        )
        if base_write.status_code != 200:
            raise RuntimeError(f"baseline write failed: {base_write.status_code} {base_write.text}")

        revoke_cred = client.post(
            f"/auth/passkeys/{credential_id}/revoke",
            json={"reason": "fire-drill"},
            headers={"x-admin-token": "test-admin-token"},
        )
        if revoke_cred.status_code != 200:
            raise RuntimeError(f"credential revoke failed: {revoke_cred.status_code} {revoke_cred.text}")

        blocked_after_cred_revoke = client.post(
            "/api/chat/commit-answer",
            json={
                "entity": "chat-team-a",
                "ledger_id": "chat-team-b",
                "context_id": "ctx:test",
                "user_message": "u",
                "assistant_reply": "a",
                "metadata": {"session_id": "s1", "turn_id": "t2"},
            },
            headers={
                "x-ledger-id": "chat-team-b",
                "authorization": f"Bearer {token}",
            },
        )

        issue_no_cred = client.post(
            "/auth/token",
            json={"principal_did": principal_did, "ttl_seconds": 900},
        )
        if issue_no_cred.status_code != 200:
            raise RuntimeError(f"token issue no-cred failed: {issue_no_cred.status_code} {issue_no_cred.text}")
        session_no_cred = issue_no_cred.json().get("session") or {}
        token_no_cred = str(session_no_cred.get("token") or "")
        jti_no_cred = str(session_no_cred.get("jti") or "")

        revoke_jti = client.post(
            "/auth/sessions/revoke",
            json={"jti": jti_no_cred, "reason": "fire-drill"},
            headers={"x-admin-token": "test-admin-token"},
        )
        if revoke_jti.status_code != 200:
            raise RuntimeError(f"session revoke failed: {revoke_jti.status_code} {revoke_jti.text}")

        blocked_after_jti_revoke = client.post(
            "/api/chat/commit-answer",
            json={
                "entity": "chat-team-a",
                "ledger_id": "chat-team-b",
                "context_id": "ctx:test",
                "user_message": "u",
                "assistant_reply": "a",
                "metadata": {"session_id": "s1", "turn_id": "t3"},
            },
            headers={
                "x-ledger-id": "chat-team-b",
                "authorization": f"Bearer {token_no_cred}",
            },
        )

        result = {
            "baseline_write_status": base_write.status_code,
            "issued_jti_with_credential": jti,
            "credential_revoke_status": revoke_cred.status_code,
            "post_credential_revoke_status": blocked_after_cred_revoke.status_code,
            "post_credential_revoke_reason": _detail_reason(blocked_after_cred_revoke),
            "issued_jti_no_credential": jti_no_cred,
            "session_revoke_status": revoke_jti.status_code,
            "post_session_revoke_status": blocked_after_jti_revoke.status_code,
            "post_session_revoke_reason": _detail_reason(blocked_after_jti_revoke),
        }

    assert result["post_credential_revoke_status"] == 401
    assert result["post_credential_revoke_reason"] == "token_credential_revoked"
    assert result["post_session_revoke_status"] == 401
    assert result["post_session_revoke_reason"] == "token_revoked"

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

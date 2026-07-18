"""Tests for the COORD demo FastHTML app."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import app


@pytest.fixture
def client():
    return TestClient(app.app)


@pytest.fixture(autouse=True)
def _stub_session_verification(monkeypatch: pytest.MonkeyPatch):
    """Treat every session token as valid in tests."""
    async def fake_verify(_token: str) -> str:
        return "did:key:z6MkTestPrincipal"

    monkeypatch.setattr(app, "_verify_session_token", fake_verify)


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "commit_sha" in payload


def test_index_redirects_when_unauthenticated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "_verify_session_token", lambda _t: None)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert app.CONTROL_PLANE_BASE in response.headers["location"]


def test_index_renders_form(client: TestClient) -> None:
    response = client.get("/", cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"})
    assert response.status_code == 200
    assert "DualSubstrate // Resolver" in response.text
    assert "Universal Coherence Decoder" in response.text
    assert "coordinate" in response.text
    assert "hx-post=\"/resolve\"" in response.text


def _sample_decode_payload() -> dict:
    return {
        "coord": "loam:WX-A71BA232-1784308498",
        "type": "WX",
        "skim": {
            "one_line": "Progress acknowledged with patience and persistence.",
            "relevance": 1.0,
        },
        "payload": {
            "segments": [{"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:WX:ANS-01"}],
            "blobs": {
                "BLOB:WX:ANS-01": "Your progress with fixes is acknowledged."
            },
        },
        "interpretation": {
            "topics": [{"label": "progress", "score": 0.78}],
            "claims": [{"label": "fixes_acknowledged"}],
        },
        "refs": {
            "context": [
                {"coord": "loam:WX-A71BA232-1784306898", "type": "WX"},
            ],
        },
        "governance": {
            "appraisal": {"score": 0.9999, "law": 1.0, "grace": 0.9999, "drift": 0.0},
            "policy_decision": "allow",
            "risk_class": "low",
            "policy_version": "mmf-gov-v2",
        },
        "meta": {
            "namespace_used": "loam",
            "created_at": "2026-07-18T01:57:00+00:00",
            "canonical_subject": "did:web:legacy.local:ledgers:ledger-loam",
        },
    }


def test_resolve_forwards_coordinate_to_middleware(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    sample = _sample_decode_payload()

    def fake_post(url: str, *, json: object, headers: object, timeout: float) -> object:  # noqa: ARG001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class Response:
            status_code = 200

            def json(self) -> object:
                return sample

            def raise_for_status(self) -> None:
                pass

        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/resolve",
        data={"coordinate": "loam:WX-A71BA232-1784308498"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
    )

    assert response.status_code == 200
    assert captured["url"] == f"{app.MIDDLEWARE_URL}/api/decode_coordinate"
    assert captured["json"] == {
        "coordinate": "loam:WX-A71BA232-1784308498",
        "ledger_id": app.DEFAULT_LEDGER_ID,
    }
    assert captured["headers"]["x-surface-id"] == "surface:coord-demo"
    text = response.text
    assert "Progress acknowledged with patience and persistence." in text
    assert "Your progress with fixes is acknowledged." in text
    assert "fixes_acknowledged" in text
    assert "loam:WX-A71BA232-1784306898" in text
    assert "View Raw Ledger JSON" in text
    assert "allow" in text
    assert "low" in text


def test_resolve_renders_upstream_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_post(*args, **kwargs) -> object:  # noqa: ARG001
        import httpx

        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/resolve",
        data={"coordinate": "chat-demo:WX-1"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
    )
    assert response.status_code == 200
    assert "Resolver error" in response.text


def test_auth_callback_sets_session_cookie(client: TestClient) -> None:
    response = client.get("/auth/callback?ds_session_token=token-123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookies = response.cookies
    assert app.BACKEND_SESSION_TOKEN_COOKIE in cookies
    assert cookies[app.BACKEND_SESSION_TOKEN_COOKIE] == "token-123"


def test_middleware_accepts_session_token_in_query_string(
    client: TestClient,
) -> None:
    response = client.get("/?ds_session_token=token-456", follow_redirects=False)
    assert response.status_code == 303
    assert "://" in response.headers["location"]
    cookies = response.cookies
    assert app.BACKEND_SESSION_TOKEN_COOKIE in cookies
    assert cookies[app.BACKEND_SESSION_TOKEN_COOKIE] == "token-456"


def test_extract_payload_text_prefers_blob_segments() -> None:
    payload = _sample_decode_payload()
    assert app._extract_payload_text(payload) == "Your progress with fixes is acknowledged."


def test_extract_summary_falls_back_to_payload_text() -> None:
    payload = _sample_decode_payload()
    payload["skim"] = {}
    assert app._extract_summary(payload) == "Your progress with fixes is acknowledged."


def test_extract_claims_and_topics() -> None:
    payload = _sample_decode_payload()
    assert app._extract_claims(payload) == ["fixes_acknowledged"]
    assert app._extract_topics(payload) == [("progress", 0.78)]


def test_collect_referenced_coords_skips_self() -> None:
    payload = _sample_decode_payload()
    refs = app._collect_referenced_coords(payload, "loam:WX-A71BA232-1784308498")
    assert "loam:WX-A71BA232-1784306898" in refs
    assert "loam:WX-A71BA232-1784308498" not in refs


def test_resolve_returns_fragment_for_htmx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _sample_decode_payload()

    def fake_post(*args, **kwargs):  # noqa: ARG001
        class Response:
            status_code = 200
            def json(self): return sample
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/resolve",
        data={"coordinate": "loam:WX-A71BA232-1784308498"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<html>" not in response.text
    assert "Reconstructed Knowledge Tree" in response.text
    assert "View Raw Ledger JSON" in response.text


def test_resolve_returns_full_page_without_htmx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _sample_decode_payload()

    def fake_post(*args, **kwargs):  # noqa: ARG001
        class Response:
            status_code = 200
            def json(self): return sample
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/resolve",
        data={"coordinate": "loam:WX-A71BA232-1784308498"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
    )
    assert response.status_code == 200
    assert "<html>" in response.text
    assert "DualSubstrate // Resolver" in response.text
    assert "Reconstructed Knowledge Tree" in response.text


def test_feedback_submits_to_middleware(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: object, headers: object, timeout: float) -> object:  # noqa: ARG001
        captured["url"] = url
        captured["json"] = json
        class Response:
            status_code = 200
            def json(self): return {"status": "ok"}
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/feedback",
        data={"coord": "loam:WX-A71BA232-1784308498", "rating": "3", "reason": "test"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
    )
    assert response.status_code == 200
    assert "Feedback submitted" in response.text
    assert captured["url"] == f"{app.MIDDLEWARE_URL}/ledger/feedback/loam:WX-A71BA232-1784308498"
    assert captured["json"]["rating"] == 3

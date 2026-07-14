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
    assert response.json() == {"status": "ok"}


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
    assert "Resolve COORD" in response.text
    assert "coordinate" in response.text


def test_resolve_forwards_coordinate_to_middleware(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: object, timeout: float) -> object:
        captured["url"] = url
        captured["json"] = json

        class Response:
            status_code = 200

            def json(self) -> object:
                return {"resolved": True, "input": json}

            def raise_for_status(self) -> None:
                pass

        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post(
        "/resolve",
        data={"coordinate": "chat-demo:WX-1"},
        cookies={app.BACKEND_SESSION_TOKEN_COOKIE: "valid-token"},
    )

    assert response.status_code == 200
    assert captured["url"] == f"{app.MIDDLEWARE_URL}/api/decode_coordinate"
    assert captured["json"] == {"coordinate": "chat-demo:WX-1", "ledger_id": app.DEFAULT_LEDGER_ID}
    assert "resolved" in response.text


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

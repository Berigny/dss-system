"""Tests for the COORD demo FastHTML app."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import app


@pytest.fixture
def client():
    return TestClient(app.app)


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_form(client: TestClient) -> None:
    response = client.get("/")
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

    response = client.post("/resolve", data={"coordinate": "chat-demo:WX-1"})

    assert response.status_code == 200
    assert captured["url"] == f"{app.MIDDLEWARE_URL}/api/decode_coordinate"
    assert captured["json"] == {"coordinate": "chat-demo:WX-1"}
    assert "resolved" in response.text


def test_resolve_renders_upstream_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_post(*args, **kwargs) -> object:  # noqa: ARG001
        import httpx

        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.post", fake_post)

    response = client.post("/resolve", data={"coordinate": "chat-demo:WX-1"})
    assert response.status_code == 200
    assert "Resolver error" in response.text

"""Tests for the coord-demo cross-domain SSO launch endpoint."""

import importlib.util
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("dss_dashboard_app", REPO_ROOT / "app.py")
dashboard_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard_app)


def test_decode_launch_redirects_authenticated_user_to_coord_demo_with_token(monkeypatch) -> None:
    async def fake_auth_proxy_get(path: str, headers: dict[str, str] | None = None):
        assert path == "/auth/session/verify"
        assert headers is not None
        assert headers.get("x-session-token") == "opaque-session-token"
        return 200, {"valid": True, "principal_did": "did:key:z6MkOperator"}

    monkeypatch.setattr(dashboard_app, "_auth_proxy_get", fake_auth_proxy_get)

    client = TestClient(dashboard_app.app)
    client.cookies.set("ds_backend_session_token", "opaque-session-token")
    response = client.get("/go/decode", follow_redirects=False)

    assert response.status_code == 303
    location = str(response.headers.get("location") or "")
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "decode.dualsubstrate.com"
    assert parsed.path in {"", "/"}
    query = parse_qs(parsed.query)
    assert query.get("ds_session_token") == ["opaque-session-token"]


def test_decode_launch_redirects_authenticated_user_directly_for_same_site_subdomain(monkeypatch) -> None:
    async def fake_auth_proxy_get(path: str, headers: dict[str, str] | None = None):
        return 200, {"valid": True, "principal_did": "did:key:z6MkOperator"}

    monkeypatch.setattr(dashboard_app, "_auth_proxy_get", fake_auth_proxy_get)
    monkeypatch.setenv("BASE_DOMAIN", "dualsubstrate.com")

    client = TestClient(dashboard_app.app)
    client.cookies.set("ds_backend_session_token", "opaque-session-token")
    response = client.get("/go/decode", follow_redirects=False)

    assert response.status_code == 303
    location = str(response.headers.get("location") or "")
    assert location == "https://decode.dualsubstrate.com"
    assert "ds_session_token" not in location


def test_decode_launch_redirects_unauthenticated_user_to_wallet_login(monkeypatch) -> None:
    async def fake_auth_proxy_get(path: str, headers: dict[str, str] | None = None):
        return 401, {"error": "invalid_token"}

    monkeypatch.setattr(dashboard_app, "_auth_proxy_get", fake_auth_proxy_get)

    client = TestClient(dashboard_app.app)
    response = client.get("/go/decode", follow_redirects=False)

    assert response.status_code == 303
    location = str(response.headers.get("location") or "")
    assert location.startswith("/login/wallet?next=")
    assert "decode.dualsubstrate.com" in location
    assert "/auth/callback" in location


def test_decode_launch_redirects_to_login_when_no_session_cookie(monkeypatch) -> None:
    # No auth proxy call should happen; the function should short-circuit.
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_auth_proxy_get(path: str, headers: dict[str, str] | None = None):
        calls.append((path, headers))
        return 200, {"valid": True, "principal_did": "did:key:z6MkOperator"}

    monkeypatch.setattr(dashboard_app, "_auth_proxy_get", fake_auth_proxy_get)

    client = TestClient(dashboard_app.app)
    response = client.get("/go/decode", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers.get("location", "").startswith("/login/wallet?next=")
    assert not calls

import asyncio
import importlib.util
import pathlib
import sys
from urllib.parse import unquote

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location("dss_dashboard_app", REPO_ROOT / "app.py")
dashboard_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard_app)


def test_render_settings_providers_content_shows_masked_key() -> None:
    html_out = dashboard_app._render_settings_providers_content(
        configured=True,
        masked="sk-or-*************f456",
        source="env",
        error="",
    )
    assert "OpenRouter" in html_out
    assert "sk-or-*************f456" in html_out
    assert "Env" in html_out or "env" in html_out.lower()
    assert 'type="password"' in html_out
    assert "Save OpenRouter key" in html_out


def test_render_settings_providers_content_shows_unconfigured() -> None:
    html_out = dashboard_app._render_settings_providers_content(
        configured=False,
        masked="",
        source="unknown",
        error="",
    )
    assert "Not configured" in html_out or "not configured" in html_out.lower()


def test_settings_providers_openrouter_key_submit_blank_keeps_existing() -> None:
    """A blank submission should keep the existing key, matching the UI copy."""
    original_require = dashboard_app._require_control_plane_auth
    original_post = dashboard_app._middleware_admin_post

    async def fake_require(request):
        return ({"identity_vc": {"principal_did": "did:web:alice"}}, None)

    async def fake_post(path, payload):
        raise AssertionError("POST should not be called for blank key")

    dashboard_app._require_control_plane_auth = fake_require
    dashboard_app._middleware_admin_post = fake_post

    class FakeRequest:
        async def form(self):
            return {"openrouter_api_key": "   "}

    try:
        response = asyncio.run(
            dashboard_app.settings_providers_openrouter_key_submit(FakeRequest())
        )
        assert response.status_code == 303
        location = unquote(str(response.headers.get("location") or ""))
        assert "no change made" in location.lower() or "kept" in location.lower()
        assert "banner_kind=ok" in location
    finally:
        dashboard_app._require_control_plane_auth = original_require
        dashboard_app._middleware_admin_post = original_post


def test_settings_providers_openrouter_key_submit_saves_new_key() -> None:
    original_require = dashboard_app._require_control_plane_auth
    original_post = dashboard_app._middleware_admin_post
    posted: list[tuple[str, dict[str, object]]] = []

    async def fake_require(request):
        return ({"identity_vc": {"principal_did": "did:web:alice"}}, None)

    async def fake_post(path, payload):
        posted.append((path, dict(payload)))
        return 200, {"configured": True, "masked": "sk-or-****", "source": "override"}

    dashboard_app._require_control_plane_auth = fake_require
    dashboard_app._middleware_admin_post = fake_post

    class FakeRequest:
        async def form(self):
            return {"openrouter_api_key": "sk-or-v1-newkey"}

    try:
        response = asyncio.run(
            dashboard_app.settings_providers_openrouter_key_submit(FakeRequest())
        )
        assert response.status_code == 303
        location = unquote(str(response.headers.get("location") or ""))
        assert "API key saved" in location
        assert "banner_kind=ok" in location
        assert posted == [
            (
                "/api/control-plane/providers/openrouter/key",
                {"api_key": "sk-or-v1-newkey"},
            )
        ]
    finally:
        dashboard_app._require_control_plane_auth = original_require
        dashboard_app._middleware_admin_post = original_post


def test_settings_providers_openrouter_key_submit_handles_save_failure() -> None:
    original_require = dashboard_app._require_control_plane_auth
    original_post = dashboard_app._middleware_admin_post

    async def fake_require(request):
        return ({"identity_vc": {"principal_did": "did:web:alice"}}, None)

    async def fake_post(path, payload):
        return 500, {"error": "middleware_unavailable"}

    dashboard_app._require_control_plane_auth = fake_require
    dashboard_app._middleware_admin_post = fake_post

    class FakeRequest:
        async def form(self):
            return {"openrouter_api_key": "sk-or-v1-newkey"}

    try:
        response = asyncio.run(
            dashboard_app.settings_providers_openrouter_key_submit(FakeRequest())
        )
        assert response.status_code == 303
        location = unquote(str(response.headers.get("location") or ""))
        assert "Could not save" in location
        assert "banner_kind=warn" in location
    finally:
        dashboard_app._require_control_plane_auth = original_require
        dashboard_app._middleware_admin_post = original_post


def test_settings_providers_openrouter_key_submit_requires_auth() -> None:
    original_require = dashboard_app._require_control_plane_auth

    async def fake_require(request):
        return (None, dashboard_app.RedirectResponse(url="/login"))

    dashboard_app._require_control_plane_auth = fake_require

    class FakeRequest:
        async def form(self):
            return {}

    try:
        response = asyncio.run(
            dashboard_app.settings_providers_openrouter_key_submit(FakeRequest())
        )
        assert response.status_code == 307 or response.status_code == 302 or response.status_code == 303
        assert "/login" in str(response.headers.get("location") or "")
    finally:
        dashboard_app._require_control_plane_auth = original_require

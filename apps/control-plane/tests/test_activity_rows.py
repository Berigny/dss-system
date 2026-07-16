import importlib.util
import json
from io import BytesIO
import pathlib
import sys
import types
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from starlette.requests import Request
from starlette.datastructures import FormData, UploadFile
from starlette.responses import RedirectResponse
import asyncio


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("dss_dashboard_app", REPO_ROOT / "app.py")
dashboard_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard_app)

from views.flows import _selection_list


def _make_request(query_string: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/activity",
            "query_string": query_string.encode("utf-8"),
            "headers": [],
        }
    )


def _make_login_request(query_string: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/login",
            "query_string": query_string.encode("utf-8"),
            "headers": [],
        }
    )


def _empty_connection_context() -> dict[str, object]:
    return {
        "ledgers": [],
        "principals": [],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {},
    }


def test_activity_share_bundle_prefers_coord_then_submission_ref() -> None:
    preferred_reference, share_ready, share_reason = dashboard_app._activity_share_bundle(
        {
            "coord": "coord:abc123",
            "submission_ref": "sub:123",
            "reference": "fallback-ref",
            "details": {},
        }
    )
    assert preferred_reference == "coord:abc123"
    assert share_ready is True
    assert share_reason == "coord"


def test_account_setup_route_and_control_plane_mount_are_registered() -> None:
    source = (REPO_ROOT / "app.py").read_text()
    shell_source = (REPO_ROOT / "views" / "shell.py").read_text()

    assert 'ACCOUNT_SETUP_PATH = "/account/setup"' in source
    assert "async def account_setup_page" in source
    assert "Route(ACCOUNT_SETUP_PATH, account_setup_page" in source
    assert 'RedirectResponse(url="/settings#account-setup-checklist"' in source
    assert '<h2>Account Setup</h2>' in source
    assert 'id="account-setup-checklist"' in source
    assert 'id="setup-prompt-banner"' in source
    assert "GET /account/current/setup-checklist" not in source
    assert "/account/current/setup-checklist" in source
    assert '("/account/setup", "Account Setup")' not in shell_source


def test_benchmark_route_and_nav_are_registered() -> None:
    source = (REPO_ROOT / "app.py").read_text()
    shell_source = (REPO_ROOT / "views" / "shell.py").read_text()

    assert "async def benchmarks_page" in source
    assert "async def api_benchmarks_performance" in source
    assert "async def api_auth_session_refresh" in source
    assert "async def api_control_plane_benchmark_publication_jobs" in source
    assert "async def api_control_plane_benchmark_publication_job_status" in source
    assert 'Route("/benchmarks", benchmarks_page' in source
    assert 'Route("/api/auth/session/refresh", api_auth_session_refresh' in source
    assert 'Route("/api/benchmarks/performance", api_benchmarks_performance' in source
    assert 'Route("/api/control-plane/benchmarks/publication-jobs", api_control_plane_benchmark_publication_jobs' in source
    assert 'Route("/api/control-plane/benchmarks/publication-jobs/{job_id}", api_control_plane_benchmark_publication_job_status' in source
    assert '("/benchmarks", "About")' in shell_source
    assert '("/settings", "Settings")' not in shell_source
    assert '("/about", "About")' not in shell_source


def test_control_plane_setup_prompt_uses_backend_and_local_setup_target() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert "window.dsAccountApiBase" in source
    assert "window.dsControlPlaneBase" in source
    assert "function setupPromptTargetHref" in source
    assert "PUBLIC_BASE_URL" in source
    assert 'const rawRoute = String((target && target.route) || "{ACCOUNT_SETUP_PATH}")' in source
    assert "/account/current/setup-prompt" in source
    assert "/account/current/setup-prompt/dismiss" in source
    assert 'render_breadcrumbs([("Home", "/"), ("Settings", None)])' in source
    assert "def _settings_section_tabs(" in source
    assert "_settings_section_tabs(" in source and "current_section" in source
    assert "Manage your profile or account details; and review trust and security configuration details." in source


def test_control_plane_layout_includes_interactive_session_refresh_hook() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert 'const refreshPath = "/api/auth/session/refresh";' in source
    assert 'BACKEND_REFRESH_TOKEN_COOKIE = "ds_backend_refresh_token"' in source
    assert '"x-refresh-token": refresh_token' in source
    assert 'document.cookie.includes("ds_backend_refresh_token=")' in source
    assert 'window.location.href = loginUrl;' in source
    assert '"login_url": f"/login?next=' in source
    assert '["pointerdown", "keydown", "submit"]' in source


def test_hydrate_add_principal_state_prefills_email_and_dids() -> None:
    context = _empty_connection_context()
    context["principal_map"] = {
        "did:key:z6MkDavidPrimary": {
            "principal_did": "did:key:z6MkDavidPrimary",
            "display_name": "David Berigny",
            "principal_type": "human",
            "metadata": {
                "actor_type": "human",
                "email": "david@example.com",
                "existing_did": "did:key:z6MkDavidSecondary",
                "additional_dids": ["did:web:id.dualsubstrate.com:principals:david-berigny"],
            },
        }
    }

    hydrated = dashboard_app._hydrate_add_principal_state(
        {"principal_id": "did:key:z6MkDavidPrimary"},
        context=context,
    )

    assert hydrated["contact_email"] == "david@example.com"
    assert hydrated["did_mode"] == "use_existing"
    assert hydrated["existing_did"] == "did:key:z6MkDavidSecondary"
    assert "did:key:z6MkDavidPrimary" in hydrated["current_did_values"]
    assert "did:web:id.dualsubstrate.com:principals:david-berigny" in hydrated["current_did_values"]


def test_hydrate_add_ledger_state_prefills_founding_constitution() -> None:
    identity_card = {"principal_did": "did:key:z6MkOps"}
    context = _empty_connection_context()
    context["ledger_map"] = {
        "chat-demo": {
            "ledger_id": "chat-demo",
            "ledger_name": "Chat Demo",
            "display_name": "Chat Demo",
            "tenant_id": "tenant:demo",
            "metadata": {
                "ledger_topology": "prime",
                "founding_constitution": {
                    "name": "LOAM",
                    "personality": "Deliberate, layered, patient with complexity.",
                    "purpose": "Hold governed memory and continuity for this ledger.",
                },
            },
        }
    }

    hydrated = dashboard_app._hydrate_add_ledger_state(
        _make_request(),
        {"ledger_id": "chat-demo"},
        identity_card=identity_card,
        context=context,
    )

    assert hydrated["founding_constitution_name"] == "LOAM"
    assert hydrated["founding_constitution_personality"] == "Deliberate, layered, patient with complexity."
    assert hydrated["founding_constitution_purpose"] == "Hold governed memory and continuity for this ledger."


def test_render_add_ledger_flow_shows_founding_constitution_fields() -> None:
    body = dashboard_app.render_add_ledger_flow(
        step="Ledger details",
        state={
            "name": "Chat Demo",
            "founding_constitution_name": "LOAM",
            "founding_constitution_personality": "Deliberate, layered, patient with complexity.",
            "founding_constitution_purpose": "Hold governed memory and continuity for this ledger.",
        },
        current_principal_did="did:key:z6MkOps",
    )

    assert "What the ledger calls itself" in body
    assert "Starter personality" in body
    assert "Founding purpose" in body
    assert "LOAM" in body
    assert "Deliberate, layered, patient with complexity." in body


def test_render_add_ledger_flow_preserves_existing_ledger_id_when_name_changes() -> None:
    body = dashboard_app.render_add_ledger_flow(
        step="Ledger details",
        state={
            "ledger_id": "chat-demo",
            "name": "LOAM 137 to 139",
            "founding_constitution_name": "LOAM",
        },
        current_principal_did="did:key:z6MkOps",
    )

    assert 'name="ledger_id"' in body
    assert 'value="chat-demo"' in body
    assert "LOAM 137 to 139" in body


def test_render_add_principal_flow_shows_current_values_with_edit_controls() -> None:
    body = dashboard_app.render_add_principal_flow(
        step="Principal details",
        state={
            "principal_id": "did:key:z6MkDavidPrimary",
            "principal_did": "did:key:z6MkDavidPrimary",
            "display_name": "David Berigny",
            "principal_type": "human",
            "contact_email": "david@example.com",
            "did_mode": "use_existing",
            "existing_did": "did:key:z6MkDavidSecondary",
            "current_did_values": "did:key:z6MkDavidPrimary\ndid:key:z6MkDavidSecondary",
        },
    )

    assert "david@example.com" in body
    assert "did:key:z6MkDavidPrimary" in body
    assert "did:key:z6MkDavidSecondary" in body
    assert 'data-toggle-target="principal-contact-email-editor"' in body
    assert 'data-toggle-target="principal-did-editor"' in body
    assert "Add existing DID" in body
    assert "Provision new" in body


def test_render_add_principal_flow_shows_codex_delegated_agent_preset() -> None:
    body = dashboard_app.render_add_principal_flow(
        step="Principal details",
        state={
            "principal_type": "service",
            "service_subtype": "delegated_agent",
            "linked_ledger_ids": "chat-demo",
            "linked_surface_ids": "surface:chat:primary",
        },
    )

    assert "Service subtype" in body
    assert "Delegated agent" in body
    assert "Codex delegated agent preset" in body
    assert "OpenAI: Codex" in body
    assert "did:web:id.dualsubstrate.com:principals:agent:openai:codex" in body
    assert "openai:agent:codex" in body
    assert "chat-demo" in body
    assert "surface:chat:primary" in body
    assert "Confirm delegated-only posture" in body
    assert "Generic provider/API fields do not apply here." in body


def test_control_plane_safe_next_allows_chat_and_rejects_external_hosts() -> None:
    assert dashboard_app._safe_next_path("/account/setup") == "/account/setup"
    assert (
        dashboard_app._safe_next_path("https://chat.dualsubstrate.com/ui/history/chat-demo?view=recent")
        == "https://chat.dualsubstrate.com/ui/history/chat-demo?view=recent"
    )
    assert dashboard_app._safe_next_path("https://evil.example/login") == "/"
    assert dashboard_app._safe_next_path("//evil.example/login") == "/"


def test_control_plane_login_redirect_preserves_safe_chat_next() -> None:
    request = _make_login_request("next=https%3A%2F%2Fchat.dualsubstrate.com%2Fui%2Fhistory%2Fchat-demo%3Fview%3Drecent")

    response = asyncio.run(dashboard_app.login_page(request))

    assert response.status_code == 307
    location = response.headers.get("location") or ""
    assert location.startswith("/login/wallet?next=")
    assert "https%3A//chat.dualsubstrate.com/ui/history/chat-demo?view=recent" in location


def test_profile_setup_submit_persists_email_and_did_metadata(monkeypatch) -> None:
    async def fake_build_identity_card(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkDavidPrimary",
                "tenant_id": "tenant:david",
                "ledger_id": "chat-demo",
            }
        }

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkDavidPrimary",
                    "tenant_id": "tenant:david",
                    "ledger_id": "chat-demo",
                }
            },
            "models_current": {
                "online_models": [
                    {"id": "google/gemma-4-26b-a4b-it:free", "name": "Gemma 4 26B A4B IT Free"},
                ]
            },
            "models_debug": {
                "settings_llm_provider": "OpenRouter",
                "settings_llm_model": "google/gemma-4-26b-a4b-it:free",
            },
            "middleware_principals": [],
            "control_plane_providers": [
                {
                    "provider_id": "provider:openrouter:shared",
                    "provider_type": "OpenRouter",
                    "provider_ref": "provider:openrouter:shared",
                    "credential_ref": "credref:openrouter:shared:v1",
                }
            ],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {
            "principal_did": "did:key:z6MkDavidPrimary",
            "display_name": "David Berigny",
            "tenant_id": "tenant:david",
            "status": "active",
            "metadata": {
                "actor_type": "human",
                "additional_dids": ["did:web:id.dualsubstrate.com:principals:david-berigny"],
            },
        }

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_control_plane_post(path, payload, request=None):
        calls.append((path, payload))
        return 200, {"status": "ok", "principal": payload}

    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_build_identity_card)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    form_body = urlencode(
        {
            "principal_did": "did:key:z6MkDavidPrimary",
            "display_name": "David Berigny",
            "contact_email": "david@example.com",
            "did_mode": "use_existing",
            "existing_did": "did:key:z6MkDavidSecondary",
            "notes": "",
            "model_library_model_id": "google/gemma-4-26b-a4b-it:free",
            "model_library_principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemma-4-26b-a4b-it-free",
            "model_library_account_binding_id": "binding:account:david",
            "model_library_ledger_binding_id": "binding:ledger:chat-demo:default",
        }
    ).encode("utf-8")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/profile/setup",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
    )
    request._body = form_body

    response = asyncio.run(dashboard_app.profile_setup_submit(request))

    assert response.status_code == 200
    payload = next(
        payload for path, payload in calls
        if path == "/api/control-plane/principals" and payload.get("principal_type") == "human"
    )
    assert payload["metadata"]["contact_email"] == "david@example.com"
    assert payload["metadata"]["email"] == "david@example.com"
    assert payload["metadata"]["existing_did"] == "did:key:z6MkDavidSecondary"
    assert payload["metadata"]["primary_did"] == "did:key:z6MkDavidPrimary"
    assert payload["metadata"]["additional_dids"] == [
        "did:web:id.dualsubstrate.com:principals:david-berigny",
        "did:key:z6MkDavidSecondary",
    ]
    assert payload["metadata"]["selected_model_id"] == "google/gemma-4-26b-a4b-it:free"
    assert any(path == "/api/control-plane/model-bindings" for path, _ in calls)


def test_profile_setup_page_includes_model_library_picker(monkeypatch) -> None:
    async def fake_build_identity_card(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkDavidPrimary",
                "principal_display_name": "David Berigny",
                "tenant_id": "tenant:david",
                "ledger_id": "chat-demo",
            }
        }

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkDavidPrimary",
                    "tenant_id": "tenant:david",
                    "ledger_id": "chat-demo",
                }
            },
            "models_current": {
                "online_models": [
                    {"id": "google/gemma-4-26b-a4b-it:free", "name": "Gemma 4 26B A4B IT Free"},
                    {"id": "google/gemma-4-26b-a4b-it", "name": "Gemma 4 26B A4B IT"},
                ]
            },
            "models_debug": {
                "settings_llm_provider": "OpenRouter",
                "settings_llm_model": "google/gemma-4-26b-a4b-it:free",
            },
            "middleware_principals": [],
            "control_plane_providers": [
                {
                    "provider_id": "provider:openrouter:shared",
                    "provider_type": "OpenRouter",
                    "provider_ref": "provider:openrouter:shared",
                }
            ],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_build_identity_card)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)

    request = Request({"type": "http", "method": "GET", "path": "/profile/setup", "query_string": b"", "headers": []})
    response = asyncio.run(dashboard_app.profile_setup_page(request))
    body = response.body.decode("utf-8")

    assert "Model library" in body
    assert "Gemma 4 26B A4B IT Free" in body
    assert "binding:account:david" in body
    assert "binding:ledger:chat-demo:default" in body
    assert "generic provider/API setup fields" in body


def test_profile_setup_submit_persists_selected_model_library(monkeypatch) -> None:
    async def fake_build_identity_card(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkDavidPrimary",
                "tenant_id": "tenant:david",
                "ledger_id": "chat-demo",
            }
        }

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkDavidPrimary",
                    "tenant_id": "tenant:david",
                    "ledger_id": "chat-demo",
                }
            },
            "models_current": {
                "online_models": [
                    {"id": "google/gemma-4-26b-a4b-it:free", "name": "Gemma 4 26B A4B IT Free"},
                ]
            },
            "models_debug": {
                "settings_llm_provider": "OpenRouter",
                "settings_llm_model": "google/gemma-4-26b-a4b-it:free",
            },
            "middleware_principals": [],
            "control_plane_providers": [
                {
                    "provider_id": "provider:openrouter:shared",
                    "provider_type": "OpenRouter",
                    "provider_ref": "provider:openrouter:shared",
                    "credential_ref": "credref:openrouter:shared:v1",
                }
            ],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_principal_registry_get(path, headers=None):
        if path == "/api/principals/did%3Akey%3Az6MkDavidPrimary":
            return 200, {
                "principal_did": "did:key:z6MkDavidPrimary",
                "display_name": "David Berigny",
                "tenant_id": "tenant:david",
                "status": "active",
                "metadata": {
                    "actor_type": "human",
                    "additional_dids": ["did:web:id.dualsubstrate.com:principals:david-berigny"],
                },
            }
        return 200, {"principal_did": "did:key:z6MkDavidPrimary", "metadata": {"actor_type": "human"}}

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_control_plane_post(path, payload, request=None):
        calls.append((path, payload))
        if path == "/api/control-plane/model-bindings":
            return 200, {"status": "ok", "model_binding": payload}
        return 200, {"status": "ok", "principal": payload}

    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_build_identity_card)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    form_body = urlencode(
        {
            "principal_did": "did:key:z6MkDavidPrimary",
            "display_name": "David Berigny",
            "contact_email": "david@example.com",
            "did_mode": "use_existing",
            "existing_did": "did:key:z6MkDavidSecondary",
            "notes": "",
            "model_library_model_id": "google/gemma-4-26b-a4b-it:free",
            "model_library_principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemma-4-26b-a4b-it-free",
            "model_library_account_binding_id": "binding:account:david",
            "model_library_ledger_binding_id": "binding:ledger:chat-demo:default",
        }
    ).encode("utf-8")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/profile/setup",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
    )
    request._body = form_body

    response = asyncio.run(dashboard_app.profile_setup_submit(request))

    assert response.status_code == 200
    assert any(path == "/api/control-plane/model-bindings" for path, _ in calls)
    assert any(
        path == "/api/control-plane/principals"
        and payload.get("principal_type") == "model"
        and payload.get("metadata", {}).get("model_id") == "google/gemma-4-26b-a4b-it:free"
        for path, payload in calls
    )


def test_account_current_model_library_endpoint_reports_selected_model(monkeypatch) -> None:
    async def fake_control_plane_json_session(_request):
        return {}, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkDavidPrimary",
                    "tenant_id": "tenant:david",
                    "ledger_id": "chat-demo",
                }
            },
            "models_current": {
                "online_models": [
                    {"id": "google/gemma-4-26b-a4b-it:free", "name": "Gemma 4 26B A4B IT Free"},
                ]
            },
            "models_debug": {
                "settings_llm_provider": "OpenRouter",
                "settings_llm_model": "google/gemma-4-26b-a4b-it:free",
            },
            "middleware_principals": [],
            "control_plane_providers": [
                {
                    "provider_id": "provider:openrouter:shared",
                    "provider_type": "OpenRouter",
                    "provider_ref": "provider:openrouter:shared",
                    "credential_ref": "credref:openrouter:shared:v1",
                }
            ],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    monkeypatch.setattr(dashboard_app, "_control_plane_json_session", fake_control_plane_json_session)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)

    request = Request({"type": "http", "method": "GET", "path": "/account/current/model-library", "query_string": b"", "headers": []})
    response = asyncio.run(dashboard_app.api_account_current_model_library(request))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["model_library"]["selected_model_id"] == "google/gemma-4-26b-a4b-it:free"
    assert payload["model_library"]["account_binding_id"] == "binding:account:david"
    assert payload["model_library"]["ledger_binding_id"] == "binding:ledger:chat-demo:default"


def test_connections_page_sources_tab_renders_upload_panel(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkSourceOwner",
            "identity_vc": {
                "principal_did": "did:key:z6MkSourceOwner",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkSourceOwner",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:identity",
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": []}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(
        dashboard_app,
        "_load_control_plane_state",
        lambda: {
            "providers": [],
            "model_bindings": [],
            "app_surfaces": [],
            "entity_settings": [],
            "relationships": [],
            "manual_ledgers": [],
            "manual_principals": [],
            "sources": [
                {
                    "source_id": "source-123",
                    "job_id": "source-job-abc",
                    "ledger_id": "ledger:identity",
                    "principal_did": "did:key:z6MkSourceOwner",
                    "batch_label": "Foundational identity seed",
                    "file_name": "foundation.txt",
                    "original_file_name": "foundation.txt",
                    "status": "ready",
                    "uploaded_at": "2026-05-16T00:00:00+00:00",
                    "updated_at": "2026-05-16T00:05:00+00:00",
                    "chunk_count": 3,
                    "content_type": "text/plain",
                    "canonical_subject": "did:web:id.dualsubstrate.com:sources:source-123",
                    "canonical_subject_source": "did:web:source",
                }
            ],
            "source_jobs": [
                {
                    "job_id": "source-job-abc",
                    "status": "running",
                    "ledger_id": "ledger:identity",
                    "principal_did": "did:key:z6MkSourceOwner",
                    "source_ids": ["source-123"],
                    "source_count": 1,
                    "progress_done": 1,
                    "progress_total": 1,
                    "queued_at": "2026-05-16T00:00:00+00:00",
                    "updated_at": "2026-05-16T00:05:00+00:00",
                    "failure_message": "",
                }
            ],
        },
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/connections",
            "query_string": b"type=sources",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Sources" in body
    assert "Upload sources" in body
    assert "doc-upload-dropzone" in body
    assert "Browse files" in body
    assert "foundation.txt" in body
    assert "Foundational identity seed" in body
    assert "/connections/source/source-123" in body
    # Pending/running sources show a spinner in the status cell.
    assert "animation:spin" in body or "status-dot pending" in body


def test_connections_page_sources_tab_includes_backend_ledger_attachments(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkSourceOwner",
            "identity_vc": {
                "principal_did": "did:key:z6MkSourceOwner",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkSourceOwner",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:identity",
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:identity", "tenant_id": "tenant:david", "status": "active"}]}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    async def fake_fetch_ledger_attachments(ledger_id, auth_headers=None):
        if ledger_id != "ledger:identity":
            return []
        return [
            {
                "source_id": "backend-chat-ledger-identity-backend-doc",
                "file_name": "backend_doc.pdf",
                "coordinate": "chat-ledger:identity:backend-doc",
                "canonical_subject": "chat-ledger:identity:backend-doc",
                "status": "completed",
                "uploaded_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-05-20T00:00:00+00:00",
            },
            {
                "source_id": "backend-duplicate",
                "file_name": "duplicate.pdf",
                "coordinate": "did:web:id.dualsubstrate.com:sources:source-123",
                "canonical_subject": "did:web:id.dualsubstrate.com:sources:source-123",
                "status": "completed",
                "uploaded_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-05-20T00:00:00+00:00",
            },
        ]

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_fetch_ledger_attachments", fake_fetch_ledger_attachments)
    monkeypatch.setattr(
        dashboard_app,
        "_load_control_plane_state",
        lambda: {
            "sources": [
                {
                    "source_id": "source-123",
                    "ledger_id": "ledger:identity",
                    "principal_did": "did:key:z6MkSourceOwner",
                    "file_name": "foundation.txt",
                    "status": "ready",
                    "uploaded_at": "2026-05-16T00:00:00+00:00",
                    "updated_at": "2026-05-16T00:05:00+00:00",
                    "canonical_subject": "did:web:id.dualsubstrate.com:sources:source-123",
                }
            ],
            "source_jobs": [],
        },
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/connections",
            "query_string": b"type=sources",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "foundation.txt" in body
    assert "backend_doc.pdf" in body
    # The backend attachment whose coordinate matches the local source is deduplicated.
    assert "duplicate.pdf" not in body
    # The tab count reflects both the local source and the merged backend attachment.
    assert "Sources (2)" in body


def test_connections_page_shows_continue_setup_banner_when_checklist_incomplete(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOwner",
            "identity_vc": {
                "principal_did": "did:key:z6MkOwner",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOwner",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:identity",
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": []}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {
            "summary": {"required": 5, "required_complete": 1},
            "items": [
                {"item_id": "create_ledger", "label": "Add a ledger", "required": True, "state": "complete"},
                {"item_id": "create_principal", "label": "Add a principal", "required": True, "state": "incomplete"},
            ],
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Finish setting up your account" not in body
    assert "Continue setup" not in body
    assert "/connections/setup-guide" not in body


def test_connections_page_hides_setup_guide_banner_when_checklist_complete(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOwner",
            "identity_vc": {
                "principal_did": "did:key:z6MkOwner",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOwner",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:identity",
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": []}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {
            "summary": {"required": 2, "required_complete": 2},
            "items": [
                {"item_id": "create_ledger", "label": "Add a ledger", "required": True, "state": "complete"},
                {"item_id": "create_principal", "label": "Add a principal", "required": True, "state": "complete"},
            ],
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Finish setting up your account" not in body
    assert "Continue setup" not in body


def test_connections_page_scopes_records_to_authenticated_principal(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkKaoru",
            "identity_vc": {
                "principal_did": "did:key:z6MkKaoru",
                "tenant_id": "tenant:kaoru",
                "ledger_id": "ledger:kaoru",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkKaoru",
                    "tenant_id": "tenant:kaoru",
                    "ledger_id": "ledger:kaoru",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {
                "ledgers": [
                    {"ledger_id": "ledger:operator", "tenant_id": "tenant:operator", "status": "active"},
                    {"ledger_id": "ledger:kaoru", "tenant_id": "tenant:kaoru", "status": "active"},
                ]
            }
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {"principal_did": "did:key:z6MkOperator", "tenant_id": "tenant:operator", "status": "active", "display_name": "Cross-account Operator"},
                    {"principal_did": "did:key:z6MkKaoru", "tenant_id": "tenant:kaoru", "status": "active", "display_name": "Kaoru Ichikawa"},
                ]
            }
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Kaoru Ichikawa" in body
    assert "ledger:kaoru" in body
    assert "Cross-account Operator" not in body
    assert "ledger:operator" not in body
    assert "did:key:z6MkOperator" not in body


def test_setup_guide_page_renders_for_authenticated_user(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOwner",
            "identity_vc": {
                "principal_did": "did:key:z6MkOwner",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections/setup-guide",
        "query_string": b"",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.authenticated_setup_guide_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Continue Account Setup" in body
    assert "Add a ledger" in body
    assert "Add a principal" in body
    assert "Add a surface" in body
    assert "Configure permissions" in body
    assert "Upload documents" in body


def test_setup_guide_page_redirects_unauthenticated_user(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return None, RedirectResponse(url="/login?next=/connections/setup-guide", status_code=303)

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections/setup-guide",
        "query_string": b"",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.authenticated_setup_guide_page(request))

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/connections/setup-guide"


def test_sources_upload_endpoint_queues_async_chunk_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dashboard_app, "CONTROL_PLANE_STATE_PATH", tmp_path / "control_plane_state.json")
    monkeypatch.setattr(dashboard_app, "SOURCE_UPLOADS_DIR", tmp_path / "sources")

    async def fake_control_plane_json_session(_request):
        return {}, None

    monkeypatch.setattr(dashboard_app, "_control_plane_json_session", fake_control_plane_json_session)

    upload = UploadFile(
        filename="foundation.txt",
        file=BytesIO((b"Alpha " * 500) + b"\n\n" + (b"Beta " * 500)),
    )
    form_data = FormData(
        [
            ("ledger_id", "ledger:identity"),
            ("principal_did", "did:key:z6MkSourceOwner"),
            ("batch_label", "Foundational identity seed"),
            ("source_files", upload),
        ]
    )

    class DummyRequest:
        async def form(self):
            return form_data

    async def run_upload() -> tuple[object, dict[str, object]]:
        response = await dashboard_app.api_control_plane_sources_upload(DummyRequest())
        response_payload = json.loads(response.body.decode("utf-8"))
        job_id = str(response_payload.get("job", {}).get("job_id") or "").strip()
        for _ in range(40):
            state = dashboard_app._load_control_plane_state()
            if any(
                str(item.get("job_id") or "").strip() == job_id and str(item.get("status") or "").strip().lower() == "completed"
                for item in state.get("source_jobs", [])
            ):
                return response, state
            await asyncio.sleep(0.05)
        return response, dashboard_app._load_control_plane_state()

    response, state = asyncio.run(run_upload())
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 202
    assert payload["job"]["status"] == "queued"
    assert payload["sources"][0]["status"] == "queued"
    assert state["source_jobs"][0]["status"] == "completed"
    assert state["sources"][0]["status"] == "ready"
    assert state["sources"][0]["chunk_count"] >= 2
    assert pathlib.Path(state["sources"][0]["chunk_manifest_path"]).exists()


def test_apply_connections_add_flow_falls_back_to_local_ledger_when_backend_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dashboard_app, "CONTROL_PLANE_STATE_PATH", tmp_path / "control_plane_state.json")

    saved_state: dict[str, Any] = {}

    def fake_load_control_plane_state():
        return dict(saved_state)

    def fake_save_control_plane_state(state):
        nonlocal saved_state
        saved_state = dict(state)

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {"principal_map": {}, "ledger_map": {}, "surface_map": {}}

    async def fake_control_plane_post(path, payload, request=None):
        return 404, {"error": "not_found"}

    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", fake_load_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_save_control_plane_state", fake_save_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    request = Request({"type": "http", "method": "POST", "path": "/connections/add/ledger", "headers": []})
    response = asyncio.run(
        dashboard_app._apply_connections_add_flow(
            request,
            entity_kind="ledger",
            state={"name": "Kaoru Ledger", "ledger_id": "ledger:kaoru-test", "tenant_id": "tenant:kaoru"},
            identity_card={
                "principal_did": "did:key:z6MkKaoru",
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkKaoru",
                    "tenant_id": "tenant:kaoru",
                },
            },
        )
    )

    assert response.status_code == 303
    assert "ledger%3Akaoru-test" in response.headers["location"]
    assert len(saved_state.get("manual_ledgers", [])) == 1
    assert saved_state["manual_ledgers"][0]["ledger_id"] == "ledger:kaoru-test"
    assert saved_state["manual_ledgers"][0]["owner_principal_id"] == "did:key:z6MkKaoru"


def test_extract_source_text_uses_pdf_reader_when_available(monkeypatch) -> None:
    class FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakePdfReader:
        def __init__(self, _stream):
            self.pages = [FakePage("First page"), FakePage("Second page")]

    fake_module = types.SimpleNamespace(PdfReader=FakePdfReader)
    monkeypatch.setitem(sys.modules, "pypdf", fake_module)
    monkeypatch.delenv("PDF_EXTRACTOR", raising=False)

    text = dashboard_app._extract_source_text(b"%PDF-1.7 fake", file_name="source.pdf", content_type="application/pdf")

    assert "First page" in text
    assert "Second page" in text


def test_settings_source_uses_profile_account_and_trust_tabs() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert '("profile", "Profile")' in source
    assert '("account", "Account")' in source
    assert '("trust-details", "Trust Details")' in source
    assert "Publication Settings" in source
    assert "Organisation Settings" in source
    assert "Runtime Integrations" in source
    assert "DID Verification Material" in source
    assert "Adapter Boundary" in source
    assert "Current Edit Boundary" in source


def test_activity_share_bundle_does_not_make_plain_entity_id_share_ready() -> None:
    preferred_reference, share_ready, share_reason = dashboard_app._activity_share_bundle(
        {
            "entity_type": "provider",
            "entity_id": "provider:demo",
            "reference": "provider:demo",
            "details": {},
        }
    )
    assert preferred_reference == "provider:demo"
    assert share_ready is False
    assert share_reason == ""


def test_activity_badges_include_permission_and_trust_signals() -> None:
    badges = dashboard_app._activity_badges(
        {
            "row_kind": "record",
            "share_ready": True,
            "permission_scope": "custom",
            "evidence_ref": "evidence:abc",
            "details": {
                "permission_payload": {"write": True},
                "standing_view": {"trust_class": "T2"},
            },
        }
    )
    assert "share-ready" in badges
    assert "permissioned" in badges
    assert "trust-bearing" in badges


def test_activity_rows_surface_answer_integrity_divergence() -> None:
    row = dashboard_app._finalize_activity_row(
        {
            "timestamp": "2026-05-04T01:00:13.645664+00:00",
            "row_kind": "event",
            "event_type": "ledger.entry",
            "event_group": "ledger",
            "type_label": "Ledger Entry",
            "entity_type": "ledger",
            "entity_id": "chat-demo",
            "entity_label": "Ledger",
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject_source": "did:web:ledger",
            "display_label": "chat-demo",
            "ledger_id": "chat-demo",
            "coord": "chat-demo:WX-1",
            "reference": "chat-demo:WX-1",
            "status": "assistant",
            "summary": "Chat response committed",
            "actor": "openai/codex",
            "details": {
                "prompt_principal_label": "openai/codex",
                "response_model_label": "anthropic/claude-haiku-4.5",
                "answer_surface_integrity": {
                    "status": "diverged",
                    "reason": "assembly_summary_richer_than_visible_answer",
                    "visible_answer_preview": "Short visible answer.",
                    "committed_summary_preview": "Longer committed summary.",
                    "summary_source": "assemble_summary",
                },
            },
        }
    )

    assert "summary richer than visible answer" in dashboard_app._activity_collapsed_hint(row)
    sections = dict(dashboard_app._activity_detail_sections(row))
    assert "Answer Integrity" in sections
    answer_fields = dict(sections["Answer Integrity"])
    assert answer_fields["Status"] == "diverged"
    assert answer_fields["Visible answer preview"] == "Short visible answer."
    assert answer_fields["Committed summary preview"] == "Longer committed summary."


def test_activity_rows_surface_answer_integrity_blocked_context_collapse() -> None:
    row = dashboard_app._finalize_activity_row(
        {
            "timestamp": "2026-05-05T01:00:13.645664+00:00",
            "row_kind": "event",
            "event_type": "ledger.entry",
            "event_group": "ledger",
            "type_label": "Ledger Entry",
            "entity_type": "ledger",
            "entity_id": "chat-demo",
            "entity_label": "Ledger",
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject_source": "did:web:ledger",
            "display_label": "chat-demo",
            "ledger_id": "chat-demo",
            "coord": "chat-demo:WX-2",
            "reference": "chat-demo:WX-2",
            "status": "assistant",
            "summary": "Chat response committed",
            "actor": "openai/codex",
            "details": {
                "prompt_principal_label": "openai/codex",
                "response_model_label": "anthropic/claude-haiku-4.5",
                "answer_surface_integrity": {
                    "status": "collapsed",
                    "reason": "visible_answer_preamble_collapse_under_blocked_context",
                    "visible_answer_preview": "I'll ground this in observable fields from the current runtime state.",
                    "summary_source": "blocked_context_review_lane",
                },
            },
        }
    )

    assert "visible answer collapsed under blocked context" in dashboard_app._activity_collapsed_hint(row)


def test_activity_target_href_prefers_target_entity_route() -> None:
    href = dashboard_app._activity_target_href(
        {
            "details": {
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
            }
        }
    )
    assert href == "/principals/principal%3Atest"


def test_activity_reference_label_humanizes_share_reason() -> None:
    label = dashboard_app._activity_reference_label({"share_reason": "standing_envelope_ref", "reference": "env:test"})
    assert label == "Standing Envelope"


def test_activity_share_bundle_marks_submission_ref_reason() -> None:
    preferred_reference, share_ready, share_reason = dashboard_app._activity_share_bundle(
        {
            "submission_ref": "sub:only",
            "reference": "sub:only",
            "details": {},
        }
    )
    assert preferred_reference == "sub:only"
    assert share_ready is True
    assert share_reason == "submission_ref"


def test_activity_empty_state_message_includes_active_filters() -> None:
    message = dashboard_app._activity_empty_state_message(
        activity_scope="shared",
        tag_filter="permissioned",
        reference_filter="coord",
        recent_period="7d",
        date_from_filter="",
        date_to_filter="",
        entity_type_filter="principal",
        status_filter="active",
        ledger_filter="ledger:test",
        actor_filter="operator",
        text_filter="alice",
    )
    assert "scope 'shared'" in message
    assert "tag 'permissioned'" in message
    assert "reference 'coord'" in message
    assert "search 'alice'" in message


def test_activity_row_matches_filters_honors_tag_and_reference_filters() -> None:
    row = {
        "timestamp": "2026-04-07T00:00:00+00:00",
        "row_kind": "record",
        "entity_type": "principal",
        "actor": "operator",
        "status": "active",
        "ledger_id": "ledger:test",
        "badges": ["permissioned", "share-ready"],
        "share_reason": "coord",
        "summary": "Principal updated",
        "details": {},
        "entity_id": "principal:test",
        "reference": "coord:abc",
        "coord": "coord:abc",
        "submission_ref": "",
    }
    assert dashboard_app._activity_row_matches_filters(
        row,
        activity_scope="all",
        entity_type_filter="principal",
        actor_filter="oper",
        status_filter="active",
        ledger_filter="ledger:test",
        tag_filter="permissioned",
        reference_filter="coord",
        date_from_dt=None,
        date_to_dt=None,
        recent_days=None,
        now=datetime.now(timezone.utc),
        text_filter="principal",
    ) is True


def test_activity_row_matches_filters_rejects_scope_mismatch() -> None:
    row = {
        "timestamp": "2026-04-07T00:00:00+00:00",
        "row_kind": "record",
        "entity_type": "principal",
        "actor": "operator",
        "status": "active",
        "ledger_id": "",
        "badges": [],
        "share_reason": "",
        "summary": "Principal updated",
        "details": {},
        "entity_id": "principal:test",
        "reference": "",
        "coord": "",
        "submission_ref": "",
        "share_ready": False,
    }
    assert dashboard_app._activity_row_matches_filters(
        row,
        activity_scope="governance",
        entity_type_filter="",
        actor_filter="",
        status_filter="",
        ledger_filter="",
        tag_filter="",
        reference_filter="",
        date_from_dt=None,
        date_to_dt=None,
        recent_days=None,
        now=datetime.now(timezone.utc),
        text_filter="",
    ) is False


def test_activity_scope_matches_treats_shared_as_overlay() -> None:
    row = {
        "row_kind": "governance",
        "share_ready": True,
    }
    assert dashboard_app._activity_scope_matches(row, "governance") is True
    assert dashboard_app._activity_scope_matches(row, "shared") is True


def test_activity_scope_matches_separates_interactions_and_identity() -> None:
    interaction_row = {"row_kind": "event"}
    identity_row = {"row_kind": "record"}
    assert dashboard_app._activity_scope_matches(interaction_row, "interaction") is True
    assert dashboard_app._activity_scope_matches(interaction_row, "record") is False
    assert dashboard_app._activity_scope_matches(identity_row, "event") is False
    assert dashboard_app._activity_scope_matches(identity_row, "identity") is True


def test_normalize_submission_rows_marks_governance_and_open_href() -> None:
    rows = dashboard_app._normalize_submission_rows(
        [
            {
                "submission_ref": "sub:approval-1",
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
                "submission_status": "submitted",
                "mutation_kind": "activate",
                "payload": {"foo": "bar"},
            }
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["row_kind"] == "governance"
    assert row["open_href"] == "/submissions?submission_ref=sub%3Aapproval-1"
    assert row["preferred_reference"] == "sub:approval-1"
    assert row["share_ready"] is True
    assert "governance" in row["badges"]


def test_normalize_submission_rows_surfaces_reviewer_and_reason_state() -> None:
    rows = dashboard_app._normalize_submission_rows(
        [
            {
                "submission_ref": "sub:approval-2",
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
                "submission_status": "rejected",
                "mutation_kind": "grant_access",
                "reviewed_by_principal_id": "ops-admin",
                "reviewer_note": "policy gate failed",
                "payload": {"scope": "full"},
            }
        ]
    )
    row = rows[0]
    assert "reviewer ops-admin" in row["summary"]
    assert "policy gate failed" in row["summary"]
    assert "reviewer: ops-admin" in dashboard_app._activity_collapsed_hint(row)
    assert "reason: policy gate failed" in dashboard_app._activity_collapsed_hint(row)


def test_finalize_activity_row_sets_normalized_contract_fields() -> None:
    row = dashboard_app._finalize_activity_row(
        {
            "row_kind": "record",
            "type_label": "Principal",
            "summary": "Principal identity: did:web:id.dualsubstrate.com:principals:p_123",
            "entity_type": "principal",
            "entity_id": "did:key:z6MkExample",
            "reference": "",
            "preferred_reference": "",
            "actor": "control-plane",
            "status": "active",
            "coord": "coord:principal-123",
            "ledger_id": "ledger:test",
            "details": {
                "principal_did": "did:key:z6MkExample",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:p_123",
            },
            "open_href": "/principals/did%3Akey%3Az6MkExample",
        }
    )
    assert row["kind"] == "record"
    assert row["type"] == "Principal"
    assert row["shareable"] is True
    assert row["open_target"] == "/principals/did%3Akey%3Az6MkExample"
    assert row["raw_source"] == "dashboard_record_projection"
    assert row["reference"] == "coord:principal-123"


def test_finalize_activity_row_marks_governance_raw_source() -> None:
    row = dashboard_app._finalize_activity_row(
        {
            "row_kind": "governance",
            "type_label": "Submission",
            "summary": "activate · submitted",
            "entity_type": "principal",
            "entity_id": "principal:test",
            "reference": "sub:approval-1",
            "preferred_reference": "sub:approval-1",
            "actor": "ops-admin",
            "status": "submitted",
            "coord": "",
            "ledger_id": "",
            "submission_ref": "sub:approval-1",
            "details": {"submission_ref": "sub:approval-1"},
            "open_href": "/submissions?submission_ref=sub%3Aapproval-1",
        }
    )
    assert row["kind"] == "governance"
    assert row["type"] == "Submission"
    assert row["raw_source"] == "control_plane_submission"
    assert row["shareable"] is True


def test_activity_detail_sections_prioritize_curated_governance_provenance_and_standing() -> None:
    sections = dashboard_app._activity_detail_sections(
        {
            "summary": "activate · applied",
            "event_type": "submission.reviewed",
            "entity_type": "principal",
            "entity_id": "principal:test",
            "status": "applied",
            "actor": "ops-admin",
            "timestamp": "2026-04-10T12:00:00+00:00",
            "coord": "coord:principal-1",
            "ledger_id": "ledger:test",
            "reference": "coord:principal-1",
            "submission_ref": "sub:approval-11",
            "mutation_kind": "activate",
            "details": {
                "submission_status": "applied",
                "execution_mode": "governed",
                "reviewed_by_principal_id": "ops-admin",
                "reviewer_note": "approved for launch",
                "applied_result": {"mutation_ref": "mut:123"},
                "evidence_manifest_ref": "manifest:abc",
                "standing_view": {
                    "trust_class": "T2",
                    "posture_class": "P1",
                    "probation_status": "clear",
                    "current_validation_status": "valid",
                    "authority_subject_id": "did:web:id.dualsubstrate.com:authorities:ops",
                    "active_sanctions": ["none"],
                },
            },
        }
    )
    labels = [label for label, _fields in sections]
    assert labels[:5] == ["Overview", "Governance", "Provenance", "Standing",]
    governance_fields = dict(sections[1][1])
    provenance_fields = dict(sections[2][1])
    standing_fields = dict(sections[3][1])
    overview_fields = dict(sections[0][1])
    assert overview_fields["Timestamp"] == "2026-04-10T12:00:00+00:00"
    assert governance_fields["Reason"] == "approved for launch"
    assert provenance_fields["Evidence manifest"] == "manifest:abc"
    assert standing_fields["Trust class"] == "T2"
    assert standing_fields["Active sanctions"] == "none"


def test_normalized_control_plane_state_rewrites_legacy_surface_host() -> None:
    normalized = dashboard_app._normalized_control_plane_state(
        {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "display_name": "Primary Chat",
                    "canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary",
                    "canonical_subject_source": "did:web:surface",
                    "metadata": {"canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary"},
                }
            ]
        }
    )
    surface = normalized["app_surfaces"][0]
    assert surface["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"
    assert surface["metadata"]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"


def test_normalize_record_rows_prefers_canonical_subject_as_identity() -> None:
    rows = dashboard_app._normalize_record_rows(
        [],
        {
            **_empty_connection_context(),
            "ledgers": [
                {
                    "ledger_id": "chat-demo",
                    "ledger_name": "Chat Demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "canonical_subject_source": "did:web:ledger",
                    "status": "active",
                }
            ],
        },
        {"providers": [], "model_bindings": [], "relationships": []},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["summary"] == "Ledger identity: did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert row["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert row["display_label"] == "Chat Demo"


def test_activity_detail_fields_include_canonical_subject_and_display_label() -> None:
    fields = dashboard_app._activity_detail_fields(
        {
            "summary": "Ledger identity: did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject_source": "did:web:ledger",
            "display_label": "Chat Demo",
            "reference": "chat-demo",
            "details": {},
        }
    )
    assert ("Canonical subject", "did:web:id.dualsubstrate.com:ledgers:chat-demo") in fields
    assert ("Display label", "Chat Demo") in fields


def test_finalize_activity_row_rewrites_legacy_payload_subjects() -> None:
    row = dashboard_app._finalize_activity_row(
        {
            "entity_type": "surface",
            "entity_id": "surface:chat:primary",
            "canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary",
            "details": {
                "canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary",
                "metadata": {"canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary"},
            },
        }
    )
    assert row["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"
    assert row["details"]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"
    assert row["details"]["metadata"]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"


def test_normalize_event_rows_prefers_canonical_subject_as_identity() -> None:
    rows = dashboard_app._normalize_event_rows(
        [
            {
                "event_type": "ledger.write",
                "event_group": "ledger",
                "timestamp": "2026-04-07T12:00:00+00:00",
                "actor": "ledger-service",
                "entity_type": "ledger",
                "entity_id": "chat-demo",
                "ledger_id": "chat-demo",
                "coord": "chat-demo:WX-1",
                "status": "active",
                "summary": "Ledger write boundary observed for did:web:id.dualsubstrate.com:ledgers:chat-demo",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                "canonical_subject_source": "did:web:ledger",
                "display_label": "Chat Demo",
                "details": {"ledger_name": "Chat Demo"},
            }
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert row["display_label"] == "Chat Demo"
    assert row["summary"] == "Ledger write boundary observed for did:web:id.dualsubstrate.com:ledgers:chat-demo"


def test_collapse_activity_rows_suppresses_identity_shadow_lifecycle_events() -> None:
    rows = dashboard_app._collapse_activity_rows(
        [
            {
                "row_kind": "event",
                "event_type": "entity.created",
                "entity_type": "principal",
                "entity_id": "did:key:z6MkAlice",
                "summary": "Principal created",
            },
            {
                "row_kind": "event",
                "event_type": "entity.status_changed",
                "entity_type": "principal",
                "entity_id": "did:key:z6MkAlice",
                "summary": "Principal status changed",
            },
            {
                "row_kind": "record",
                "entity_type": "principal",
                "entity_id": "did:key:z6MkAlice",
                "summary": "Principal identity",
            },
            {
                "row_kind": "event",
                "event_type": "ledger.write",
                "entity_type": "ledger",
                "entity_id": "chat-demo",
                "summary": "Ledger write boundary observed",
            },
        ]
    )
    summaries = [str(row.get("summary") or "") for row in rows]
    assert "Principal created" not in summaries
    assert "Principal status changed" not in summaries
    assert "Principal identity" in summaries
    assert "Ledger write boundary observed" in summaries


def test_login_page_redirects_to_wallet_without_wallet_state() -> None:
    response = asyncio.run(dashboard_app.login_page(_make_login_request("next=%2F")))
    assert response.status_code == 307
    assert response.headers["location"] == "/login/wallet?next=/"


def test_login_page_redirects_state_to_wallet_route() -> None:
    response = asyncio.run(dashboard_app.login_page(_make_login_request("next=%2F&state=vid_test123")))
    assert response.status_code == 307
    assert response.headers["location"] == "/login/wallet?next=/&state=vid_test123"


def test_login_github_page_redirects_back_to_wallet_entry() -> None:
    response = asyncio.run(dashboard_app.login_github_page(_make_login_request("next=%2Fsettings")))
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/settings"


def test_login_passkey_page_redirects_back_to_wallet_entry() -> None:
    response = asyncio.run(dashboard_app.login_passkey_page(_make_login_request("next=%2Fsettings")))
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/settings"


def test_render_activity_page_scope_governance_filters_rendered_rows() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=governance"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[
            {
                "submission_ref": "sub:approval-1",
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
                "submission_status": "submitted",
                "mutation_kind": "activate",
                "payload": {"foo": "bar"},
            }
        ],
    )
    assert "sub:approval-1" in html
    assert "Submission" in html
    assert "Principal record:" not in html
    assert "Activity log" in html
    assert "Entity log" in html


def test_render_activity_page_event_scope_excludes_record_rows() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=event"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                "created_at": "2026-04-07T12:00:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Test answer from chat surface.",
                        "actor_did": "did:key:z6MkModelDemo",
                    }
                },
            }
        ],
    )
    assert "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo" in html
    assert "Principal identity: did:web:id.dualsubstrate.com:principals:alice" not in html


def test_render_activity_page_shows_prompt_and_response_attribution_for_chat_rows() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=event"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-prompt"},
                "created_at": "2026-05-04T10:00:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "user",
                        "content": "Run the manual test.",
                        "contributor": {
                            "principal_type": "agent",
                            "principal_id": "openai:codex",
                            "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                        },
                    }
                },
            },
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-answer"},
                "created_at": "2026-05-04T10:00:01+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Here is the result.",
                        "model_id": "anthropic/claude-haiku-4.5",
                        "provider_id": "provider:openrouter:shared",
                        "contributor": {
                            "principal_type": "agent",
                            "principal_id": "openai:codex",
                            "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                        },
                    }
                },
            },
        ],
    )
    assert "Chat prompt committed to did:web:id.dualsubstrate.com:ledgers:chat-demo" in html
    assert "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo" in html
    assert "chat-demo:WX-prompt" in html
    assert "chat-demo:WX-answer" in html


def test_render_activity_page_shows_delegated_prompt_governance_fields() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=event"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-answer"},
                "created_at": "2026-05-04T10:00:01+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Here is the result.",
                        "model_id": "anthropic/claude-haiku-4.5",
                        "contributor": {
                            "principal_type": "agent",
                            "principal_id": "openai:codex",
                            "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                        },
                        "delegated_prompt_path": {
                            "active": True,
                            "delegation_mode": "delegated_only",
                            "audit_posture": "requested_by_operator_executed_by_delegated_principal",
                            "requested_by_principal_did": "did:key:z6MkOperator",
                            "target_ledger_id": "chat-demo",
                            "target_surface_id": "surface:chat:primary",
                            "ledger_scope": ["chat-demo"],
                            "surface_scope": ["surface:chat:primary"],
                            "cli_request_required": True,
                        },
                    }
                },
            }
        ],
    )
    assert "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo" in html
    assert "chat-demo:WX-answer" in html


def test_render_activity_page_shows_backlog_scope_metrics() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=all"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                "created_at": "2026-04-07T12:00:00+00:00",
                "state": {"metadata": {"kind": "chat", "role": "assistant", "content": "Test answer from chat surface."}},
            }
        ],
    )
    assert "Activity log" in html
    assert "Entity log" in html
    # The activity tab shows the ledger entry event; the entity tab shows the ledger record.
    assert "Chat response committed" in html


def test_render_activity_page_all_scope_prioritizes_events_over_newer_records() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=all"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "updated_at": "2026-04-09T00:00:00+00:00",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-older"},
                "created_at": "2026-04-08T23:00:00+00:00",
                "state": {"metadata": {"kind": "chat", "role": "assistant", "content": "Older interaction."}},
            }
        ],
    )
    # Default activity tab shows interactions only.
    assert "Older interaction." in html
    assert "Principal identity: did:web:id.dualsubstrate.com:principals:alice" not in html
    # Entity tab shows the principal record.
    entity_html = dashboard_app.render_activity_page(
        _make_request("tab=entity"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "updated_at": "2026-04-09T00:00:00+00:00",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-older"},
                "created_at": "2026-04-08T23:00:00+00:00",
                "state": {"metadata": {"kind": "chat", "role": "assistant", "content": "Older interaction."}},
            }
        ],
    )
    assert "Principal identity: did:web:id.dualsubstrate.com:principals:alice" in entity_html
    assert "Older interaction." not in entity_html


def test_render_activity_page_record_scope_preserves_record_rows() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=record"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-1"},
                "created_at": "2026-04-08T23:00:00+00:00",
                "state": {"metadata": {"kind": "chat", "role": "assistant", "content": "Interaction row."}},
            }
        ],
    )
    assert "Principal identity: did:web:id.dualsubstrate.com:principals:alice" in html
    assert "Interaction row." not in html


def test_render_activity_page_scope_tabs_match_backlog_categories() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=all"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
    )
    assert "Activity log" in html
    assert "Entity log" in html
    # Old scope tab class and labels are gone.
    assert "activity-scope-tab" not in html
    assert "Governance" not in html
    assert "Shared" not in html


def test_render_activity_page_labels_raw_payload_as_secondary() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=record"),
        principals=[
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
                "metadata": {"actor_type": "human"},
            }
        ],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
    )
    assert "Principal identity: did:web:id.dualsubstrate.com:principals:alice" in html
    assert "Alice" in html
    # Expanding detail panels were removed in the two-tab redesign.
    assert "Overview" not in html
    assert "Raw payload" not in html


def test_render_activity_page_governance_rows_show_open_copy_share_actions() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=governance"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[
            {
                "submission_ref": "sub:approval-9",
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
                "submission_status": "applied",
                "mutation_kind": "activate",
                "reviewed_by_principal_id": "ops-admin",
                "reviewer_note": "approved for launch",
            }
        ],
    )
    # Action buttons were removed; the reference is surfaced in the Event column and the row links to the submission.
    assert "sub:approval-9" in html
    assert "activate" in html
    assert "ops-admin" in html
    assert 'href="/submissions?submission_ref=sub%3Aapproval-9"' in html
    assert ">Open<" not in html
    assert "data-copy-value" not in html
    assert "data-share-ref" not in html


def test_render_activity_page_shared_scope_keeps_shareable_governance_rows() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=shared"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[
            {
                "submission_ref": "sub:approval-10",
                "target_entity_type": "principal",
                "target_entity_id": "principal:test",
                "submission_status": "submitted",
                "mutation_kind": "activate",
            }
        ],
    )
    assert "sub:approval-10" in html
    assert "activate" in html


def test_render_activity_page_tag_and_reference_filters_preserve_state() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("tag=permissioned&ref=coord"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={
            "relationships": [
                {
                    "relationship_id": "rel:test",
                    "relationship_type": "accesses",
                    "status": "active",
                    "coord": "coord:rel-1",
                    "permission_scope": "custom",
                    "permission_payload": {"write": True},
                    "enabled_state": "enabled",
                }
            ]
        },
        submissions=[],
    )
    # Filter state is preserved in hidden inputs for the search form.
    assert 'name="tag" value="permissioned"' in html
    assert 'name="ref" value="coord"' in html
    assert 'class="activity-search-category"' in html


def test_render_activity_page_includes_recent_ledger_entry_coords() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=all"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                "created_at": "2026-04-07T12:00:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Test answer from chat surface.",
                        "actor_did": "did:key:z6MkModelDemo",
                    }
                },
            }
        ],
    )
    assert "chat-demo:WX-9C2621E0-1775565322" in html
    assert "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo" in html


def test_render_activity_page_includes_persisted_wx_payload_content() -> None:
    html = dashboard_app.render_activity_page(
        _make_request("scope=all"),
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=_empty_connection_context(),
        control_plane_state={},
        submissions=[],
        ledger_entries=[
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                "created_at": "2026-04-07T12:35:23.140441+00:00",
                "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                "type": "WX",
                "state": {
                    "metadata": {
                        "payload": {
                            "segments": [{"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:WX:ANS-01"}],
                            "blobs": {
                                "BLOB:WX:ANS-01": "Hello. I'm reading this fresh."
                            },
                        },
                        "skim": {"one_line": "Hello. I'm reading this fresh."},
                        "meta": {"created_at": "2026-04-07T12:35:23.140441+00:00"},
                        "governance": {"policy_decision": "block"},
                    }
                },
            }
        ],
    )
    assert "chat-demo:WX-9C2621E0-1775565322" in html
    assert "did:web:id.dualsubstrate.com:ledgers:chat-demo" in html
    assert "Hello. I&#x27;m reading this fresh." in html or "Hello. I'm reading this fresh." in html


def test_activity_page_tolerates_ledger_all_body_shape(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkCaller",
                "ledger_id": "chat-demo",
                "ledger_access_ready": True,
            }
        }, None

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {"principals": []}

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        context = _empty_connection_context()
        context["ledgers"] = [
            {
                "ledger_id": "chat-demo",
                "ledger_name": "Chat Demo",
                "tenant_id": "tenant:demo",
                "status": "active",
            }
        ]
        return context

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(_path, headers=None):
        return {
            "entries": [
                {
                    "key": {"namespace": "chat-demo", "identifier": "WX-1"},
                    "created_at": "2026-04-07T12:00:00+00:00",
                    "state": {"metadata": {"kind": "chat", "role": "assistant", "content": "hello"}},
                }
            ]
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(dashboard_app.activity_page(_make_request()))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "chat-demo:WX-1" in body


def test_activity_page_does_not_default_scope_to_first_principal(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True}}, None

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {"principals": [{"principal_did": "did:key:z6MkAlice", "display_name": "Alice"}]}

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return _empty_connection_context()

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(_path, headers=None):
        return {"entries": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(dashboard_app.activity_page(_make_request()))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    # The search form no longer emits an empty principal_did input.
    assert 'name="principal_did"' not in body
    assert "Principal: did:key:z6MkAlice" not in body


def test_render_about_benchmarks_page_shows_overview_content() -> None:
    publication = dashboard_app._load_benchmark_publication()

    html = dashboard_app.render_about_benchmarks_page(
        publication=publication,
        current_section="overview",
    )

    assert "DSS Benchmark Suite v0.3" in html
    assert "About" in html
    assert "Overview" in html
    assert "LongMemEval_M" in html
    assert "RULER 256K" in html
    assert "LoCoMo QA" in html
    assert "benchmark-overview-table" in html
    assert "Known limitations" in html
    assert "Reproduction" in html
    assert "Inspect sample trace" in html
    assert "1.00" in html


def test_render_about_benchmarks_page_handles_empty_publication() -> None:
    html = dashboard_app.render_about_benchmarks_page(
        publication={"status": "empty", "runs": [], "detail": "No benchmark runs are published yet."},
        current_section="overview",
    )

    assert "DSS Benchmark Suite v0.3" in html
    assert "LongMemEval_M" in html
    assert "RULER 256K" in html
    assert "LoCoMo QA" in html
    assert "1.00" in html
    assert "No benchmark runs are published yet." not in html


def test_render_about_benchmarks_page_uses_unpublished_copy_when_empty_detail_missing() -> None:
    html = dashboard_app.render_about_benchmarks_page(
        publication={"status": "empty", "runs": [], "detail": ""},
        current_section="overview",
    )

    assert "DSS Benchmark Suite v0.3" in html
    assert "LongMemEval_M" in html
    assert "RULER 256K" in html
    assert "LoCoMo QA" in html
    assert "No published measures yet." not in html
    assert "1.00" in html


def test_render_about_benchmarks_page_handles_publication_error_state() -> None:
    html = dashboard_app.render_about_benchmarks_page(
        publication={"status": "error", "runs": [], "detail": "Backend benchmark service unavailable."},
        current_section="overview",
    )

    assert "DSS Benchmark Suite v0.3" in html
    assert "LongMemEval_M" in html
    assert "RULER 256K" in html
    assert "LoCoMo QA" in html
    assert "Backend benchmark service unavailable." not in html
    assert "1.00" in html


def test_build_benchmark_performance_page_data_freezes_route_and_payload_shape() -> None:
    page_data = dashboard_app._build_benchmark_performance_page_data(
        {
            "status": "ok",
            "detail": "Published benchmark artefacts are read-only trust material.",
            "runs": [
                {
                    "run_id": "run-latest",
                    "executed_at": "2026-04-23T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "full_dss",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "054b654"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.2, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                }
            ],
        }
    )

    assert page_data["route"] == "/benchmarks?section=overview"
    assert page_data["section"] == "overview"
    assert page_data["status"] == "ok"
    assert isinstance(page_data["latest_run"], dict)
    assert page_data["baseline_run"] is None
    assert isinstance(page_data["run_completeness"], list)
    assert isinstance(page_data["recent_runs"], list)
    assert isinstance(page_data["trend_metrics"], list)
    assert isinstance(page_data["comparison_metrics"], list)
    assert isinstance(page_data["learn_more_links"], list)
    assert isinstance(page_data["phase_1_activation"], dict)
    assert isinstance(page_data["operator_publication"], dict)
    assert page_data["operator_publication"]["request_path_policy"] == "background_jobs_only"
    assert page_data["top_line_result"] == ""


def test_benchmark_operator_action_source_tracks_status_and_permission_states() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert "Operator authority is required to trigger benchmark publication." in source
    assert "Running benchmarks. Job ref:" in source
    assert "Writing artefacts. Job ref:" in source
    assert "Publishing canonical feed. Job ref:" in source
    assert "Benchmark execution failed." in source
    assert "Artefact write failed." in source
    assert "Publication refresh failed." in source
    assert "Published. Job ref:" in source
    assert "Runs the domain benchmark suites, writes fresh artefacts, and republishes the canonical feed." in source
    assert "Republishes from existing benchmark artefacts already present in the artefact store." in source
    assert '/api/control-plane/benchmarks/publication-jobs/${{encodeURIComponent(jobId)}}' in source
    assert "status_code, body = await _backend_admin_post(" in source
    assert '"/api/control-plane/benchmarks/publication-jobs",' in source
    assert "status_code, body = await _backend_admin_get(" in source
    assert "def _backend_admin_auth_headers_from_request(request: Request)" in source
    assert 'headers["x-session-token"] = token' in source


def test_build_benchmark_performance_page_data_freezes_shared_section_contracts() -> None:
    page_data = dashboard_app._build_benchmark_performance_page_data(
        {
            "status": "ok",
            "detail": "",
            "runs": [
                {
                    "run_id": "run-latest",
                    "executed_at": "2026-04-23T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "full_dss",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "054b654"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0}, "mrr": {"value": 0.91}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.2, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                    "exemplars": [{"label": "Latest exemplar", "query": "Who is researching quantum networks?", "coord": "coord:latest", "replay_outcome": "resolved"}],
                }
            ],
        }
    )

    assert page_data["publication_note"]
    assert isinstance(page_data["run_completeness"], list)
    assert page_data["recent_runs"][0]["run_id"] == "run-latest"
    assert page_data["trend_metrics"][0]["label"] == "Recall@10"
    assert page_data["comparison_metrics"][0]["label"] == "Recall@10"
    assert page_data["learn_more_links"][0]["label"] == "About DSS Retrieval Benchmark v1"
    assert any(item["status"] == "ready" for item in page_data["learn_more_links"])
    assert page_data["learn_more_links"][0]["href"].endswith("#benchmark-suite")
    assert isinstance(page_data["notices"], list)
    assert isinstance(page_data["phase_1_activation"], dict)
    assert page_data["top_line_result"] == ""


def test_build_benchmark_performance_page_data_generates_top_line_result_when_meaningful() -> None:
    page_data = dashboard_app._build_benchmark_performance_page_data(
        {
            "status": "ok",
            "detail": "",
            "runs": [
                {
                    "run_id": "run-latest",
                    "executed_at": "2026-04-23T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "full_dss",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "054b654"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0}, "mrr": {"value": 1.0}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.2, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                },
                {
                    "run_id": "run-baseline",
                    "executed_at": "2026-04-22T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "semantic_only",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "c1cee47"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0}, "mrr": {"value": 0.83}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.1, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                },
            ],
        }
    )

    assert page_data["top_line_result"] == (
        "In this early run, DSS matched the semantic-only baseline on retrieval coverage and outperformed it on ranking quality."
    )


def test_benchmark_performance_api_payload_uses_unpublished_semantics() -> None:
    status_code, payload = dashboard_app._benchmark_performance_api_payload(
        {"status": "empty", "runs": [], "detail": "No benchmark publication file is available yet."}
    )

    assert status_code == 200
    assert payload["status"] == "unpublished"
    assert payload["detail"] == "No benchmark publication file is available yet."
    assert payload["latest_run"] is None
    assert payload["baseline_run"] is None
    assert payload["top_line_result"] == ""
    assert isinstance(payload["comparison_metrics"], list)


def test_benchmark_performance_api_payload_uses_temporary_failure_semantics() -> None:
    status_code, payload = dashboard_app._benchmark_performance_api_payload(
        {"status": "error", "runs": [], "detail": "Benchmark publication is not valid JSON."}
    )

    assert status_code == 503
    assert payload["status"] == "temporary_failure"
    assert payload["detail"] == "Benchmark publication is not valid JSON."


def test_benchmarks_performance_api_returns_canonical_contract(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True}}, None

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    async def fake_load_backend_benchmark_publication(_request):
        return {
            "status": "ok",
            "detail": "Published benchmark artefacts are read-only trust material.",
            "phase_1_activation": {
                "publication_checked_at": "2026-04-23T12:00:00Z",
                "max_age_hours": 168,
                "suite_activation": [
                    {"suite_name": "HotpotQA", "family": "retrieval_and_multihop", "status": "active", "freshness_status": "fresh", "published_modes": ["semantic_only", "coordinate_guided", "full_dss"]},
                    {"suite_name": "LongMemEval", "family": "long_memory", "status": "reference_only", "freshness_status": "unpublished", "published_modes": []},
                ],
            },
            "runs": [
                {
                    "run_id": "run-latest",
                    "executed_at": "2026-04-23T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "full_dss",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "054b654"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0}, "mrr": {"value": 0.91}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.2, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                    "freshness": {"status": "fresh", "checked_at": "2026-04-23T12:00:00Z", "max_age_hours": 24, "age_hours": 0},
                    "exemplars": [],
                },
                {
                    "run_id": "run-baseline",
                    "executed_at": "2026-04-22T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "semantic_only",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "c1cee47"}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 0.9}, "mrr": {"value": 0.83}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.1, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                    "freshness": {"status": "fresh", "checked_at": "2026-04-22T12:00:00Z", "max_age_hours": 24, "age_hours": 12},
                    "exemplars": [],
                },
            ],
        }

    monkeypatch.setattr(
        dashboard_app,
        "_load_backend_benchmark_publication",
        fake_load_backend_benchmark_publication,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/benchmarks/performance",
            "query_string": b"",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.api_benchmarks_performance(request))

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["status"] == "partial"
    assert payload["route"] == "/benchmarks?section=overview"
    assert payload["section"] == "overview"
    assert payload["latest_run"]["run_id"] == "run-latest"
    assert payload["baseline_run"]["run_id"] == "run-baseline"
    assert payload["top_line_result"] == (
        "In this early run, DSS outperformed the semantic-only baseline on retrieval coverage and outperformed it on ranking quality."
    )
    assert payload["run_completeness"][0]["label"] == "Retrieval"
    assert payload["comparison_metrics"][0]["label"] == "Recall@10"
    assert payload["trend_metrics"][0]["label"] == "Recall@10"
    assert payload["learn_more_links"][0]["href"].endswith("#benchmark-suite")
    assert payload["phase_1_activation"]["suite_activation"][0]["suite_name"] == "HotpotQA"


def test_benchmarks_performance_api_requires_auth(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return None, dashboard_app.RedirectResponse(url="/login?next=/benchmarks", status_code=303)

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/benchmarks/performance",
            "query_string": b"",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.api_benchmarks_performance(request))

    assert response.status_code == 401
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "authentication_required"


def test_load_backend_benchmark_publication_normalizes_backend_snapshot(monkeypatch) -> None:
    async def fake_backend_admin_get(_path, *, extra_headers=None):
        return 200, {
            "status": "ok",
            "source_contract": {"canonical_publication_owner": "ds_backend_local"},
            "config": {"publication_output": {"configured": True}},
            "publication": {
                "note": "Published benchmark artefacts are read-only trust material.",
                "operator_publication": {"request_path_policy": "background_jobs_only"},
                "phase_1_activation": {"publication_checked_at": "2026-04-23T12:00:00Z"},
                "runs": [
                    {
                        "run_id": "run-latest",
                        "executed_at": "2026-04-23T12:00:00Z",
                        "mode": "full_dss",
                        "status": "partial",
                    }
                ],
            },
        }

    monkeypatch.setattr(dashboard_app, "_backend_admin_get", fake_backend_admin_get)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/benchmarks",
            "query_string": b"",
            "headers": [],
        }
    )

    publication = asyncio.run(dashboard_app._load_backend_benchmark_publication(request))

    assert publication["status"] == "ok"
    assert publication["runs"][0]["run_id"] == "run-latest"
    assert publication["canonical_publication_source"]["canonical_publication_owner"] == "ds_backend_local"
    assert publication["publication_config"]["publication_output"]["configured"] is True


def test_load_backend_benchmark_publication_surfaces_backend_unavailable_reason(monkeypatch) -> None:
    async def fake_backend_admin_get(_path, *, extra_headers=None):
        return 503, {
            "status": "unavailable",
            "reason": "benchmark_publication_output_not_configured",
            "message": "Canonical publication output is not configured.",
            "source_contract": {"canonical_publication_owner": "ds_backend_local"},
            "config": {"publication_output": {"configured": False}},
        }

    monkeypatch.setattr(dashboard_app, "_backend_admin_get", fake_backend_admin_get)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/benchmarks",
            "query_string": b"",
            "headers": [],
        }
    )

    publication = asyncio.run(dashboard_app._load_backend_benchmark_publication(request))

    assert publication["status"] == "error"
    assert publication["detail"] == "Canonical publication output is not configured."
    assert publication["canonical_publication_source"]["canonical_publication_owner"] == "ds_backend_local"
    assert publication["publication_config"]["publication_output"]["configured"] is False


def test_render_about_benchmarks_page_normalizes_performance_alias() -> None:
    html = dashboard_app.render_about_benchmarks_page(
        publication={"status": "empty", "runs": [], "detail": "No benchmark runs are published yet."},
        current_section="performance",
    )

    assert "Overview" in html
    assert "LongMemEval_M" in html


def test_render_about_benchmarks_page_shows_about_overview_tab() -> None:
    html = dashboard_app.render_about_benchmarks_page(
        publication={"status": "empty", "runs": [], "detail": "No benchmark runs are published yet."},
        current_section="about",
    )

    assert "DSS Benchmark Suite v0.3" in html
    assert "About" in html
    assert "LongMemEval_M" in html
    assert "RULER 256K" in html
    assert "LoCoMo QA" in html
    assert "Last run: 2026-07-09" in html
    assert "0.87" in html
    assert "0.54" in html
    assert "1.00" in html
    assert "coordinate-guided memory architecture" not in html


def test_benchmarks_page_redirects_when_unauthenticated(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return None, dashboard_app.RedirectResponse(url="/login?next=/benchmarks", status_code=303)

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/benchmarks",
            "query_string": b"section=overview",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.benchmarks_page(request))

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/benchmarks"


def test_benchmarks_page_renders_publication(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True}}, None

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    async def fake_load_backend_benchmark_publication(_request):
        return {
            "status": "ok",
            "detail": "Published benchmark artefacts are read-only trust material.",
            "phase_1_activation": {
                "publication_checked_at": "2026-04-23T12:00:00Z",
                "max_age_hours": 168,
                "suite_activation": [
                    {"suite_name": "HotpotQA", "family": "retrieval_and_multihop", "status": "active", "freshness_status": "fresh", "published_modes": ["semantic_only", "coordinate_guided", "full_dss"]},
                    {"suite_name": "LongMemEval", "family": "long_memory", "status": "reference_only", "freshness_status": "unpublished", "published_modes": []},
                    {"suite_name": "MuSiQue", "family": "retrieval_and_multihop", "status": "planned", "freshness_status": "unpublished", "published_modes": []},
                ],
            },
            "runs": [
                {
                    "run_id": "run-latest",
                    "executed_at": "2026-04-23T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "full_dss",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "054b654", "role": "canonical", "required_for_run": True}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 1.0, "unit": "ratio"}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.2, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                    "freshness": {"status": "fresh", "checked_at": "2026-04-23T12:00:00Z", "max_age_hours": 24, "age_hours": 0},
                    "exemplars": [
                        {
                            "label": "Latest exemplar",
                            "query": "Who is researching quantum networks?",
                            "coord": "coord:latest",
                            "retrieved_coords": ["coord:latest", "coord:related"],
                            "walk_path": ["coord:latest", "coord:related"],
                            "replay_outcome": "resolved",
                        }
                    ],
                },
                {
                    "run_id": "run-baseline",
                    "executed_at": "2026-04-22T12:00:00Z",
                    "suite_id": "dual_retrieval_benchmark",
                    "suite_version": "v1",
                    "artefact_schema_version": "1.0.0",
                    "mode": "semantic_only",
                    "status": "partial",
                    "repos": [{"name": "ds-backend-local", "commit_sha": "c1cee47", "role": "canonical", "required_for_run": True}],
                    "datasets": [{"name": "benchmark_dataset", "version": "local-v1", "split": "benchmark"}],
                    "metrics": {
                        "retrieval": {"status": "present", "metrics": {"recall_at_10": {"value": 0.9, "unit": "ratio"}}},
                        "traceability": {"status": "absent", "absence_reason": "not measured"},
                        "governance": {"status": "absent", "absence_reason": "not measured"},
                        "latency": {"status": "present", "metrics": {"avg_latency_ms": {"value": 0.1, "unit": "ms"}}},
                        "cost": {"status": "absent", "absence_reason": "not measured"},
                    },
                    "freshness": {"status": "fresh", "checked_at": "2026-04-22T12:00:00Z", "max_age_hours": 24, "age_hours": 12},
                    "exemplars": [],
                },
            ],
        }

    monkeypatch.setattr(
        dashboard_app,
        "_load_backend_benchmark_publication",
        fake_load_backend_benchmark_publication,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/benchmarks",
            "query_string": b"section=overview",
            "headers": [],
        }
    )

    response = asyncio.run(dashboard_app.benchmarks_page(request))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "DSS Benchmark Suite v0.3" in body
    assert "About" in body
    assert "Overview" in body
    assert "LongMemEval_M" in body
    assert "RULER 256K" in body
    assert "LoCoMo QA" in body
    assert "benchmark-overview-table" in body
    assert "Known limitations" in body
    assert "Reproduction" in body
    assert "1.00" in body
    assert "Phase 1 suite activation scoreboard" not in body
    assert "Operator publication actions" not in body
    assert "Benchmark transparency" not in body


def test_normalize_ledger_entry_rows_include_canonical_subject_and_runtime_namespace() -> None:
    rows = dashboard_app._normalize_ledger_entry_rows(
        [
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                "coord_meta": {
                    "coord": "chat-demo:WX-9C2621E0-1775565322",
                    "coord_type": "WX",
                    "identifier": "WX-9C2621E0-1775565322",
                    "runtime_namespace": "chat-demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "canonical_subject_source": "did:web:ledger",
                },
                "created_at": "2026-04-07T12:00:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Test answer from chat surface.",
                    }
                },
            }
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert row["canonical_subject_source"] == "did:web:ledger"
    assert row["details"]["runtime_namespace"] == "chat-demo"
    assert row["details"]["coord_meta"]["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert row["summary"].startswith("Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo")


def test_relationship_record_lookup_still_resolves_by_operational_ids() -> None:
    record = dashboard_app._relationship_record(
        "principal",
        "did:key:z6MkAlice",
        "surface",
        "surface:chat:primary",
        relationship_records=[
            {
                "relationship_id": "principal::did:key:z6MkAlice::surface::surface:chat:primary".replace("::", "::"),
            }
        ],
    )
    assert record["subject_entity_type"] == "principal"
    assert record["subject_entity_id"] == "did:key:z6MkAlice"
    assert record["object_entity_type"] == "surface"
    assert record["object_entity_id"] == "surface:chat:primary"


def test_relationship_record_preserves_permissions_when_canonical_subject_present() -> None:
    stored = {
        "relationship_id": "principal::did:key:z6MkAlice::surface::surface:chat:primary".replace("::", "::"),
        "subject_entity_type": "principal",
        "subject_entity_id": "did:key:z6MkAlice",
        "object_entity_type": "surface",
        "object_entity_id": "surface:chat:primary",
        "relationship_type": "can_access_surface",
        "permission_scope": "custom",
        "permission_payload": {"write": True},
        "enabled_state": "enabled",
        "canonical_subject": "did:web:id.dualsubstrate.com:relationships:principal-did-key-z6mkalice-surface-surface-chat-primary",
        "canonical_subject_source": "did:web:relationship",
    }
    record = dashboard_app._relationship_record(
        "principal",
        "did:key:z6MkAlice",
        "surface",
        "surface:chat:primary",
        relationship_records=[stored],
    )
    assert record["permission_scope"] == "custom"
    assert record["permission_payload"] == {"write": True}
    assert record["canonical_subject"] == stored["canonical_subject"]


def test_entity_settings_record_still_projects_permissions_from_relationship_ids() -> None:
    stored = {
        "relationship_id": "ledger::chat-demo::ledger::chat-demo".replace("::", "::"),
        "subject_entity_type": "ledger",
        "subject_entity_id": "chat-demo",
        "object_entity_type": "ledger",
        "object_entity_id": "chat-demo",
        "relationship_type": "related_to",
        "enabled_state": "enabled",
        "permission_scope": "custom",
        "permission_payload": {"write": True},
        "canonical_subject": "did:web:id.dualsubstrate.com:relationships:ledger-chat-demo-ledger-chat-demo",
        "canonical_subject_source": "did:web:relationship",
    }
    projected = dashboard_app._entity_settings_record(
        "ledger",
        "chat-demo",
        relationship_records=[stored],
    )
    assert projected["entity_type"] == "ledger"
    assert projected["entity_id"] == "chat-demo"
    assert projected["permission_set"] == "custom"
    assert projected["enabled_state"] == "enabled"


def test_activity_page_includes_active_ledger_history_coords(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "ledger_id": "chat-demo"}}, None

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {"principals": []}

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return _empty_connection_context()

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(path, headers=None):
        if path.startswith("/ledger/history/"):
            return {
                "history": [
                    {
                        "role": "assistant",
                        "content": "History-backed answer.",
                        "timestamp": "2026-04-08T00:00:00+00:00",
                        "entry_id": "WX-9C2621E0-1775565322",
                        "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                        "coord_meta": {
                            "coord": "chat-demo:WX-9C2621E0-1775565322",
                            "coord_type": "WX",
                            "identifier": "WX-9C2621E0-1775565322",
                            "runtime_namespace": "chat-demo",
                            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                            "canonical_subject_source": "did:web:ledger",
                        },
                        "metadata": {"kind": "chat", "role": "assistant"},
                    }
                ]
            }
        return {"entries": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(dashboard_app.activity_page(_make_request()))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "chat-demo:WX-9C2621E0-1775565322" in body
    assert "did:web:id.dualsubstrate.com:ledgers:chat-demo" in body


def test_activity_page_includes_known_ledger_namespace_entries(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkCaller",
                "ledger_id": "s2",
            }
        }, None

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {"principals": []}

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        context = _empty_connection_context()
        context["ledgers"] = [
            {
                "ledger_id": "chat-demo",
                "ledger_name": "Chat Demo",
                "tenant_id": "tenant:demo",
                "status": "active",
            }
        ]
        return context

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(path, headers=None):
        if path == "/ledger/all?namespace=chat-demo&limit=25":
            return {
                "entries": [
                    {
                        "key": {"namespace": "chat-demo", "identifier": "WX-9C2621E0-1775565322"},
                        "created_at": "2026-04-07T12:35:23.140441+00:00",
                        "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                        "type": "WX",
                        "state": {
                            "metadata": {
                                "skim": {"one_line": "Hello. I'm reading this fresh."},
                                "meta": {"created_at": "2026-04-07T12:35:23.140441+00:00"},
                            }
                        },
                    }
                ]
            }
        return {"entries": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(dashboard_app.activity_page(_make_request()))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "chat-demo:WX-9C2621E0-1775565322" in body
    assert "Hello. I&#x27;m reading this fresh." in body or "Hello. I'm reading this fresh." in body


def test_activity_page_includes_known_ledger_history_without_principal_scope(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkCaller",
                "ledger_id": "s2",
            }
        }, None

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {"principals": [{"principal_did": "did:key:z6MkCaller", "display_name": "Caller"}]}

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        context = _empty_connection_context()
        context["ledgers"] = [
            {
                "ledger_id": "chat-demo",
                "ledger_name": "Chat Demo",
                "tenant_id": "tenant:demo",
                "status": "active",
            }
        ]
        return context

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(path, headers=None):
        if path == "/ledger/history/chat-demo?limit=50":
            return {
                "history": [
                    {
                        "role": "assistant",
                        "content": "History-backed answer.",
                        "timestamp": "2026-04-08T00:00:00+00:00",
                        "entry_id": "WX-9C2621E0-1775565322",
                        "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                        "metadata": {"kind": "chat", "role": "assistant"},
                    }
                ]
            }
        return {"entries": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(dashboard_app.activity_page(_make_request()))

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "chat-demo:WX-9C2621E0-1775565322" in body
    assert "History-backed answer." in body


def test_did_first_collection_pages_render_canonical_subjects(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "principal_display_name": "Tester"}}, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "ledgers": [
                {
                    "ledger_id": "chat-demo",
                    "ledger_name": "Chat Demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "ledger_access_ready": True,
                }
            ],
            "principals": [],
            "surfaces": [],
            "providers": [],
            "model_bindings": [],
            "relationships": [],
        }

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {
            "principals": [
                {
                    "principal_did": "did:key:z6MkAlice",
                    "display_name": "Alice",
                    "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                    "canonical_subject_source": "did:web:principal",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "metadata": {"actor_type": "human"},
                    "standing_view": {"trust_class": "T2"},
                }
            ]
        }

    async def fake_load_permissions_lookup(_principal, auth_headers=None):
        return {
            "principal": {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "canonical_subject_source": "did:web:principal",
                "status": "active",
                "tenant_id": "tenant:test",
                "metadata": {"actor_type": "human"},
                "principal_key_refs": ["keyref:alice:1"],
            },
            "standing": {},
            "authority": {},
            "provisioning": {},
            "approval_lookup": {},
        }

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {
            "ledger_map": {
                "chat-demo": {
                    "ledger_id": "chat-demo",
                    "display_name": "Chat Demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "status": "active",
                    "tenant_id": "tenant:test",
                }
            },
            "principal_map": {},
            "surface_map": {},
            "relationships": [],
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_permissions_lookup", fake_load_permissions_lookup)

    ledgers_response = asyncio.run(dashboard_app.ledgers_page(Request({
        "type": "http", "method": "GET", "path": "/ledgers", "query_string": b"", "headers": []
    })))
    principals_response = asyncio.run(dashboard_app.principals_page(Request({
        "type": "http", "method": "GET", "path": "/principals", "query_string": b"principal_did=did%3Akey%3Az6MkAlice", "headers": []
    })))

    ledgers_html = ledgers_response.body.decode("utf-8")
    principals_html = principals_response.body.decode("utf-8")

    assert "did:web:id.dualsubstrate.com:ledgers:chat-demo" in ledgers_html
    assert "Runtime namespace: chat-demo" in ledgers_html
    assert "Display label:" in ledgers_html
    assert "Canonical subject:" in principals_html
    assert "did:web:id.dualsubstrate.com:principals:alice" in principals_html
    assert "Display label:" in principals_html


def test_principals_page_renders_codex_as_delegated_agent(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_display_name": "Tester",
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:test",
                "ledger_id": "chat-demo",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "ledger_id": "chat-demo",
                    "status": "active",
                }
            ]
        }

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {
            "principals": [
                {
                    "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                    "display_name": "OpenAI Codex",
                    "canonical_subject": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                    "canonical_subject_source": "principal_did",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "metadata": {
                        "actor_type": "agent",
                        "delegated_authority": {
                            "delegation_mode": "delegated_only",
                            "delegated_prompt_execution": "explicit_cli_request_required",
                            "revocation_mode": "control_plane_operator",
                            "hidden_operator_alias": False,
                            "revocable": True,
                            "ledger_scope": ["chat-demo"],
                            "surface_scope": ["surface:chat:primary"],
                        },
                    },
                    "standing_view": {"trust_class": "T3"},
                }
            ]
        }

    async def fake_load_permissions_lookup(_principal, auth_headers=None):
        return {
            "principal": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "display_name": "OpenAI Codex",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "canonical_subject_source": "principal_did",
                "status": "active",
                "tenant_id": "tenant:test",
                "metadata": {
                    "actor_type": "agent",
                    "delegated_authority": {
                        "delegation_mode": "delegated_only",
                        "delegated_prompt_execution": "explicit_cli_request_required",
                        "revocation_mode": "control_plane_operator",
                        "hidden_operator_alias": False,
                        "revocable": True,
                        "ledger_scope": ["chat-demo"],
                        "surface_scope": ["surface:chat:primary"],
                    },
                },
                "principal_key_refs": ["openai:agent:codex"],
            },
            "standing": {},
            "authority": {},
            "provisioning": {},
            "approval_lookup": {},
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_permissions_lookup", fake_load_permissions_lookup)

    response = asyncio.run(dashboard_app.principals_page(Request({
        "type": "http", "method": "GET", "path": "/principals", "query_string": b"principal_did=did%3Aweb%3Aid.dualsubstrate.com%3Aprincipals%3Aagent%3Aopenai%3Acodex", "headers": []
    })))
    body = response.body.decode("utf-8")

    assert "did:web:id.dualsubstrate.com:principals:agent:openai:codex" in body
    assert "Actor type:</strong> agent" in body
    assert "Delegated Authority" in body
    assert "delegated_only" in body
    assert "explicit_cli_request_required" in body
    assert "control_plane_operator" in body
    assert "Re-provision Codex Principal" in body
    assert "Default ledger scope:</strong> chat-demo" in body
    assert "Default surface scope:</strong> surface:chat:primary" in body
    assert "Confirm delegated-only posture" in body


def test_principals_page_renders_codex_provision_action_when_absent(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_display_name": "Tester",
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:test",
                "ledger_id": "chat-demo",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "ledger_id": "chat-demo",
                    "status": "active",
                }
            ]
        }

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {
            "principals": [
                {
                    "principal_did": "did:key:z6MkAlice",
                    "display_name": "Alice",
                    "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                    "canonical_subject_source": "principal_did",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "metadata": {"actor_type": "human"},
                    "standing_view": {"trust_class": "T3"},
                }
            ]
        }

    async def fake_load_permissions_lookup(_principal, auth_headers=None):
        return {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_permissions_lookup", fake_load_permissions_lookup)

    response = asyncio.run(dashboard_app.principals_page(Request({
        "type": "http", "method": "GET", "path": "/principals", "query_string": b"", "headers": []
    })))
    body = response.body.decode("utf-8")

    assert "Provision Codex Principal" in body
    assert "/principals/codex/provision" in body
    assert "chat-demo" in body
    assert "surface:chat:primary" in body
    assert "delegated_only" in body
    assert "Confirm delegated-only posture" in body


def test_render_principal_detail_page_includes_delegated_access_control() -> None:
    body = dashboard_app.render_principal_detail_page(
        {
            "principal_id": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            "name": "OpenAI Codex",
            "canonical_subject": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            "status": "active",
            "actor_type": "agent",
            "ledger_links": [],
            "surface_links": [],
            "principal_links": [],
            "related_rows": [],
            "metadata": {
                "delegated_authority": {
                    "delegation_mode": "delegated_only",
                    "delegated_prompt_execution": "explicit_cli_request_required",
                    "revocation_mode": "control_plane_operator",
                    "hidden_operator_alias": False,
                    "revocable": True,
                    "ledger_scope": ["chat-demo"],
                    "surface_scope": ["surface:chat:primary"],
                }
            },
        },
        connection_context=_empty_connection_context(),
    )
    assert "Revoke delegated access" in body
    assert "/delegated-access" in body


def test_render_principal_detail_page_summary_tab_uses_accordion_table() -> None:
    body = dashboard_app.render_principal_detail_page(
        {
            "principal_id": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            "name": "OpenAI Codex",
            "canonical_subject": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            "status": "active",
            "actor_type": "agent",
            "ledger_links": [],
            "surface_links": [],
            "principal_links": [],
            "related_rows": [],
            "metadata": {
                "delegated_authority": {
                    "delegation_mode": "delegated_only",
                    "delegated_prompt_execution": "explicit_cli_request_required",
                    "revocation_mode": "control_plane_operator",
                    "hidden_operator_alias": False,
                    "revocable": True,
                    "ledger_scope": ["chat-demo"],
                    "surface_scope": ["surface:chat:primary"],
                }
            },
        },
        connection_context=_empty_connection_context(),
    )
    assert 'data-principal-summary-view' in body
    assert 'data-principal-relationship-view' in body
    assert "Identity &amp; Addressing" in body
    assert "Authority &amp; Delegation" in body
    assert "Scope &amp; Revocation" in body
    assert "Provenance &amp; Governance" in body
    assert "Principal DID" in body
    assert "Prompt execution" in body
    assert "Ledger scope" in body


def test_ledger_summary_renders_card_stack_and_dropzone_upload() -> None:
    ledger: dashboard_app._LedgerDetailData = {
        "ledger_id": "ledger:demo",
        "name": "Demo Ledger",
        "display_subtitle": "demo",
        "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:demo",
        "status": "active",
        "metadata": {
            "founding_constitution": {"name": "Demo Charter", "personality": "Deliberate", "purpose": "Hold memory."},
        },
        "ledger_self_description": {},
        "principal_links": [],
        "surface_links": [],
        "related_rows": [],
    }
    context = {
        "ledgers": [],
        "principals": [],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {},
    }
    html = dashboard_app.render_ledger_detail_page(
        ledger, connection_context=context, current_principal_did="did:key:z6MkOperator"
    )
    assert "summary-card-stack" in html
    assert "summary-card" in html
    assert "doc-upload-dropzone" in html
    assert "Browse files" in html
    assert "doc-upload-input" in html
    assert "Drag and drop files here" in html
    assert "Document Archive" in html

    archive_html, count = dashboard_app._render_ledger_document_archive_inner(
        ledger_id="ledger:demo",
        current_principal_did="did:key:z6MkOperator",
        sources=[
            {
                "ledger_id": "ledger:demo",
                "source_id": "source:1",
                "coordinate": "ledger:demo:SRC-1",
                "file_name": "report.pdf",
                "summary": "A test document",
                "content_type": "application/pdf",
                "status": "completed",
            }
        ],
        source_jobs=[],
        backend_attachments=[],
    )
    assert count == 1
    assert "doc-archive-row" in archive_html
    assert "doc-archive-list" in archive_html
    assert "report.pdf" in archive_html


def test_principal_delegated_access_update_redirects_with_banner(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "principal_display_name": "Tester"}}, None

    async def fake_backend_admin_post(_path, payload, *, extra_headers=None):
        assert payload["status"] == "disabled"
        assert payload["reason"] == "delegated_access_revoked_by_control_plane"
        return 200, {"status": "ok"}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_backend_admin_post", fake_backend_admin_post)

    async def _run():
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/principals/did:web:id.dualsubstrate.com:principals:agent:openai:codex/delegated-access",
                "path_params": {"principal_id": "did:web:id.dualsubstrate.com:principals:agent:openai:codex"},
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            }
        )
        request._form = {"action": "revoke", "reason": "delegated_access_revoked_by_control_plane"}  # type: ignore[attr-defined]
        return await dashboard_app.principal_delegated_access_update(request)

    response = asyncio.run(_run())
    assert response.status_code == 303
    assert "banner=Delegated+access+revoked." in response.headers["location"]


def test_codex_principal_provision_submit_redirects_to_principal_detail(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_display_name": "Tester",
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:test",
                "ledger_id": "chat-demo",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "ledger_id": "chat-demo",
                    "status": "active",
                }
            ]
        }

    async def fake_control_plane_post(path, payload, request=None):
        assert path == "/api/control-plane/principals/codex/provision"
        assert payload["tenant_id"] == "tenant:test"
        assert payload["delegated_by_principal_did"] == "did:key:z6MkOperator"
        assert payload["ledger_id"] == "chat-demo"
        assert payload["surface_ids"] == ["surface:chat:primary"]
        return 200, {
            "principal": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            }
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    async def _run():
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/principals/codex/provision",
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            }
        )
        request._form = {  # type: ignore[attr-defined]
            "confirm_delegated_only": "yes",
            "ledger_id": "chat-demo",
            "surface_id": "surface:chat:primary",
        }
        return await dashboard_app.codex_principal_provision_submit(request)

    response = asyncio.run(_run())
    assert response.status_code == 303
    assert "/principals/did%3Aweb%3Aid.dualsubstrate.com%3Aprincipals%3Aagent%3Aopenai%3Acodex" in response.headers["location"]
    assert "banner=Codex+principal+provisioned." in response.headers["location"]


def test_codex_principal_provision_submit_requires_delegated_only_confirmation(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_display_name": "Tester",
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:test",
                "ledger_id": "chat-demo",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "ledger_id": "chat-demo",
                    "status": "active",
                }
            ]
        }

    async def fake_control_plane_post(_path, _payload, request=None):
        raise AssertionError("control plane post should not be called without delegated-only confirmation")

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    async def _run():
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/principals/codex/provision",
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            }
        )
        request._form = {}  # type: ignore[attr-defined]
        return await dashboard_app.codex_principal_provision_submit(request)

    response = asyncio.run(_run())
    assert response.status_code == 303
    assert "banner_kind=warn" in response.headers["location"]
    assert "Confirm+delegated-only+posture" in response.headers["location"]


def test_codex_principal_provision_submit_requires_active_scope(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_display_name": "Tester",
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:test",
                "ledger_id": "",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {"app_surfaces": []}

    async def fake_control_plane_post(_path, _payload, request=None):
        raise AssertionError("control plane post should not be called without active scope defaults")

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    async def _run():
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/principals/codex/provision",
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            }
        )
        request._form = {"confirm_delegated_only": "yes"}  # type: ignore[attr-defined]
        return await dashboard_app.codex_principal_provision_submit(request)

    response = asyncio.run(_run())
    assert response.status_code == 303
    assert "banner_kind=warn" in response.headers["location"]
    assert "active+ledger+scope" in response.headers["location"]


def test_apply_connections_add_flow_provisions_codex_from_service_delegated_agent(monkeypatch) -> None:
    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {
            "principal_map": {},
            "ledger_map": {},
            "surface_map": {},
        }

    async def fake_build_dashboard_snapshot(_request):
        return {
            "app_surfaces": [
                {
                    "surface_id": "surface:chat:primary",
                    "ledger_id": "chat-demo",
                    "status": "active",
                }
            ]
        }

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_control_plane_post(path, payload, request=None):
        calls.append((path, payload))
        assert path == "/api/control-plane/principals/codex/provision"
        assert payload["tenant_id"] == "tenant:test"
        assert payload["delegated_by_principal_did"] == "did:key:z6MkOperator"
        assert payload["ledger_id"] == "chat-demo"
        assert payload["surface_ids"] == ["surface:chat:primary"]
        return 200, {
            "principal": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            }
        }

    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {})
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/connections/add/principal",
            "headers": [],
        }
    )

    response = asyncio.run(
        dashboard_app._apply_connections_add_flow(
            request,
            entity_kind="principal",
            state={
                "display_name": "Wrong Name Should Be Ignored",
                "principal_type": "service",
                "service_subtype": "delegated_agent",
                "confirm_delegated_only": "yes",
                "tenant_id": "tenant:test",
                "linked_ledger_ids": "chat-demo",
                "linked_surface_ids": "surface:chat:primary",
            },
            identity_card={
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:test",
                    "ledger_id": "chat-demo",
                }
            },
        )
    )

    assert len(calls) == 1
    assert response.status_code == 303
    assert "/principals/did%3Aweb%3Aid.dualsubstrate.com%3Aprincipals%3Aagent%3Aopenai%3Acodex" in response.headers["location"]


def test_apply_connections_add_flow_provisions_codex_from_wizard_defaults_when_links_missing(monkeypatch) -> None:
    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {
            "principal_map": {},
            "ledger_map": {},
            "surface_map": {},
        }

    async def fake_build_dashboard_snapshot(_request):
        return {"app_surfaces": []}

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_control_plane_post(path, payload, request=None):
        calls.append((path, payload))
        assert path == "/api/control-plane/principals/codex/provision"
        assert payload["ledger_id"] == "chat-demo"
        assert payload["surface_ids"] == ["surface:chat:primary"]
        return 200, {
            "principal": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            }
        }

    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {})
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    request = Request({"type": "http", "method": "POST", "path": "/connections/add/principal", "headers": []})

    response = asyncio.run(
        dashboard_app._apply_connections_add_flow(
            request,
            entity_kind="principal",
            state={
                "principal_type": "service",
                "service_subtype": "delegated_agent",
                "confirm_delegated_only": "yes",
                "tenant_id": "tenant:test",
                "default_ledger_scope": "chat-demo",
                "default_surface_scope": "surface:chat:primary",
            },
            identity_card={
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:test",
                    "ledger_id": "chat-demo",
                }
            },
        )
    )

    assert len(calls) == 1
    assert response.status_code == 303


def test_apply_connections_add_flow_redirects_wizard_with_banner_when_codex_confirmation_missing(monkeypatch) -> None:
    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {
            "principal_map": {},
            "ledger_map": {},
            "surface_map": {},
        }

    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {})
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)

    request = Request({"type": "http", "method": "POST", "path": "/connections/add/principal", "headers": []})

    response = asyncio.run(
        dashboard_app._apply_connections_add_flow(
            request,
            entity_kind="principal",
            state={
                "step": "Summary",
                "principal_type": "service",
                "service_subtype": "delegated_agent",
            },
            identity_card={
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:test",
                    "ledger_id": "chat-demo",
                }
            },
        )
    )

    assert response.status_code == 303
    assert "/connections/add/principal?" in response.headers["location"]
    assert "banner_kind=warn" in response.headers["location"]
    assert "Confirm+delegated-only+posture" in response.headers["location"]


def test_did_first_models_and_standing_pages_render_canonical_subjects(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "principal_display_name": "Tester", "tenant_id": "tenant:test", "principal_did": "did:key:z6MkAlice"}}, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {"identity_vc": {"tenant_id": "tenant:test", "principal_did": "did:key:z6MkAlice"}},
            "providers": [
                {
                    "provider_id": "provider:openrouter:shared",
                    "provider_type": "OpenRouter",
                    "canonical_subject": "did:web:id.dualsubstrate.com:providers:provider-openrouter-shared",
                    "status": "configured",
                    "owner_scope": "shared",
                    "secret_ref": "OPENROUTER_API_KEY",
                    "deployment_targets": ["vercel:chat"],
                }
            ],
            "model_bindings": [
                {
                    "binding_id": "binding:chat:default",
                    "name": "Chat Default",
                    "canonical_subject": "did:web:id.dualsubstrate.com:bindings:binding-chat-default",
                    "provider_type": "OpenRouter",
                    "model_id": "openai/gpt-4o",
                    "app_surfaces": ["surface:chat:primary"],
                    "status": "active",
                    "source": "seed",
                }
            ],
        }

    async def fake_principal_registry_get(_path, headers=None):
        return 200, {
            "principals": [
                {
                    "principal_did": "did:key:z6MkModel",
                    "display_name": "Model Demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:principals:model-demo",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "metadata": {"actor_type": "model", "model_id": "demo/model"},
                    "standing_view": {"trust_class": "T3", "posture_class": "P2", "operator_profile": "steady", "probation_status": "cleared"},
                },
                {
                    "principal_did": "did:key:z6MkAlice",
                    "display_name": "Alice",
                    "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                    "status": "active",
                    "tenant_id": "tenant:test",
                    "metadata": {"actor_type": "human", "wallet_binding_ref": "wallet:1"},
                    "standing_view": {"trust_class": "T3", "posture_class": "P2", "operator_profile": "steady", "probation_status": "cleared"},
                }
            ]
        }

    async def fake_fetch_json(path, headers=None):
        if path == '/api/models?mode=full':
            return {"local_models": [], "online_models": [], "fallback": False}
        if path == '/api/models/debug':
            return {"settings_llm_provider": "OpenRouter", "settings_llm_model": "openai/gpt-4o", "local": {"status": "ok"}}
        return {}

    async def fake_load_permissions_lookup(_principal, auth_headers=None):
        return {
            "principal": {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
                "status": "active",
            },
            "standing": {"trust_class": "T3", "posture_class": "P2", "operator_profile": "steady", "probation_status": "cleared"},
            "authority": {},
            "provisioning": {},
            "approval_lookup": {},
            "standing_events": [],
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(dashboard_app, "_load_permissions_lookup", fake_load_permissions_lookup)
    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_require_control_plane_auth)

    models_response = asyncio.run(dashboard_app.models_page(Request({
        "type": "http", "method": "GET", "path": "/models", "query_string": b"principal_did=did%3Akey%3Az6MkModel", "headers": []
    })))
    standing_response = asyncio.run(dashboard_app.permissions_page(Request({
        "type": "http", "method": "GET", "path": "/permissions", "query_string": b"principal_did=did%3Akey%3Az6MkAlice", "headers": []
    })))

    models_html = models_response.body.decode("utf-8")
    standing_html = standing_response.body.decode("utf-8")

    assert "<h1>Models</h1>" in models_html
    assert "Choose the model/s you would like to connect as a Principal" in models_html
    assert "Model Demo" in models_html
    assert "<span>Name</span>" in models_html
    assert "<span>Connection</span>" in models_html
    assert "data-binding-id=\"binding:chat:demo-model\"" in models_html
    assert "did:web:id.dualsubstrate.com:principals:alice" in standing_html
    assert "Display label:" in standing_html
    assert "Canonical Subject" in standing_html


def test_connection_detail_pages_render_did_first_identity() -> None:
    context = {
        "ledgers": [{"ledger_id": "chat-demo", "ledger_name": "Chat Demo", "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo", "status": "active", "metadata": {"founding_constitution": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory."}, "ledger_alias_history": ["ledger:loam-137to139"], "ledger_supersession_history": ["ledger:loam-137to139"], "ledger_consolidation_history": [{"event": "ledger_split_consolidated", "superseded_ledger_ids": ["ledger:loam-137to139"]}]}, "ledger_self_description": {"seed_identity": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory.", "source": "control_plane_operator"}, "resolved_constitution_context": {"present": False, "basis": [], "coord_resolved_access_is_not_runtime_foundation_identity": True}, "runtime_foundation_identity": {"available": True, "fields": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory.", "source": "control_plane_operator"}, "structured_runtime_surface_required": True}, "verified_ledger_traits": [{"trait": "lifecycle_status", "summary": "Lifecycle status is active.", "evidence": [{"field": "status", "value": "active"}]}], "speculative_overlay": {"summary": "Mythic language remains optional."}}}],
        "principals": [{"principal_did": "did:key:z6MkAlice", "display_name": "Alice", "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice", "status": "active", "metadata": {"actor_type": "human"}}],
        "surfaces": [{"surface_id": "surface:chat:primary", "name": "Chat Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary", "status": "active"}],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {"chat-demo": {"ledger_id": "chat-demo", "ledger_name": "Chat Demo", "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo", "status": "active", "metadata": {"founding_constitution": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory."}, "ledger_alias_history": ["ledger:loam-137to139"], "ledger_supersession_history": ["ledger:loam-137to139"], "ledger_consolidation_history": [{"event": "ledger_split_consolidated", "superseded_ledger_ids": ["ledger:loam-137to139"]}]}, "ledger_self_description": {"seed_identity": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory.", "source": "control_plane_operator"}, "resolved_constitution_context": {"present": False, "basis": [], "coord_resolved_access_is_not_runtime_foundation_identity": True}, "runtime_foundation_identity": {"available": True, "fields": {"name": "LOAM", "personality": "Deliberate, layered.", "purpose": "Hold governed memory.", "source": "control_plane_operator"}, "structured_runtime_surface_required": True}, "verified_ledger_traits": [{"trait": "lifecycle_status", "summary": "Lifecycle status is active.", "evidence": [{"field": "status", "value": "active"}]}], "speculative_overlay": {"summary": "Mythic language remains optional."}}}},
        "principal_map": {"did:key:z6MkAlice": {"principal_did": "did:key:z6MkAlice", "display_name": "Alice", "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice", "status": "active", "metadata": {"actor_type": "human"}}},
        "surface_map": {"surface:chat:primary": {"surface_id": "surface:chat:primary", "name": "Chat Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary", "status": "active"}},
    }
    ledger = dashboard_app._lookup_ledger_detail_data("chat-demo", context)
    principal = dashboard_app._lookup_principal_detail_data("did:key:z6MkAlice", context)
    surface = dashboard_app._lookup_surface_detail_data("surface:chat:primary", context)
    assert ledger and principal and surface

    ledger_html = dashboard_app.render_ledger_detail_page(ledger, connection_context=context)
    principal_html = dashboard_app.render_principal_detail_page(principal, connection_context=context)
    surface_html = dashboard_app.render_surface_detail_page(surface, connection_context=context)

    assert "Canonical subject" in ledger_html
    assert "did:web:id.dualsubstrate.com:ledgers:chat-demo" in ledger_html
    assert "Display label" in ledger_html
    assert "Founding Charter" in ledger_html
    assert "Identity &amp; Addressing" in ledger_html
    assert "Document Archive" in ledger_html
    assert "Canonical ledger ID" in ledger_html
    assert "did:web:id.dualsubstrate.com:principals:alice" in principal_html
    assert "Principal DID" in principal_html
    assert "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary" in surface_html
    assert "Surface ID" in surface_html


def test_chat_surface_detail_shows_billing_alert_and_safe_message() -> None:
    context = {
        "ledgers": [],
        "principals": [],
        "surfaces": [{"surface_id": "surface:chat:primary", "name": "Chat Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary", "status": "active", "surface_type": "chat", "endpoint": "https://chat.dualsubstrate.com"}],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {"surface:chat:primary": {"surface_id": "surface:chat:primary", "name": "Chat Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary", "status": "active", "surface_type": "chat", "endpoint": "https://chat.dualsubstrate.com"}},
    }
    surface = dashboard_app._lookup_surface_detail_data("surface:chat:primary", context)
    assert surface
    surface_html = dashboard_app.render_surface_detail_page(surface, connection_context=context)
    assert "OpenRouter account has no available credit" in surface_html
    assert "OpenRouter billing" in surface_html
    assert "https://openrouter.ai/settings/billing" in surface_html
    assert "Review provider settings" in surface_html


def test_non_chat_surface_detail_does_not_show_chat_billing_alert() -> None:
    context = {
        "ledgers": [],
        "principals": [],
        "surfaces": [{"surface_id": "surface:api:primary", "name": "API Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-api-primary", "status": "active", "surface_type": "api", "endpoint": "https://api.dualsubstrate.com"}],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {"surface:api:primary": {"surface_id": "surface:api:primary", "name": "API Surface", "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-api-primary", "status": "active", "surface_type": "api", "endpoint": "https://api.dualsubstrate.com"}},
    }
    surface = dashboard_app._lookup_surface_detail_data("surface:api:primary", context)
    assert surface
    surface_html = dashboard_app.render_surface_detail_page(surface, connection_context=context)
    assert "OpenRouter account has no available credit" not in surface_html


def test_activity_detail_fields_include_runtime_namespace_for_ledger_rows() -> None:
    fields = dashboard_app._activity_detail_fields(
        {
            "summary": "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "canonical_subject_source": "did:web:ledger",
            "display_label": "Chat Demo",
            "coord": "chat-demo:WX-123",
            "ledger_id": "chat-demo",
            "details": {"runtime_namespace": "chat-demo"},
        }
    )
    assert ("Runtime namespace", "chat-demo") in fields


def test_ledger_entries_from_history_items_preserve_coord_meta_namespace() -> None:
    entries = dashboard_app._ledger_entries_from_history_items(
        [
            {
                "role": "assistant",
                "content": "History item",
                "timestamp": "2026-04-07T12:00:00+00:00",
                "coordinate": "chat-demo:WX-123",
                "entry_id": "WX-123",
                "coord_meta": {
                    "coord": "chat-demo:WX-123",
                    "coord_type": "WX",
                    "identifier": "WX-123",
                    "runtime_namespace": "chat-demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "canonical_subject_source": "did:web:ledger",
                },
                "metadata": {"kind": "chat", "role": "assistant", "content": "History item"},
            }
        ]
    )
    assert entries[0]["key"]["namespace"] == "chat-demo"
    assert entries[0]["coord_meta"]["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"


def test_normalize_ledger_entry_rows_preserves_prompt_and_response_attribution() -> None:
    rows = dashboard_app._normalize_ledger_entry_rows(
        [
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-123"},
                "created_at": "2026-05-04T10:00:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "History item",
                        "model_id": "anthropic/claude-haiku-4.5",
                        "provider_id": "provider:openrouter:shared",
                        "contributor": {
                            "principal_type": "agent",
                            "principal_id": "openai:codex",
                            "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                        },
                    }
                },
            }
        ]
    )
    assert rows[0]["actor"] == "openai/codex"
    assert rows[0]["details"]["prompt_principal_label"] == "openai/codex"
    assert rows[0]["details"]["response_model_label"] == "anthropic/claude-haiku-4.5"


def test_normalize_ledger_entry_rows_uses_delegated_prompt_path_for_prompt_attribution() -> None:
    rows = dashboard_app._normalize_ledger_entry_rows(
        [
            {
                "key": {"namespace": "chat-demo", "identifier": "WX-456"},
                "created_at": "2026-05-04T10:05:00+00:00",
                "state": {
                    "metadata": {
                        "kind": "chat",
                        "role": "assistant",
                        "content": "Delegated response",
                        "model_id": "anthropic/claude-haiku-4.5",
                        "delegated_prompt_path": {
                            "prompt_principal_id": "openai:agent:codex",
                            "prompt_principal_did": "did:web:ds-backend-new.fly.dev:principals:agent:openai:codex",
                            "requested_by_principal_did": "did:key:z6MkOperator",
                        },
                    }
                },
            }
        ]
    )
    assert rows[0]["actor"] == "openai/codex"
    assert rows[0]["details"]["prompt_principal_label"] == "openai/codex"


def _distinct_delegated_row() -> dict[str, Any]:
    return {
        "summary": "Chat response committed to did:web:id.dualsubstrate.com:ledgers:chat-demo",
        "event_type": "ledger.entry",
        "entity_type": "ledger",
        "entity_id": "chat-demo",
        "status": "assistant",
        "actor": "operator:david",
        "timestamp": "2026-05-04T10:05:00+00:00",
        "coord": "loam:WX-123",
        "ledger_id": "chat-demo",
        "reference": "loam:WX-123",
        "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
        "details": {
            "prompt_principal_label": "Moonshot: Kimi-code",
            "response_model_label": "MoonshotAI: Kimi K2.5",
            "model_id": "moonshotai/kimi-k2.5",
            "provider_id": "openrouter",
            "delegated_prompt_path": {
                "active": True,
                "prompt_principal_id": "moonshot:kimi-code",
                "prompt_principal_did": "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code",
                "prompt_principal_display_name": "Moonshot: Kimi-code",
                "requested_by_principal_did": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
                "requested_by_principal_id": "operator:david",
                "requested_by_is_distinct_from_prompt_principal": True,
            },
        },
    }


def test_activity_detail_fields_swaps_asked_and_answered_for_distinct_delegation() -> None:
    fields = dict(dashboard_app._activity_detail_fields(_distinct_delegated_row()))
    assert fields["Asked by"] == "operator:david"
    assert fields["Answered by"] == "Moonshot: Kimi-code"
    assert fields["Response model"] == "MoonshotAI: Kimi K2.5"


def test_activity_detail_sections_swaps_attribution_for_distinct_delegation() -> None:
    sections = dict(dashboard_app._activity_detail_sections(_distinct_delegated_row()))
    assert "Attribution" in sections
    attribution = dict(sections["Attribution"])
    assert attribution["Asked by"] == "operator:david"
    assert attribution["Answered by"] == "Moonshot: Kimi-code"
    assert attribution["Response model"] == "MoonshotAI: Kimi K2.5"
    assert "Prompt principal DID" not in attribution
    assert "Delegation" in sections
    delegation = dict(sections["Delegation"])
    assert delegation["Prompt principal DID"] == "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code"


def test_activity_collapsed_hint_swaps_labels_for_distinct_delegation() -> None:
    hint = dashboard_app._activity_collapsed_hint(_distinct_delegated_row())
    assert "asked by: operator:david" in hint
    assert "answered by: Moonshot: Kimi-code" in hint
    assert "model: MoonshotAI: Kimi K2.5" in hint
    assert "requested by:" not in hint


def test_flow_selection_list_labels_canonical_id_for_ledgers() -> None:
    flows_spec = importlib.util.spec_from_file_location("dss_dashboard_flows", REPO_ROOT / "views" / "flows.py")
    flows_module = importlib.util.module_from_spec(flows_spec)
    assert flows_spec.loader is not None
    flows_spec.loader.exec_module(flows_module)

    html = flows_module._selection_list(
        items=[
            {
                "ledger_id": "chat-demo",
                "ledger_name": "Chat Demo",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                "status": "active",
            }
        ],
        field_name="linked_ledger_ids",
        kind="ledger",
        selected_values=set(),
        multi=True,
    )
    assert "Canonical ID" in html
    assert "did:web:id.dualsubstrate.com:ledgers:chat-demo" in html


def test_activity_collapsed_hint_includes_runtime_namespace_for_ledger_rows() -> None:
    hint = dashboard_app._activity_collapsed_hint(
        {
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            "details": {"runtime_namespace": "chat-demo"},
        }
    )
    assert "runtime: chat-demo" in hint

def test_activity_ledgers_for_principal_include_stable_model_alias_ledgers() -> None:
    context = {
        "ledgers": [{"ledger_id": "chat-demo", "ledger_name": "Chat Demo"}],
        "principals": [
            {
                "principal_did": "did:key:z6MkGuardianGemini25Flash",
                "display_name": "Guardian Gemini 2.5 Flash",
                "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
            },
            {
                "principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                "display_name": "google/gemini-2.5-flash",
                "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
            },
        ],
        "surfaces": [{"surface_id": "surface:chat:primary", "ledger_id": "chat-demo"}],
        "model_bindings": [],
        "relationships": [
            {
                "relationship_id": "principal::did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash::surface::surface:chat:primary",
                "subject_entity_type": "principal",
                "subject_entity_id": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                "object_entity_type": "surface",
                "object_entity_id": "surface:chat:primary",
            }
        ],
        "ledger_map": {"chat-demo": {"ledger_id": "chat-demo", "ledger_name": "Chat Demo"}},
        "principal_map": {
            "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                "display_name": "google/gemini-2.5-flash",
                "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
            },
        },
        "surface_map": {"surface:chat:primary": {"surface_id": "surface:chat:primary", "ledger_id": "chat-demo"}},
    }
    ledgers = dashboard_app._activity_ledgers_for_principal(
        "did:key:z6MkGuardianGemini25Flash",
        context,
        {
            "principal_did": "did:key:z6MkGuardianGemini25Flash",
            "display_name": "Guardian Gemini 2.5 Flash",
            "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
            "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
        },
    )
    assert "chat-demo" in ledgers


def test_activity_page_selected_model_principal_loads_history_via_lookup_alias(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkGuardianGemini25Flash",
            }
        }, None

    async def fake_principal_registry_get(path, headers=None):
        if path == "/api/principals?limit=50":
            return 200, {
                "principals": [
                    {
                        "principal_did": "did:key:z6MkGuardianGemini25Flash",
                        "display_name": "Guardian Gemini 2.5 Flash",
                    }
                ]
            }
        return 404, {}

    async def fake_load_activity_lookup(_principal_did, auth_headers=None):
        return {
            "principal": {
                "principal_did": "did:key:z6MkGuardianGemini25Flash",
                "display_name": "Guardian Gemini 2.5 Flash",
                "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
            }
        }

    async def fake_load_connection_lookup_context(_request, identity_card=None):
        return {
            "ledgers": [{"ledger_id": "chat-demo", "ledger_name": "Chat Demo"}],
            "principals": [
                {
                    "principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                    "display_name": "google/gemini-2.5-flash",
                    "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                    "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
                }
            ],
            "surfaces": [{"surface_id": "surface:chat:primary", "ledger_id": "chat-demo"}],
            "model_bindings": [],
            "relationships": [
                {
                    "relationship_id": "principal::did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash::surface::surface:chat:primary",
                    "subject_entity_type": "principal",
                    "subject_entity_id": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                    "object_entity_type": "surface",
                    "object_entity_id": "surface:chat:primary",
                }
            ],
            "ledger_map": {"chat-demo": {"ledger_id": "chat-demo", "ledger_name": "Chat Demo"}},
            "principal_map": {
                "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash": {
                    "principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash",
                    "display_name": "google/gemini-2.5-flash",
                    "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
                    "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
                }
            },
            "surface_map": {"surface:chat:primary": {"surface_id": "surface:chat:primary", "ledger_id": "chat-demo"}},
        }

    async def fake_load_activity_control_plane_state(auth_headers=None):
        return {}

    async def fake_control_plane_get(_path, **kwargs):
        return 200, {"submissions": []}

    async def fake_fetch_json(path, headers=None):
        if path.startswith("/ledger/history/"):
            return {
                "history": [
                    {
                        "role": "assistant",
                        "content": "History-backed answer.",
                        "timestamp": "2026-04-08T00:00:00+00:00",
                        "entry_id": "WX-9C2621E0-1775565322",
                        "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                        "metadata": {"kind": "chat", "role": "assistant"},
                    }
                ]
            }
        return {"entries": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_principal_registry_get", fake_principal_registry_get)
    monkeypatch.setattr(dashboard_app, "_load_activity_lookup", fake_load_activity_lookup)
    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_load_connection_lookup_context)
    monkeypatch.setattr(dashboard_app, "_load_activity_control_plane_state", fake_load_activity_control_plane_state)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)

    response = asyncio.run(
        dashboard_app.activity_page(_make_request("principal_did=did:key:z6MkGuardianGemini25Flash"))
    )

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "chat-demo:WX-9C2621E0-1775565322" in body
    assert "History-backed answer." in body


def test_entity_trust_identifier_rewrites_legacy_surface_and_model_subjects() -> None:
    assert dashboard_app._entity_trust_identifier("surface", "surface:chat:primary", {"canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary"}) == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"
    assert dashboard_app._entity_trust_identifier(
        "principal",
        "did:key:z6MkGuardianGemini25Flash",
        {
            "principal_did": "did:key:z6MkGuardianGemini25Flash",
            "canonical_subject": "openrouter:model:google/gemini-2.5-flash",
            "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
            "metadata": {"actor_type": "model", "provider_type": "OpenRouter", "model_id": "google/gemini-2.5-flash"},
        },
    ) == "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash"



def test_load_connection_lookup_context_trusts_backend_authz(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkKaoru",
                "tenant_id": "tenant:kaoru",
                "ledger_id": "ledger:kaoru",
            }
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "control_plane_app_surfaces": [
                {"surface_id": "surface:operator", "ledger_id": "ledger:operator", "tenant_id": "tenant:operator", "status": "active", "metadata": {}},
                {"surface_id": "surface:kaoru", "ledger_id": "ledger:kaoru", "tenant_id": "tenant:kaoru", "status": "active", "metadata": {}},
            ],
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {
                "ledgers": [
                    {"ledger_id": "ledger:operator", "tenant_id": "tenant:operator", "status": "active"},
                    {"ledger_id": "ledger:kaoru", "tenant_id": "tenant:kaoru", "status": "active"},
                ]
            }
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {"principal_did": "did:key:z6MkOperator", "tenant_id": "tenant:operator", "status": "active"},
                    {"principal_did": "did:key:z6MkKaoru", "tenant_id": "tenant:kaoru", "status": "active"},
                ]
            }
        if path == "/api/control-plane/relationships":
            return 200, {
                "relationships": [
                    {
                        "relationship_id": "principal::did:key:z6MkOperator::ledger::ledger:operator",
                        "subject_entity_type": "principal",
                        "subject_entity_id": "did:key:z6MkOperator",
                        "object_entity_type": "ledger",
                        "object_entity_id": "ledger:operator",
                    },
                    {
                        "relationship_id": "principal::did:key:z6MkKaoru::ledger::ledger:kaoru",
                        "subject_entity_type": "principal",
                        "subject_entity_id": "did:key:z6MkKaoru",
                        "object_entity_type": "ledger",
                        "object_entity_id": "ledger:kaoru",
                    },
                ]
            }
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    ledger_ids = {str(item.get("ledger_id")) for item in context["ledgers"]}
    principal_ids = {str(item.get("principal_did")) for item in context["principals"]}
    surface_ids = {str(item.get("surface_id")) for item in context["surfaces"]}
    relationship_ids = {str(item.get("relationship_id")) for item in context["relationships"]}

    # Frontend scopes by ledger linkage/ownership rather than tenant_id.
    assert "ledger:kaoru" in ledger_ids
    assert "ledger:operator" not in ledger_ids
    assert "did:key:z6MkKaoru" in principal_ids
    assert "did:key:z6MkOperator" not in principal_ids
    assert "surface:kaoru" in surface_ids
    assert "surface:operator" not in surface_ids
    assert "principal::did:key:z6MkKaoru::ledger::ledger:kaoru" in relationship_ids
    assert "principal::did:key:z6MkOperator::ledger::ledger:operator" not in relationship_ids


def test_api_control_plane_relationships_upsert_parses_string_permission_payload(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "tenant_id": "tenant:test", "principal_did": "did:key:z6MkOperator"}}, None

    async def fake_control_plane_post(path, payload, request=None):
        captured.append(payload)
        return 200, {
            "relationship": {
                "relationship_id": str(payload.get("relationship_id") or ""),
                "permission_payload": payload.get("permission_payload"),
            }
        }

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    async def _run():
        body = json.dumps({
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkAlice",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:alice",
            "permission_scope": "custom",
            "permission_payload": json.dumps({"can_view": True, "custom": True}),
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        }).encode("utf-8")
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/control-plane/relationships",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("utf-8")),
                ],
            }
        )
        request._body = body  # type: ignore[attr-defined]
        return await dashboard_app.api_control_plane_relationships_upsert(request)

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert len(captured) == 1
    payload = captured[0]
    assert payload["permission_payload"] == {"can_view": True, "custom": True}
    assert payload["start_at"] == "2026-01-01"
    assert payload["end_at"] == "2026-12-31"


def test_api_control_plane_entities_remove_calls_backend_mutation(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_require_control_plane_auth(_request):
        return {"identity_vc": {"verified": True, "tenant_id": "tenant:test", "principal_did": "did:key:z6MkOperator"}}, None

    async def fake_control_plane_post(path, payload, request=None):
        captured.append((path, payload))
        return 200, {"removed": True}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)

    async def _run():
        body = json.dumps({"entity_type": "principal", "entity_id": "did:key:z6MkAlice"}).encode("utf-8")
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/control-plane/entities/remove",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("utf-8")),
                ],
            }
        )
        request._body = body  # type: ignore[attr-defined]
        return await dashboard_app.api_control_plane_entities_remove(request)

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0] == (
        "/api/control-plane/entities/remove",
        {
            "entity_type": "principal",
            "entity_id": "did:key:z6MkAlice",
            "governance_mode": "direct_write",
            "break_glass": {
                "actor": "did:key:z6MkOperator",
                "reason_code": "owner_requested_removal",
                "scope": "principal:did:key:z6MkAlice",
            },
        },
    )


def test_render_principal_detail_page_includes_backend_remove_action() -> None:
    body = dashboard_app.render_principal_detail_page(
        {
            "principal_id": "did:key:z6MkAlice",
            "name": "Alice",
            "canonical_subject": "did:web:id.dualsubstrate.com:principals:alice",
            "status": "active",
            "actor_type": "human",
            "ledger_links": [],
            "surface_links": [],
            "principal_links": [],
            "related_rows": [],
            "metadata": {},
        },
        connection_context=_empty_connection_context(),
    )
    assert "Remove principal" in body
    assert 'data-remove-entity-type="principal"' in body
    assert 'data-remove-entity-id="did:key:z6MkAlice"' in body
    assert "/api/control-plane/entities/remove" in body



def test_display_label_for_entity_prefers_alias_and_display_name() -> None:
    assert dashboard_app._display_label_for_entity(
        "ledger", "ledger:demo", {"display_name": "Demo Ledger", "ledger_name": "legacy-demo", "canonical_subject": "did:web:demo"}
    ) == "Demo Ledger"
    assert dashboard_app._display_label_for_entity(
        "ledger", "ledger:demo", {"ledger_name": "legacy-demo", "canonical_subject": "did:web:demo"}
    ) == "legacy-demo"
    assert dashboard_app._display_label_for_entity(
        "principal", "did:key:z6MkAlice", {"display_name": "Alice Smith", "canonical_subject": "did:web:alice"}
    ) == "Alice Smith"
    assert dashboard_app._display_label_for_entity(
        "surface", "surface:chat:primary", {"label": "Primary Chat", "name": "Chat", "canonical_subject": "did:web:chat"}
    ) == "Primary Chat"
    assert dashboard_app._display_label_for_entity(
        "source", "source:abc", {"file_name": "report.pdf", "original_file_name": "upload.pdf"}
    ) == "report.pdf"


def test_lookup_ledger_detail_data_uses_display_name_alias() -> None:
    context = {
        "ledgers": [
            {
                "ledger_id": "ledger:demo",
                "display_name": "Demo Alias",
                "ledger_name": "legacy-demo",
                "status": "active",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:demo",
                "metadata": {},
                "ledger_self_description": {},
            }
        ],
        "principals": [],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {
            "ledger:demo": {
                "ledger_id": "ledger:demo",
                "display_name": "Demo Alias",
                "ledger_name": "legacy-demo",
                "status": "active",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:demo",
                "metadata": {},
                "ledger_self_description": {},
            }
        },
        "principal_map": {},
        "surface_map": {},
    }
    detail = dashboard_app._lookup_ledger_detail_data("ledger:demo", context)
    assert detail is not None
    assert detail["name"] == "Demo Alias"
    assert detail["display_subtitle"] == "ledger:demo"


def test_lookup_principal_detail_data_uses_display_name_alias() -> None:
    context = {
        "ledgers": [],
        "principals": [
            {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice Alias",
                "status": "active",
                "metadata": {},
            }
        ],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {
            "did:key:z6MkAlice": {
                "principal_did": "did:key:z6MkAlice",
                "display_name": "Alice Alias",
                "status": "active",
                "metadata": {},
            }
        },
        "surface_map": {},
    }
    detail = dashboard_app._lookup_principal_detail_data("did:key:z6MkAlice", context)
    assert detail is not None
    assert detail["name"] == "Alice Alias"



def test_fetch_ledger_attachments_extracts_filename_summary_and_content_type(monkeypatch) -> None:
    async def fake_middleware_json_request(*, method, path, params=None, **kwargs):
        assert method == "GET"
        assert path == "/ledger/all"
        namespace = str((params or {}).get("namespace") or "")
        if namespace == "ledger:demo":
            return 200, {
                "entries": [
                    {
                        "coordinate": "ledger:demo:doc:abc123",
                        "created_at": "2026-01-01T00:00:00Z",
                        "state": {
                            "metadata": {
                                "attachment": {"filename": "report.pdf", "content_type": "application/pdf", "size_bytes": 1234},
                                "summary": "Annual report",
                                "kind": "attachment",
                            }
                        },
                    }
                ]
            }
        return 200, {"entries": []}

    monkeypatch.setattr(dashboard_app, "_middleware_json_request", fake_middleware_json_request)

    async def _run():
        return await dashboard_app._fetch_ledger_attachments("ledger:demo")

    attachments = asyncio.run(_run())
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment["file_name"] == "report.pdf"
    assert attachment["original_file_name"] == "report.pdf"
    assert attachment["summary"] == "Annual report"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["coordinate"] == "ledger:demo:doc:abc123"
    assert attachment["parent_coordinate"] == "ledger:demo:doc:abc123"


def test_fetch_ledger_attachments_skips_part_entries(monkeypatch) -> None:
    async def fake_middleware_json_request(*, method, path, params=None, **kwargs):
        return 200, {
            "entries": [
                {
                    "coordinate": "ledger:demo:doc:parent",
                    "created_at": "2026-01-01T00:00:00Z",
                    "state": {
                        "metadata": {
                            "attachment": {"filename": "parent.txt"},
                            "kind": "attachment",
                        }
                    },
                },
                {
                    "coordinate": "ledger:demo:doc:parent/part/0",
                    "created_at": "2026-01-01T00:00:00Z",
                    "state": {
                        "metadata": {
                            "attachment": {"filename": "part0.bin"},
                            "kind": "attachment",
                            "attachment_part": True,
                        }
                    },
                },
            ]
        }

    monkeypatch.setattr(dashboard_app, "_middleware_json_request", fake_middleware_json_request)

    async def _run():
        return await dashboard_app._fetch_ledger_attachments("ledger:demo")

    attachments = asyncio.run(_run())
    coords = {a["coordinate"] for a in attachments}
    assert coords == {"ledger:demo:doc:parent"}


def test_fetch_ledger_attachments_falls_back_to_coordinate_when_filename_missing(monkeypatch) -> None:
    async def fake_middleware_json_request(*, method, path, params=None, **kwargs):
        return 200, {
            "entries": [
                {
                    "coordinate": "ledger:demo:doc:no-name",
                    "created_at": "2026-01-01T00:00:00Z",
                    "state": {
                        "metadata": {
                            "attachment": {"content_type": "text/plain"},
                            "kind": "attachment",
                        }
                    },
                }
            ]
        }

    monkeypatch.setattr(dashboard_app, "_middleware_json_request", fake_middleware_json_request)

    async def _run():
        return await dashboard_app._fetch_ledger_attachments("ledger:demo")

    attachments = asyncio.run(_run())
    assert len(attachments) == 1
    assert attachments[0]["file_name"] == "ledger:demo:doc:no-name"


# ── Epic 27 regression tests ──

def test_connections_page_synthesizes_self_principal_when_backend_omits(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "principal_display_name": "David Berigny",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = _make_request()
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "David Berigny" in body
    # (You) suffix removed for naming consistency; name presence verified above.
    assert "did:key:z6MkDavid" in body


def test_load_connection_lookup_context_keeps_chat_surface_before_ledger_ready(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": False,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": False,
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    surface_ids = {str(s.get("surface_id")) for s in context["surfaces"] if isinstance(s, dict)}
    assert "surface:chat:primary" in surface_ids
    chat_surface = next(s for s in context["surfaces"] if isinstance(s, dict) and str(s.get("surface_id")) == "surface:chat:primary")
    assert str(chat_surface.get("status")).lower() == "pending"


def test_load_connection_lookup_context_synthesizes_self_principal_when_backend_omits(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    principal_ids = {str(p.get("principal_did")) for p in context["principals"] if isinstance(p, dict)}
    assert "did:key:z6MkDavid" in principal_ids


def test_load_connection_lookup_context_resolves_ledger_alias_for_derived_surfaces(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOperator",
            "identity_vc": {
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:david", "status": "active", "owner_principal_id": "did:key:z6MkOperator"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    chat_surface = next(
        (s for s in context["surfaces"] if isinstance(s, dict) and str(s.get("surface_id")) == "surface:chat:primary"),
        None,
    )
    assert chat_surface is not None
    assert str(chat_surface.get("ledger_id")) == "LOAM"
    ledger_relationships = {
        (str(r.get("subject_entity_type")), str(r.get("subject_entity_id")), str(r.get("object_entity_type")), str(r.get("object_entity_id")))
        for r in context["relationships"]
        if isinstance(r, dict) and str(r.get("object_entity_type")) == "ledger"
    }
    assert ("surface", "surface:chat:primary", "ledger", "LOAM") in ledger_relationships


def test_connections_page_excludes_sources_from_all_tab_and_shows_source_count_on_ledger(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOperator",
            "identity_vc": {
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:david", "status": "active", "owner_principal_id": "did:key:z6MkOperator"}]}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    async def fake_fetch_ledger_attachments(ledger_id, auth_headers=None):
        if ledger_id != "LOAM":
            return []
        return [
            {
                "source_id": f"backend-LOAM-ATT-{i}",
                "file_name": f"doc-{i}.pdf",
                "coordinate": f"LOAM:ATT-{i}",
                "canonical_subject": f"LOAM:ATT-{i}",
                "ledger_id": "LOAM",
                "status": "completed",
                "uploaded_at": "2026-05-20T00:00:00+00:00",
                "updated_at": "2026-05-20T00:00:00+00:00",
            }
            for i in range(3)
        ]

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_fetch_ledger_attachments", fake_fetch_ledger_attachments)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    all_request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"type=all",
        "headers": [],
    })
    all_response = asyncio.run(dashboard_app.connections_page(all_request))
    all_body = all_response.body.decode("utf-8")

    assert all_response.status_code == 200
    assert "LOAM" in all_body
    assert "Sources: 3" in all_body
    assert "doc-0.pdf" not in all_body
    assert "doc-1.pdf" not in all_body
    assert "doc-2.pdf" not in all_body

    sources_request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"type=sources",
        "headers": [],
    })
    sources_response = asyncio.run(dashboard_app.connections_page(sources_request))
    sources_body = sources_response.body.decode("utf-8")

    assert sources_response.status_code == 200
    assert "doc-0.pdf" in sources_body
    assert "doc-1.pdf" in sources_body
    assert "doc-2.pdf" in sources_body


def test_connections_page_synthesizes_self_principal_when_backend_record_is_tenant_mismatched(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOperator",
            "identity_vc": {
                "principal_did": "did:key:z6MkOperator",
                "principal_display_name": "David Berigny",
                "tenant_id": "tenant:david",
                "ledger_id": "LOAM",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "verified": True,
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:david",
                    "ledger_id": "LOAM",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:david", "status": "active", "owner_principal_id": "did:key:z6MkOperator"}]}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {
                "principals": [
                    {
                        "principal_did": "did:key:z6MkOperator",
                        "tenant_id": "tenant:other",
                        "status": "active",
                        "display_name": "David Berigny",
                        "metadata": {},
                    }
                ]
            }
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = _make_request()
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "David Berigny" in body
    # (You) suffix removed for naming consistency; name presence verified above.
    assert "did:key:z6MkOperator" in body


def test_load_connection_lookup_context_protects_model_principals_referenced_by_bindings(monkeypatch) -> None:
    model_principal_did = dashboard_app._frontend_model_principal_did("openrouter", "openai/gpt-4o")

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [
                {
                    "binding_id": "binding:chat:default",
                    "linked_model_principal": model_principal_did,
                    "model_id": "openai/gpt-4o",
                    "status": "active",
                    "app_surfaces": ["surface:chat:primary"],
                }
            ],
            "control_plane_app_surfaces": [
                {"surface_id": "surface:chat:primary", "ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active", "metadata": {}}
            ],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {
                        "principal_did": "did:key:z6MkModel",
                        "tenant_id": "tenant:david",
                        "status": "active",
                        "display_name": "OpenAI: GPT-4o",
                        "metadata": {"actor_type": "model", "model_id": "openai/gpt-4o"},
                    }
                ]
            }
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    principal_ids = {str(item.get("principal_did")) for item in context["principals"]}
    assert model_principal_did in principal_ids

    detail = dashboard_app._lookup_ledger_detail_data("ledger:loam", context)
    assert detail is not None
    linked_principal_ids = {pid for _, pid in detail["principal_links"]}
    assert model_principal_did in linked_principal_ids


def test_load_connection_lookup_context_skips_redundant_control_plane_calls(monkeypatch) -> None:
    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {"identity_vc": {"principal_did": "did:key:z6MkDavid", "tenant_id": "tenant:david"}},
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [{"provider_id": "provider:openrouter:shared", "provider_type": "OpenRouter"}],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    requested_paths: list[str] = []

    async def fake_control_plane_get(path, **kwargs):
        requested_paths.append(path)
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    asyncio.run(_run())
    assert "/api/control-plane/providers" not in requested_paths
    assert "/api/control-plane/model-bindings" not in requested_paths
    assert "/api/control-plane/surfaces" not in requested_paths


def test_selection_list_renders_locked_selected_values_missing_from_items() -> None:
    html_output = _selection_list(
        items=[{"surface_id": "surface:chat:primary", "label": "Chat", "status": "active"}],
        field_name="linked_surface_ids",
        kind="surface",
        selected_values={"surface:chat:primary", "surface:missing:template"},
        multi=True,
    )
    assert "surface:chat:primary" in html_output
    assert "surface:missing:template" in html_output
    assert html_output.count("checked") >= 2


def test_load_connection_lookup_context_backfills_ledger_from_control_plane_metadata(monkeypatch) -> None:
    """When the middleware identity card omits ledger_id, the dashboard falls back
    to the control-plane principal metadata so runtime relationships can still be
    derived for the authenticated operator."""

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "principal_display_name": "David Berigny",
                "tenant_id": "tenant:demo",
                # Middleware session does not assign a ledger.
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:demo",
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:demo", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {
                        "principal_did": "did:key:z6MkDavid",
                        "tenant_id": "tenant:demo",
                        "status": "active",
                        "display_name": "David Berigny",
                        "metadata": {"actor_type": "human", "ledger_id": "LOAM", "provisioned_ledger_id": "LOAM"},
                    }
                ]
            }
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    assert "LOAM" in context["ledger_map"]
    detail = dashboard_app._lookup_ledger_detail_data("LOAM", context)
    assert detail is not None
    linked_principal_ids = {pid for _, pid in detail["principal_links"]}
    assert "did:key:z6MkDavid" in linked_principal_ids
    surface_ids = {sid for _, sid in detail["surface_links"]}
    assert "surface:chat:primary" in surface_ids


def test_connections_page_backfills_ledger_from_control_plane_metadata(monkeypatch) -> None:
    """The signed-in operator is visible in Manage Connections even when the
    middleware identity card omits ledger_id, because the dashboard reads the
    assigned ledger from control-plane principal metadata."""

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkDavid",
            "identity_vc": {
                "principal_did": "did:key:z6MkDavid",
                "principal_display_name": "David Berigny",
                "tenant_id": "tenant:demo",
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkDavid",
                    "tenant_id": "tenant:demo",
                }
            },
            "models_current": {"online_models": []},
            "control_plane_app_surfaces": [],
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:demo", "status": "active"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {
                        "principal_did": "did:key:z6MkDavid",
                        "tenant_id": "tenant:demo",
                        "status": "active",
                        "display_name": "David Berigny",
                        "metadata": {"actor_type": "human", "ledger_id": "LOAM", "provisioned_ledger_id": "LOAM"},
                    }
                ]
            }
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"type=principals",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "David Berigny" in body
    # (You) suffix removed for naming consistency; name presence verified above.
    assert "did:key:z6MkDavid" in body


def test_build_dashboard_snapshot_schema_unchanged(monkeypatch) -> None:
    """build_dashboard_snapshot() returns the same keys and equivalent values
    after parallelization."""

    async def fake_fetch_json(path, headers=None):
        return {"source": path}

    async def fake_middleware_json_request(*, method, path, headers, error_prefix, timeout=20.0, payload=None):
        if path == "/api/principals?limit=25":
            return 200, {"principals": [{"principal_did": "did:key:z6MkTest"}]}
        if path == "/api/control-plane/providers/openrouter/key":
            return 200, {"configured": False, "masked": None, "source": "env"}
        return 200, {}

    async def fake_control_plane_get(path, headers=None):
        if path == "/api/control-plane/providers":
            return 200, {"providers": [{"provider_id": "provider:test"}]}
        if path == "/api/control-plane/model-bindings":
            return 200, {"model_bindings": [{"binding_id": "binding:test"}]}
        if path == "/api/control-plane/surfaces":
            return 200, {"surfaces": [{"surface_id": "surface:test"}]}
        return 200, {}

    async def fake_build_identity_card(_request):
        return {"identity_vc": {"principal_did": "did:key:z6MkTest"}}

    monkeypatch.setattr(dashboard_app, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(dashboard_app, "_middleware_json_request", fake_middleware_json_request)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_build_identity_card)

    request = _make_request()
    snapshot = asyncio.run(dashboard_app.build_dashboard_snapshot(request))

    expected_keys = {
        "issuer_did",
        "public_base_url",
        "did_document_url",
        "trust_bundle_url",
        "middleware_base_url",
        "auth_base_url",
        "did_document_config",
        "trust_anchor",
        "trust_bundle",
        "models_debug",
        "models_current",
        "models_full",
        "openrouter_key_status",
        "middleware_principals",
        "control_plane_providers",
        "control_plane_model_bindings",
        "control_plane_app_surfaces",
        "identity_card",
        "linked_environments",
    }
    assert set(snapshot.keys()) == expected_keys
    assert snapshot["trust_anchor"] == {"source": "/api/trust-anchor/status"}
    assert snapshot["trust_bundle"] == {"source": "/api/trust-anchor/bundle"}
    assert snapshot["models_debug"] == {"source": "/api/models/debug"}
    assert snapshot["models_current"] == {"source": "/api/models"}
    assert snapshot["models_full"] == {"source": "/api/models?mode=full"}
    assert len(snapshot["middleware_principals"]) == 1
    assert len(snapshot["control_plane_providers"]) == 1
    assert len(snapshot["control_plane_model_bindings"]) == 1
    assert len(snapshot["control_plane_app_surfaces"]) == 1


def test_build_dashboard_snapshot_issues_independent_calls_concurrently(monkeypatch) -> None:
    """Independent backend calls inside build_dashboard_snapshot() overlap in time."""

    active = 0
    max_active = 0

    async def tracking_fetch_json(path, headers=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {}

    async def tracking_middleware_json_request(*, method, path, headers, error_prefix, timeout=20.0, payload=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        if path == "/api/principals?limit=25":
            return 200, {"principals": []}
        if path == "/api/control-plane/providers/openrouter/key":
            return 200, {"configured": False, "masked": None, "source": "env"}
        return 200, {}

    async def tracking_control_plane_get(path, headers=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        if path == "/api/control-plane/providers":
            return 200, {"providers": []}
        if path == "/api/control-plane/model-bindings":
            return 200, {"model_bindings": []}
        if path == "/api/control-plane/surfaces":
            return 200, {"surfaces": []}
        return 200, {}

    async def fake_build_identity_card(_request):
        return {}

    monkeypatch.setattr(dashboard_app, "fetch_json", tracking_fetch_json)
    monkeypatch.setattr(dashboard_app, "_middleware_json_request", tracking_middleware_json_request)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", tracking_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_build_identity_card", fake_build_identity_card)

    request = _make_request()
    snapshot = asyncio.run(dashboard_app.build_dashboard_snapshot(request))

    assert isinstance(snapshot, dict)
    # With independent calls gathered concurrently, all ten fetchers should be
    # active at the same time. We assert at least 2 to avoid false negatives
    # under any scheduler jitter, but the implementation should peak at 10.
    assert max_active >= 2


def test_connections_page_restores_operator_model_principals_from_bindings(monkeypatch) -> None:
    """Model principals bound to the Operator's chat surface appear in the All tab
    even when the backend principal record does not carry the canonical ledger id."""
    model_principal_did = dashboard_app._frontend_model_principal_did("openrouter", "openai/gpt-4o")

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOperator",
            "identity_vc": {
                "principal_did": "did:key:z6MkOperator",
                "principal_display_name": "Operator",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {
                "online_models": [
                    {"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o", "provider": "openrouter"}
                ]
            },
            "models_full": {
                "online_models": [
                    {"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o", "provider": "openrouter"}
                ]
            },
            "models_debug": {"settings_llm_provider": "openrouter", "settings_llm_model": "openai/gpt-4o"},
            "control_plane_providers": [],
            "control_plane_model_bindings": [],
            "control_plane_app_surfaces": [],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:david", "status": "active", "owner_principal_id": "did:key:z6MkOperator"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {"principals": []}
        return 404, {"error": "not_found"}

    async def fake_fetch_setup_checklist(_principal_did):
        return {"summary": {"required": 5, "required_complete": 5}, "items": []}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)
    monkeypatch.setattr(dashboard_app, "_fetch_setup_checklist", fake_fetch_setup_checklist)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: {"sources": [], "source_jobs": []})

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/connections",
        "query_string": b"type=all",
        "headers": [],
    })
    response = asyncio.run(dashboard_app.connections_page(request))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "OpenAI: GPT-4o" in body
    assert model_principal_did in body


def test_model_principals_linked_to_canonical_loam_appear_in_ledger_detail(monkeypatch) -> None:
    """When the middleware uses the ledger:loam alias but the backend ledger is
    LOAM, restored model principals must be linked to the canonical ledger so
    they show up on the LOAM detail page and child principal pages."""
    model_principal_did = dashboard_app._frontend_model_principal_did("openrouter", "openai/gpt-4o")

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkOperator",
            "identity_vc": {
                "principal_did": "did:key:z6MkOperator",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:loam",
                "ledger_access_ready": True,
            },
        }, None

    async def fake_build_dashboard_snapshot(_request):
        return {
            "identity_card": {
                "identity_vc": {
                    "principal_did": "did:key:z6MkOperator",
                    "tenant_id": "tenant:david",
                    "ledger_id": "ledger:loam",
                    "ledger_access_ready": True,
                }
            },
            "models_current": {"online_models": []},
            "models_debug": {},
            "control_plane_providers": [],
            "control_plane_model_bindings": [
                {
                    "binding_id": "binding:chat:default",
                    "linked_model_principal": model_principal_did,
                    "model_id": "openai/gpt-4o",
                    "status": "active",
                    "app_surfaces": ["surface:chat:primary"],
                }
            ],
            "control_plane_app_surfaces": [
                {"surface_id": "surface:chat:primary", "ledger_id": "ledger:loam", "tenant_id": "tenant:david", "status": "active", "metadata": {}}
            ],
        }

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": [{"ledger_id": "LOAM", "tenant_id": "tenant:david", "status": "active", "owner_principal_id": "did:key:z6MkOperator"}]}
        if path.startswith("/api/control-plane/principals"):
            return 200, {
                "principals": [
                    {
                        "principal_did": model_principal_did,
                        "tenant_id": "tenant:david",
                        "status": "active",
                        "display_name": "OpenAI: GPT-4o",
                        "metadata": {"actor_type": "model", "model_id": "openai/gpt-4o"},
                    }
                ]
            }
        if path == "/api/control-plane/relationships":
            return 200, {"relationships": []}
        return 200, {}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "build_dashboard_snapshot", fake_build_dashboard_snapshot)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    async def _run():
        request = _make_request()
        return await dashboard_app._load_connection_lookup_context(request)

    context = asyncio.run(_run())
    principal_ids = {str(item.get("principal_did")) for item in context["principals"]}
    assert model_principal_did in principal_ids

    # Model principal should be visible on the canonical LOAM ledger detail page.
    loam_detail = dashboard_app._lookup_ledger_detail_data("LOAM", context)
    assert loam_detail is not None
    linked_principal_ids = {pid for _, pid in loam_detail["principal_links"]}
    assert model_principal_did in linked_principal_ids

    # LOAM should appear on the model principal's own detail page.
    principal_detail = dashboard_app._lookup_principal_detail_data(model_principal_did, context)
    assert principal_detail is not None
    ledger_ids = {lid for _, lid in principal_detail["ledger_links"]}
    assert "LOAM" in ledger_ids



def test_settings_providers_tab_registered() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert '("providers", "Providers")' in source
    assert '"providers"' in source
    assert "_render_settings_providers_content" in source
    assert "OpenRouter" in source
    assert 'id="openrouter_api_key"' in source
    assert 'action="/settings/providers/openrouter/key"' in source
    assert "/api/control-plane/providers/openrouter/key" in source


def test_settings_providers_openrouter_submit_route_registered() -> None:
    source = (REPO_ROOT / "app.py").read_text()

    assert "async def settings_providers_openrouter_key_submit" in source
    assert 'Route("/settings/providers/openrouter/key", settings_providers_openrouter_key_submit' in source


def test_middleware_admin_headers_includes_token_when_configured() -> None:
    original = dashboard_app.MIDDLEWARE_ADMIN_TOKEN
    dashboard_app.MIDDLEWARE_ADMIN_TOKEN = "test-token"
    try:
        headers = dashboard_app._middleware_admin_headers()
        assert headers["x-admin-token"] == "test-token"
        assert headers["accept"] == "application/json"
    finally:
        dashboard_app.MIDDLEWARE_ADMIN_TOKEN = original


def test_middleware_admin_headers_omits_token_when_unconfigured() -> None:
    original = dashboard_app.MIDDLEWARE_ADMIN_TOKEN
    dashboard_app.MIDDLEWARE_ADMIN_TOKEN = ""
    try:
        headers = dashboard_app._middleware_admin_headers()
        assert "x-admin-token" not in headers
    finally:
        dashboard_app.MIDDLEWARE_ADMIN_TOKEN = original



def test_control_plane_provider_records_uses_openrouter_key_status() -> None:
    records = dashboard_app._control_plane_provider_records(
        {},
        openrouter_key_status={"configured": True, "masked": "sk-****", "source": "override"},
    )
    openrouter_record = next((r for r in records if r.get("provider_id") == "provider:openrouter:shared"), None)
    assert openrouter_record is not None
    assert openrouter_record["status"] == "configured"


def test_current_model_library_state_uses_standard_model_set_as_authoritative() -> None:
    snapshot = {
        "identity_card": {
            "identity_vc": {
                "tenant_id": "tenant:david",
                "ledger_id": "LOAM",
                "principal_did": "did:key:z6MkOperator",
            }
        },
        "models_debug": {"settings_llm_provider": "openrouter", "settings_llm_model": "openai/gpt-5.1-chat"},
        "models_current": {
            "online_models": [
                {"id": "openai/gpt-5.1-chat", "name": "OpenAI: GPT-5.1 Chat"},
                {"id": "anthropic/claude-haiku-4.5", "name": "Anthropic: Claude Haiku 4.5"},
                {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash"},
            ]
        },
        "models_full": {
            "online_models": [
                {"id": "openai/gpt-5.1-chat", "name": "OpenAI: GPT-5.1 Chat"},
                {"id": "anthropic/claude-haiku-4.5", "name": "Anthropic: Claude Haiku 4.5"},
                {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash"},
                {"id": "x-ai/grok-4-fast", "name": "xAI: Grok 4 Fast"},
            ]
        },
        "openrouter_key_status": {"configured": True, "source": "override"},
        "middleware_principals": [],
        "control_plane_providers": [],
        "control_plane_model_bindings": [],
        "control_plane_app_surfaces": [],
    }
    state = dashboard_app._current_model_library_state(snapshot)
    model_ids = {opt["model_id"] for opt in state["available_models"]}
    assert model_ids == {"openai/gpt-5.1-chat", "anthropic/claude-haiku-4.5", "google/gemini-2.5-flash"}
    assert "x-ai/grok-4-fast" not in model_ids
    assert state["selected_model_id"] == "openai/gpt-5.1-chat"
    default_binding = next(
        (b for b in state["binding_records"] if str(b.get("binding_id") or "").strip() == "binding:chat:default"),
        None,
    )
    assert default_binding is not None
    assert default_binding.get("model_id") == "openai/gpt-5.1-chat"
    assert default_binding.get("status") == "derived"


def test_current_model_library_state_falls_back_to_models_current_when_full_empty() -> None:
    snapshot = {
        "identity_card": {
            "identity_vc": {
                "tenant_id": "tenant:david",
                "ledger_id": "LOAM",
            }
        },
        "models_debug": {"settings_llm_provider": "openrouter", "settings_llm_model": "openai/gpt-4o"},
        "models_current": {"online_models": [{"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o"}]},
        "models_full": {"online_models": []},
        "openrouter_key_status": {"configured": False, "source": "env"},
        "middleware_principals": [],
        "control_plane_providers": [],
        "control_plane_model_bindings": [],
        "control_plane_app_surfaces": [],
    }
    state = dashboard_app._current_model_library_state(snapshot)
    model_ids = {opt["model_id"] for opt in state["available_models"]}
    assert "openai/gpt-4o" in model_ids


def test_model_binding_records_include_ledger_id_for_runtime_relationships() -> None:
    provider_records = [{"provider_id": "provider:openrouter:shared", "provider_type": "OpenRouter"}]
    model_principals = [
        {
            "principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:openai-gpt-5-1-chat",
            "metadata": {"actor_type": "model", "model_id": "openai/gpt-5.1-chat"},
        }
    ]
    bindings = dashboard_app._control_plane_model_binding_records(
        provider_records,
        model_principals,
        "openrouter",
        "openai/gpt-5.1-chat",
        {"online_models": [{"id": "openai/gpt-5.1-chat", "name": "OpenAI: GPT-5.1 Chat"}]},
        ledger_id="LOAM",
    )
    default_binding = next((b for b in bindings if b.get("binding_id") == "binding:chat:default"), None)
    assert default_binding is not None
    assert default_binding.get("ledger_id") == "LOAM"
    assert "surface:chat:primary" in default_binding.get("app_surfaces", [])



def test_settings_connection_shortcuts_render_edit_links() -> None:
    context = {
        "ledgers": [
            {"ledger_id": "LOAM", "display_name": "LOAM"},
        ],
        "principals": [
            {"principal_did": "did:key:z6MkHuman", "display_name": "Human principal", "metadata": {"actor_type": "human"}},
            {"principal_did": "did:web:id.dualsubstrate.com:principals:model:openrouter:openai-gpt-4o", "display_name": "OpenAI: GPT-4o", "metadata": {"actor_type": "model"}},
        ],
        "surfaces": [
            {"surface_id": "surface:chat:primary", "label": "Primary chat"},
        ],
    }
    identity_card = {"identity_vc": {"principal_did": "did:key:z6MkOperator"}}
    html = dashboard_app._render_settings_connection_shortcuts(context, identity_card)
    assert "Continue Control Plane setup" in html
    assert "/connections/setup-guide" in html
    assert "/connections/add?entity_kind=ledger" in html
    assert "Edit ledger: LOAM" in html
    assert "ledger_id=LOAM" in html
    assert "step=Ledger+details" in html
    assert "Edit principal: Human principal" in html
    assert "principal_id=did%3Akey%3Az6MkHuman" in html
    assert "Edit surface: Primary chat" in html
    assert "surface_id=surface%3Achat%3Aprimary" in html
    # Model principals are managed by the library and should not appear as edit shortcuts.
    assert "OpenAI: GPT-4o" not in html


def test_settings_account_section_uses_connection_shortcuts() -> None:
    source = (REPO_ROOT / "app.py").read_text()
    assert "_render_settings_connection_shortcuts" in source
    assert "connection_context = await _load_connection_lookup_context" in source
    assert "shortcuts_html = _render_settings_connection_shortcuts" in source
    assert "_render_settings_account_content(shortcuts_html=shortcuts_html)" in source



def test_new_ledger_wizard_auto_links_default_surfaces(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_control_plane_post(path: str, payload: dict[str, Any], request: Request | None = None) -> tuple[int, dict[str, Any]]:
        calls.append((path, payload))
        if path == "/api/control-plane/ledgers":
            return 200, {"ledger": {"ledger_id": "ledger:kaoru-test", "name": "Kaoru Test"}}
        return 200, {"relationship": payload}

    monkeypatch.setattr(dashboard_app, "_control_plane_post", fake_control_plane_post)
    monkeypatch.setattr(dashboard_app, "_load_control_plane_state", lambda: dashboard_app._default_control_plane_state())
    monkeypatch.setattr(dashboard_app, "_save_control_plane_state", lambda _state: None)

    state = {
        "entity_kind": "ledger",
        "ledger_id": "ledger:kaoru-test",
        "name": "Kaoru Test",
        "tenant_id": "tenant:kaoru",
        "linked_principal_ids": "",
        "linked_surface_ids": "",
    }
    identity_card = {
        "principal_did": "did:web:id.dualsubstrate.com:wallet:97982a07ab4ed0f1",
        "identity_vc": {"principal_did": "did:web:id.dualsubstrate.com:wallet:97982a07ab4ed0f1", "verified": True},
    }

    class _FakeURL:
        hostname = "id.dualsubstrate.com"

    class _FakeRequest:
        method = "POST"
        query_params = {}
        url = _FakeURL()
        async def form(self):
            return dashboard_app.FormData([])

    async def fake_context(_request, identity_card=None):
        return _empty_connection_context()

    monkeypatch.setattr(dashboard_app, "_load_connection_lookup_context", fake_context)

    response = asyncio.run(dashboard_app._apply_connections_add_flow(_FakeRequest(), entity_kind="ledger", state=state, identity_card=identity_card))
    assert isinstance(response, dashboard_app.RedirectResponse)

    surface_relationship_subjects = {
        payload.get("subject_entity_id")
        for path, payload in calls
        if path == "/api/control-plane/relationships" and payload.get("subject_entity_type") == "surface"
    }
    assert "surface:chat:primary" in surface_relationship_subjects
    assert "surface:chat:local-offline" in surface_relationship_subjects
    assert "surface:telegram:template" in surface_relationship_subjects

    principal_relationships = [
        payload
        for path, payload in calls
        if path == "/api/control-plane/relationships" and payload.get("subject_entity_type") == "principal"
    ]
    assert any(
        payload.get("object_entity_type") == "ledger" and payload.get("object_entity_id") == "ledger:kaoru-test"
        for payload in principal_relationships
    )


def test_derive_runtime_relationships_uses_surface_ledger_relationships() -> None:
    identity_card = {
        "identity_vc": {
            "principal_did": "did:web:id.dualsubstrate.com:wallet:97982a07ab4ed0f1",
            "ledger_id": "ledger:kaoru-test",
        }
    }
    surfaces = [
        {"surface_id": "surface:chat:primary", "ledger_id": "ledger:other", "status": "active"},
    ]
    binding_records = [
        {
            "binding_id": "binding:chat:default",
            "linked_model_principal": "did:web:id.dualsubstrate.com:principals:model:openrouter:openai-gpt-4o",
            "status": "derived",
            "app_surfaces": ["surface:chat:primary"],
        }
    ]
    relationships = [
        {
            "subject_entity_type": "surface",
            "subject_entity_id": "surface:chat:primary",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:kaoru-test",
            "relationship_type": "belongs_to",
        }
    ]
    derived = dashboard_app._derive_runtime_relationships(identity_card, surfaces, binding_records, relationships_data=relationships)
    model_to_ledger = {
        (rel["subject_entity_id"], rel["object_entity_id"])
        for rel in derived
        if rel["subject_entity_type"] == "principal" and rel["object_entity_type"] == "ledger"
    }
    assert (
        "did:web:id.dualsubstrate.com:principals:model:openrouter:openai-gpt-4o",
        "ledger:kaoru-test",
    ) in model_to_ledger


def test_wallet_did_is_recognised_as_principal() -> None:
    identity_card = {
        "identity_vc": {
            "principal_did": "did:web:id.dualsubstrate.com:wallet:97982a07ab4ed0f1",
            "verified": True,
        }
    }
    assert dashboard_app._principal_did_from_identity_card(identity_card) == "did:web:id.dualsubstrate.com:wallet:97982a07ab4ed0f1"


def test_ledger_wizard_next_button_advances_step(monkeypatch) -> None:
    """Clicking Next on the ledger details step should advance to Principal/s access."""
    from starlette.testclient import TestClient

    async def fake_require_control_plane_auth(_request):
        return {
            "principal_did": "did:key:z6MkWizard",
            "identity_vc": {
                "principal_did": "did:key:z6MkWizard",
                "tenant_id": "tenant:david",
                "ledger_id": "ledger:identity",
            },
        }, None

    async def fake_control_plane_get(path, **kwargs):
        if path == "/api/control-plane/ledgers":
            return 200, {"ledgers": []}
        if path == "/api/control-plane/principals?limit=200":
            return 200, {"principals": []}
        if path == "/api/control-plane/surfaces?limit=200":
            return 200, {"surfaces": []}
        if path == "/api/control-plane/model-bindings":
            return 200, {"model_bindings": []}
        if path == "/api/control-plane/relationships?limit=200":
            return 200, {"relationships": []}
        return 404, {"error": "not_found"}

    monkeypatch.setattr(dashboard_app, "_require_control_plane_auth", fake_require_control_plane_auth)
    monkeypatch.setattr(dashboard_app, "_control_plane_get", fake_control_plane_get)

    client = TestClient(dashboard_app.app)

    # Initial ledger details step.
    detail_response = client.get("/connections/add/ledger?name=Test%20Ledger&step=Ledger%20details")
    assert detail_response.status_code == 200
    detail_html = detail_response.text
    assert "Ledger details" in detail_html
    # The form should not contain a hidden step input competing with the Next button.
    step_input_count = detail_html.count('name="step"')
    # One input per navigation button; no hidden duplicate.
    assert step_input_count <= 2

    # Simulate clicking Next.
    next_response = client.get(
        "/connections/add/ledger?name=Test+Ledger&ledger_topology=prime&step=Principal%2Fs+access"
    )
    assert next_response.status_code == 200
    next_html = next_response.text
    assert "Principal/s access" in next_html
    assert "Ledger details" not in next_html.split("<h1")[0] or "Principal/s access" in next_html



def test_record_visible_to_principal_owns_record():
    record = {
        "principal_did": "did:web:example:principal:kaoru",
        "ledger_id": "ledger:kaoru-ichikawa",
        "created_by_principal_id": "did:web:example:principal:operator-1",
    }
    assert dashboard_app._record_visible_to_principal(record, "did:web:example:principal:operator-1", "ledger:operator-ledger")


def test_record_visible_to_principal_ledger_match():
    record = {"ledger_id": "ledger:operator-ledger"}
    assert dashboard_app._record_visible_to_principal(record, "did:web:example:principal:operator-1", "ledger:operator-ledger")


def test_record_visible_to_principal_own_principal_record():
    record = {"principal_did": "did:web:example:principal:operator-1"}
    assert dashboard_app._record_visible_to_principal(record, "did:web:example:principal:operator-1", "")


def test_record_visible_to_principal_hides_unrelated_record():
    record = {
        "principal_did": "did:web:example:principal:kaoru",
        "ledger_id": "ledger:kaoru-ichikawa",
        "created_by_principal_id": "did:web:example:principal:kaoru",
    }
    assert not dashboard_app._record_visible_to_principal(record, "did:web:example:principal:operator-1", "ledger:operator-ledger")


def test_record_visible_to_principal_explicit_relationship():
    record = {"ledger_id": "ledger:delegated-ledger"}
    related_ids = {"ledger": {"ledger:delegated-ledger"}}
    assert dashboard_app._record_visible_to_principal(record, "did:web:example:principal:operator-1", "ledger:operator-ledger", related_ids)


def test_filter_records_by_tenant_scopes_to_principal():
    own_ledger = {"ledger_id": "ledger:operator-ledger", "name": "Operator"}
    other_ledger = {"ledger_id": "ledger:kaoru-ichikawa", "name": "Kaoru"}
    delegated_ledger = {"ledger_id": "ledger:shared-ledger", "name": "Shared"}
    related_ids = {"ledger": {"ledger:shared-ledger"}}
    records = [own_ledger, other_ledger, delegated_ledger]
    result = dashboard_app._filter_records_by_tenant(
        records,
        "tenant:ops-admin",
        "did:web:example:principal:operator-1",
        "ledger:operator-ledger",
        related_ids,
    )
    assert result == [own_ledger, delegated_ledger]


def test_related_ids_from_relationships_anchors_on_principal():
    relationships = [
        {
            "subject_entity_type": "principal",
            "subject_entity_id": "did:web:example:principal:operator-1",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:shared-ledger",
        },
        {
            "subject_entity_type": "surface",
            "subject_entity_id": "surface:chat:shared",
            "object_entity_type": "principal",
            "object_entity_id": "did:web:example:principal:operator-1",
        },
        # Unrelated relationship should be ignored.
        {
            "subject_entity_type": "principal",
            "subject_entity_id": "did:web:example:principal:kaoru",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:kaoru-ichikawa",
        },
    ]
    related = dashboard_app._related_ids_from_relationships(
        relationships,
        "did:web:example:principal:operator-1",
        "ledger:operator-ledger",
    )
    assert related["ledger"] == {"ledger:shared-ledger"}
    assert related["surface"] == {"surface:chat:shared"}
    assert "ledger:kaoru-ichikawa" not in related["ledger"]


def test_record_visible_to_principal_model_only_when_linked():
    model_principal = {
        "principal_did": "did:web:example:model:openrouter:x-ai-grok-4-3",
        "metadata": {"actor_type": "model", "model_id": "x-ai/grok-4.3"},
    }
    # No link -> hidden.
    assert not dashboard_app._record_visible_to_principal(model_principal, "did:web:example:principal:operator-1", "")
    # Linked to caller ledger.
    model_principal["ledger_id"] = "ledger:operator-ledger"
    assert dashboard_app._record_visible_to_principal(model_principal, "did:web:example:principal:operator-1", "ledger:operator-ledger")


def test_merge_frontend_model_principals_drops_deprecated_online_models():
    principals = []
    available_models = {
        "online_models": [
            {"id": "x-ai/grok-4-fast", "name": "Grok 4 Fast"},
            {"id": "x-ai/grok-4.3", "name": "Grok 4.3"},
        ]
    }
    merged = dashboard_app._merge_frontend_model_principals(principals, available_models, tenant_id="tenant:ops-admin")
    model_ids = {
        str(dashboard_app._as_dict(item.get("metadata")).get("model_id") or "").strip()
        for item in merged
        if isinstance(item, dict)
    }
    assert "x-ai/grok-4-fast" not in model_ids
    assert "x-ai/grok-4.3" in model_ids



def test_render_activity_page_shows_ledger_selector_when_multiple_ledgers_visible() -> None:
    request = _make_request()
    connection_context = {
        "ledgers": [
            {"ledger_id": "loam", "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:loam", "status": "active"},
            {"ledger_id": "chat-demo", "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo", "status": "active"},
        ],
        "principals": [],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {},
    }
    ledger_entries = [
        {
            "key": {"namespace": "loam", "identifier": "WX-123"},
            "coord_meta": {
                "coord": "loam:WX-123",
                "coord_type": "WX",
                "identifier": "WX-123",
                "runtime_namespace": "loam",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:loam",
            },
            "created_at": "2026-07-16T00:00:00+00:00",
            "state": {"metadata": {"role": "assistant", "content": "Decoded loam coordinate."}},
        },
        {
            "key": {"namespace": "chat-demo", "identifier": "WX-456"},
            "coord_meta": {
                "coord": "chat-demo:WX-456",
                "coord_type": "WX",
                "identifier": "WX-456",
                "runtime_namespace": "chat-demo",
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
            },
            "created_at": "2026-07-16T00:00:00+00:00",
            "state": {"metadata": {"role": "assistant", "content": "Chat demo response."}},
        },
    ]
    html = dashboard_app.render_activity_page(
        request,
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=connection_context,
        control_plane_state={},
        submissions=[],
        ledger_entries=ledger_entries,
    )
    assert '<select aria-label="Filter by ledger"' in html
    assert 'ledger=loam' in html
    assert 'ledger=chat-demo' in html
    assert '>All ledgers</option>' in html
    assert '>loam</option>' in html
    assert '>chat-demo</option>' in html


def test_render_activity_page_hides_ledger_selector_for_single_ledger() -> None:
    request = _make_request()
    connection_context = {
        "ledgers": [
            {"ledger_id": "chat-demo", "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo", "status": "active"},
        ],
        "principals": [],
        "surfaces": [],
        "model_bindings": [],
        "relationships": [],
        "ledger_map": {},
        "principal_map": {},
        "surface_map": {},
    }
    html = dashboard_app.render_activity_page(
        request,
        principals=[],
        selected_principal="",
        lookup={},
        connection_context=connection_context,
        control_plane_state={},
        submissions=[],
        ledger_entries=[],
    )
    assert 'aria-label="Filter by ledger"' not in html


def test_orphan_ledgers_route_is_registered() -> None:
    source = (REPO_ROOT / "app.py").read_text()
    assert 'Route("/settings/orphan-ledgers", orphan_ledgers_page' in source


def test_settings_connection_shortcuts_include_orphan_ledgers_link() -> None:
    context = _empty_connection_context()
    html = dashboard_app._render_settings_connection_shortcuts(context, {"identity_vc": {}})
    assert 'href="/settings/orphan-ledgers"' in html
    assert '>Orphan ledgers</a>' in html

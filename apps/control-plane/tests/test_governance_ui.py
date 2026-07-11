import asyncio
import html
import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location("dss_dashboard_app", REPO_ROOT / "app.py")
dashboard_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard_app)


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


def test_relationship_table_includes_remove_button_for_ledger_owner() -> None:
    entity_id = "did:web:example.com:principals:alice"
    rows = [
        {
            "entity_type": "principal",
            "entity_id": entity_id,
            "label": "Alice",
            "secondary": entity_id,
            "status": "active",
        }
    ]
    html_out = dashboard_app._render_relationship_table(
        rows,
        connection_context=_empty_connection_context(),
        owner_entity_type="ledger",
        owner_entity_id="ledger-001",
    )
    assert 'class="btn danger relationship-remove-trigger"' in html_out
    assert f'data-remove-entity-id="{html.escape(entity_id)}"' in html_out
    assert 'data-remove-ledger-id="ledger-001"' in html_out
    assert "Remove from ledger" in html_out


def test_relationship_table_omits_remove_button_for_non_ledger_owner() -> None:
    entity_id = "did:web:example.com:ledgers:ledger-001"
    rows = [
        {
            "entity_type": "ledger",
            "entity_id": entity_id,
            "label": "Ledger 001",
            "secondary": entity_id,
            "status": "active",
        }
    ]
    html_out = dashboard_app._render_relationship_table(
        rows,
        connection_context=_empty_connection_context(),
        owner_entity_type="principal",
        owner_entity_id="did:web:example.com:principals:alice",
    )
    assert "relationship-remove-trigger" not in html_out
    assert "Remove from ledger" not in html_out


def test_render_removal_impact_modal_markup() -> None:
    modal = dashboard_app.render_removal_impact_modal()
    assert 'id="removal-impact-modal"' in modal
    assert 'id="impact-entity-type"' in modal
    assert 'id="impact-entity-id"' in modal
    assert 'id="impact-ledger-id"' in modal
    assert 'id="impact-confirm-token"' in modal
    assert 'id="impact-loading"' in modal
    assert 'id="impact-content"' in modal
    assert 'id="impact-summary"' in modal
    assert 'id="impact-affected-list"' in modal
    assert 'id="impact-warnings"' in modal
    assert 'id="impact-error"' in modal
    assert 'id="impact-confirm-input"' in modal
    assert 'id="impact-ack-checkbox"' in modal
    assert 'id="impact-confirm-btn"' in modal
    assert "disabled" in modal  # confirm button starts disabled
    assert "Remove" in modal


def test_entity_link_editor_modal_id() -> None:
    assert (
        dashboard_app._entity_link_editor_modal_id("ledger", "ledger-001")
        == "edit-links-ledger-ledger-001"
    )
    assert (
        dashboard_app._entity_link_editor_modal_id("principal", "did:web:alice")
        == "edit-links-principal-did-web-alice"
    )


def test_link_editor_checkbox_list_renders_switches() -> None:
    items = [("ledger", {"ledger_id": "ledger-001", "display_name": "L1"})]
    html_out = dashboard_app._link_editor_checkbox_list(
        "principal",
        "did:web:example.com:principals:alice",
        items,
        {"ledger": {"ledger-001"}},
    )
    assert 'data-link-type="ledger"' in html_out
    assert 'data-is-model="false"' in html_out
    assert 'value="ledger-001"' in html_out
    assert "checked" in html_out
    assert 'class="relationship-switch"' in html_out


def test_link_editor_checkbox_list_renders_model_principal_switch() -> None:
    principal_did = "did:web:id.dualsubstrate.com:principals:model:openrouter:my-model"
    items = [
        (
            "principal",
            {
                "principal_did": principal_did,
                "display_name": "My Model",
                "metadata": {"actor_type": "model", "model_id": "openrouter/my-model"},
            },
        )
    ]
    html_out = dashboard_app._link_editor_checkbox_list(
        "ledger",
        "ledger-001",
        items,
        {},
        model_bindings=[
            {
                "binding_id": "binding:chat:my-model",
                "model_id": "openrouter/my-model",
                "linked_model_principal": principal_did,
                "source": "connection-link-editor",
                "status": "active",
            }
        ],
    )
    assert 'data-is-model="true"' in html_out
    assert 'data-binding-id="binding:chat:my-model"' in html_out
    assert 'data-model-id="openrouter/my-model"' in html_out
    assert f'data-linked-principal="{principal_did}"' in html_out
    assert "checked" in html_out


def test_render_entity_link_editor_modal_for_principal() -> None:
    context = _empty_connection_context()
    context["ledgers"] = [{"ledger_id": "ledger-001", "display_name": "L1"}]
    context["ledger_map"] = {"ledger-001": context["ledgers"][0]}
    context["principals"] = [{"principal_did": "did:web:bob", "display_name": "Bob"}]
    context["principal_map"] = {"did:web:bob": context["principals"][0]}
    context["surfaces"] = [{"surface_id": "surface-001", "display_name": "S1"}]
    context["surface_map"] = {"surface-001": context["surfaces"][0]}
    html_out = dashboard_app._render_entity_link_editor_modal(
        owner_entity_type="principal",
        owner_entity_id="did:web:alice",
        owner_name="Alice",
        connection_context=context,
        current_links={"ledger": {"ledger-001"}},
    )
    assert 'id="edit-links-principal-did-web-alice"' in html_out
    assert 'class="entity-link-editor"' in html_out
    assert 'data-owner-ledger-id=""' in html_out
    assert 'id="entity-link-editor-update"' in html_out
    assert 'id="entity-link-editor-summary"' in html_out
    assert '<span>Name</span>' in html_out
    assert '<span>Connection</span>' in html_out
    assert 'data-link-type="ledger"' in html_out
    assert 'data-link-type="surface"' in html_out
    assert 'data-link-type="principal"' not in html_out


def test_render_entity_link_editor_modal_for_ledger_omits_owner_type() -> None:
    context = _empty_connection_context()
    context["principals"] = [{"principal_did": "did:web:bob", "display_name": "Bob"}]
    context["principal_map"] = {"did:web:bob": context["principals"][0]}
    html_out = dashboard_app._render_entity_link_editor_modal(
        owner_entity_type="ledger",
        owner_entity_id="ledger-001",
        owner_name="Ledger One",
        connection_context=context,
        current_links={},
    )
    assert 'data-owner-ledger-id="ledger-001"' in html_out
    assert 'data-link-type="principal"' in html_out
    assert 'data-link-type="ledger"' not in html_out


def test_entity_links_endpoint_adds_and_removes_links() -> None:
    """api_control_plane_entity_links retires removed links and activates new ones."""
    owner_id = "did:web:example.com:principals:alice"
    ledger_one = "ledger:one"
    ledger_two = "ledger:two"
    context = _empty_connection_context()
    context["ledger_map"] = {
        ledger_one: {"ledger_id": ledger_one, "display_name": "One"},
        ledger_two: {"ledger_id": ledger_two, "display_name": "Two"},
    }
    context["principal_map"] = {
        owner_id: {
            "principal_did": owner_id,
            "display_name": "Alice",
            "status": "active",
            "metadata": {"ledger_id": ledger_one},
        }
    }
    context["relationships"] = [
        {
            "relationship_id": dashboard_app._relationship_record_id(
                "principal", owner_id, "ledger", ledger_one
            ),
            "subject_entity_type": "principal",
            "subject_entity_id": owner_id,
            "object_entity_type": "ledger",
            "object_entity_id": ledger_one,
            "relationship_type": "member_of_ledger",
            "status": "active",
            "enabled_state": "enabled",
        }
    ]

    original_session = dashboard_app._control_plane_json_session
    original_load = dashboard_app._load_connection_lookup_context
    original_post = dashboard_app._control_plane_post
    original_update_principal = dashboard_app._update_principal_ledger_metadata

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_session(request):
        return ({"identity_vc": {"principal_did": owner_id}}, None)

    async def fake_load(request, identity_card=None):
        return context

    async def fake_post(path, payload, request=None):
        calls.append((path, dict(payload)))
        return (200, {"relationship": payload})

    async def fake_update_principal(*args, **kwargs):
        return None

    dashboard_app._control_plane_json_session = fake_session
    dashboard_app._load_connection_lookup_context = fake_load
    dashboard_app._control_plane_post = fake_post
    dashboard_app._update_principal_ledger_metadata = fake_update_principal

    class FakeRequest:
        async def json(self):
            return {
                "owner_entity_type": "principal",
                "owner_entity_id": owner_id,
                "links": {"ledger": [ledger_two]},
            }

    try:
        response = asyncio.run(
            dashboard_app.api_control_plane_entity_links(FakeRequest())
        )
        assert response.status_code == 200
        statuses = {
            (c[1].get("subject_entity_id"), c[1].get("object_entity_id"), c[1].get("status"))
            for c in calls
        }
        assert (owner_id, ledger_one, "retired") in statuses
        assert (owner_id, ledger_two, "active") in statuses
    finally:
        dashboard_app._control_plane_json_session = original_session
        dashboard_app._load_connection_lookup_context = original_load
        dashboard_app._control_plane_post = original_post
        dashboard_app._update_principal_ledger_metadata = original_update_principal

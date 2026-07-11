from pathlib import Path


def test_account_setup_route_redirects_to_control_plane() -> None:
    route_source = Path("routes/home.py").read_text()
    js_source = Path("static/js/app.js").read_text()

    assert '@rt("/account/setup")' in route_source
    assert "RedirectResponse" in route_source
    assert "settings.CONTROL_PLANE_BASE" in route_source
    assert 'id="account-setup-checklist"' not in route_source
    assert "function renderSetupChecklist" in js_source
    assert "/account/current/setup-checklist" in js_source

"""Tests for Control Plane account-setup wizard page rendering."""

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


def test_wizard_page_contains_auto_approved_confirmation_branch() -> None:
    html = dashboard_app.render_wizard_page()
    assert "Request Approved" in html
    assert "Add DSS Identity to your wallet" in html
    assert "/verified-id?principal_did=" in html


def test_wizard_page_keeps_awaiting_approval_branch() -> None:
    html = dashboard_app.render_wizard_page()
    assert "Request Submitted" in html
    assert "awaiting operator approval" in html

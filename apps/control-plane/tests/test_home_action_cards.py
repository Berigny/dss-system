"""Tests for Control Plane home-page quick-entry action cards."""

import importlib.util
import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("dss_dashboard_app", REPO_ROOT / "app.py")
dashboard_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard_app)


def test_home_page_renders_chat_and_decode_cards() -> None:
    html = dashboard_app.render_home_page({})
    assert dashboard_app.CHAT_BASE_URL in html
    assert dashboard_app.COORD_DEMO_BASE_URL in html
    assert "Open Chat" in html
    assert "Open Decode" in html


def test_home_page_renders_telegram_coming_soon_when_no_url() -> None:
    html = dashboard_app.render_home_page({})
    assert "Telegram" in html
    assert "Coming soon" in html


def test_render_action_cards_supports_telegram_url(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_app, "TELEGRAM_BASE_URL", "https://t.me/dssbot")
    html = dashboard_app.render_action_cards(
        chat_url="https://chat.dualsubstrate.com",
        decode_url="https://coord-demo.vercel.app",
        telegram_url="https://t.me/dssbot",
    )
    assert "https://t.me/dssbot" in html
    assert "Open Telegram" in html
    assert "Coming soon" not in html


def test_render_action_cards_disabled_when_chat_url_missing() -> None:
    html = dashboard_app.render_action_cards(
        chat_url="",
        decode_url="https://coord-demo.vercel.app",
        telegram_url=None,
    )
    assert "Chat not configured" in html
    assert "Open Decode" in html

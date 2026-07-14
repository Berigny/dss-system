"""Tests for collapsing duplicate chat surface entries in the Control Plane."""

import app as app_module


def test_dedupe_chat_surfaces_keeps_primary_and_drops_duplicate_endpoint():
    primary = {
        "surface_id": "surface:chat:primary",
        "name": "Chat Surface",
        "endpoint": "https://chat.dualsubstrate.com",
    }
    duplicate = {
        "surface_id": "surface:chat:linked-runtime",
        "name": "Linked Chat",
        "endpoint": "https://chat.dualsubstrate.com?principal_did=did:key:abc",
    }
    other = {
        "surface_id": "surface:telegram:bot",
        "name": "Telegram Bot",
        "endpoint": "https://t.me/examplebot",
    }
    result = app_module._dedupe_chat_surfaces([primary, duplicate, other])
    ids = {r["surface_id"] for r in result}
    assert ids == {"surface:chat:primary", "surface:telegram:bot"}


def test_dedupe_chat_surfaces_keeps_all_when_no_primary_configured():
    original_chat_base = app_module.CHAT_BASE_URL
    try:
        app_module.CHAT_BASE_URL = ""
        records = [
            {"surface_id": "surface:chat:one", "endpoint": "https://chat.dualsubstrate.com"},
            {"surface_id": "surface:chat:two", "endpoint": "https://chat.dualsubstrate.com"},
        ]
        result = app_module._dedupe_chat_surfaces(records)
        assert len(result) == 2
    finally:
        app_module.CHAT_BASE_URL = original_chat_base

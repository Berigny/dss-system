"""Smoke tests for the Telegram surface API contract."""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("telegram_api", REPO_ROOT / "backend" / "api" / "telegram.py")
telegram_api = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(telegram_api)


def test_router_has_telegram_routes() -> None:
    routes = [route.path for route in telegram_api.router.routes]
    assert "/v1/telegram/webhook" in routes
    assert "/v1/telegram/pairing-code" in routes


def _fake_request(*, headers: dict | None = None, db: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = headers or {}
    request.app.state.db = db if db is not None else {}
    request.state = MagicMock()
    return request


@pytest.fixture
def fresh_db() -> dict:
    return {}


def test_pairing_code_requires_admin_secret(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_ADMIN_SECRET = "admin-secret"
    request = _fake_request(headers={}, db=fresh_db)
    with pytest.raises(telegram_api.HTTPException) as exc_info:
        import asyncio
        asyncio.run(telegram_api.create_pairing_code(request, telegram_api.PairingCodeRequest(principal_did="did:web:test")))
    assert exc_info.value.status_code == 401


def test_pairing_code_minted_with_valid_secret(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_ADMIN_SECRET = "admin-secret"
    telegram_api._pairing_codes.clear()
    request = _fake_request(headers={"x-telegram-admin-secret": "admin-secret"}, db=fresh_db)
    import asyncio
    result = asyncio.run(
        telegram_api.create_pairing_code(request, telegram_api.PairingCodeRequest(principal_did="did:web:test"))
    )
    assert "code" in result
    assert result["expires_at"] > 0
    assert result["code"] in telegram_api._pairing_codes


def test_webhook_rejects_invalid_secret(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_WEBHOOK_SECRET = "webhook-secret"
    request = _fake_request(headers={"x-telegram-bot-api-secret-token": "wrong"}, db=fresh_db)
    import asyncio
    with pytest.raises(telegram_api.HTTPException) as exc_info:
        asyncio.run(telegram_api.telegram_webhook(request, telegram_api.TelegramUpdate(update_id=1)))
    assert exc_info.value.status_code == 401


def test_webhook_ignores_unpaired_chat(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_WEBHOOK_SECRET = ""
    telegram_api.TELEGRAM_BOT_TOKEN = ""
    request = _fake_request(db=fresh_db)
    update = telegram_api.TelegramUpdate(
        update_id=2,
        message={"chat": {"id": 123}, "text": "hello"},
    )
    import asyncio
    result = asyncio.run(telegram_api.telegram_webhook(request, update))
    assert result == {"ok": True}


def test_webhook_pairs_chat_on_start_code(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_WEBHOOK_SECRET = ""
    telegram_api.TELEGRAM_BOT_TOKEN = ""
    telegram_api._pairing_codes.clear()
    code = "valid-code"
    telegram_api._pairing_codes[code] = {
        "principal_did": "did:web:test",
        "expires_at": 9999999999,
    }
    request = _fake_request(db=fresh_db)
    update = telegram_api.TelegramUpdate(
        update_id=3,
        message={"chat": {"id": 456}, "text": f"/start {code}"},
    )

    with patch.object(telegram_api, "_send_telegram_message", new=AsyncMock()) as mock_send:
        import asyncio
        result = asyncio.run(telegram_api.telegram_webhook(request, update))

    assert result == {"ok": True}
    mock_send.assert_awaited_once()
    bindings = telegram_api._load_bindings(fresh_db)
    assert bindings["456"] == "did:web:test"


def test_webhook_deduplicates_update_id(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_WEBHOOK_SECRET = ""
    telegram_api.TELEGRAM_BOT_TOKEN = ""
    request = _fake_request(db=fresh_db)
    update = telegram_api.TelegramUpdate(
        update_id=4,
        message={"chat": {"id": 789}, "text": "hello"},
    )
    # Pre-populate update_id.
    fresh_db[telegram_api._TELEGRAM_UPDATE_IDS_KEY] = b'{"4": 9999999999}'

    import asyncio
    result = asyncio.run(telegram_api.telegram_webhook(request, update))
    assert result == {"ok": True}


def test_webhook_sends_abstention_verbatim(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_WEBHOOK_SECRET = ""
    telegram_api.TELEGRAM_BOT_TOKEN = ""
    principal = "did:web:test"
    fresh_db[telegram_api._TELEGRAM_BINDINGS_KEY] = json.dumps({"999": principal}).encode()

    request = _fake_request(db=fresh_db)
    update = telegram_api.TelegramUpdate(
        update_id=5,
        message={"chat": {"id": 999}, "text": "hello"},
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            raise telegram_api.HTTPException(status_code=422, detail={"detail": "I cannot answer that.", "abstention": True})

    with patch.object(telegram_api, "mint_surface_session_bundle", return_value={"session": {"token": "fake-token"}}):
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResponse())):
            with patch.object(telegram_api, "_send_telegram_message", new=AsyncMock()) as mock_send:
                import asyncio
                result = asyncio.run(telegram_api.telegram_webhook(request, update))

    assert result == {"ok": True}
    sent_text = mock_send.await_args[0][1]
    assert "I cannot answer that." in sent_text


def test_send_telegram_message_no_token_logs_warning(fresh_db: dict) -> None:
    telegram_api.TELEGRAM_BOT_TOKEN = ""
    import asyncio
    result = asyncio.run(telegram_api._send_telegram_message(123, "test"))
    assert result is None

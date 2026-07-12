"""Ledger helpers for retrieving and persisting chat threads."""

from __future__ import annotations

from typing import Dict, List

from api.client import api


async def persist_turns(
    entity: str,
    user_message: str,
    assistant_reply: str,
    metadata: Dict | None = None,
    ledger_id: str | None = None,
) -> None:
    """Persist the latest chat turns to the ledger, ignoring failures."""

    if ledger_id:
        api.set_ledger(ledger_id)

    try:
        await api.enrich(entity=entity, role="user", content=user_message, kind="message")
        await api.enrich_guardian(
            entity=entity,
            user_message=user_message,
            assistant_reply=assistant_reply,
        )
    except Exception:
        # Ledger writes should not block chat responses if unavailable
        return

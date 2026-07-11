"""Helpers for building consistent stats payloads."""

from __future__ import annotations

from starlette.requests import Request

from api.client import api
from config.settings import DEFAULT_SESSION_ID, settings
from utils.session import get_session


async def build_stats_payload(request: Request) -> dict:
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)

    if ledger_id:
        api.set_ledger(ledger_id)

    try:
        stats_data = await api.session_stats(session_id)
    except Exception:
        stats_data = {}

    if not isinstance(stats_data, dict):
        stats_data = {}

    totals = stats_data.get("totals", {})
    metrics = stats_data.get("metrics", {})

    def _has_signal(row: dict) -> bool:
        if not isinstance(row, dict):
            return False
        for key in ("events", "chat_turns", "resolve_successes", "emitted_refs"):
            value = row.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                return True
        return False

    if not isinstance(totals, dict):
        totals = {}

    if not isinstance(metrics, dict):
        metrics = {}

    # Local/dev sessions can rotate ids quickly; if session stats are empty,
    # fall back to global aggregates instead of returning hard-zero tiles.
    if not _has_signal(totals):
        try:
            global_data = await api.global_stats()
        except Exception:
            global_data = {}
        if isinstance(global_data, dict):
            g_totals = global_data.get("totals")
            g_metrics = global_data.get("metrics")
            if isinstance(g_totals, dict) and _has_signal(g_totals):
                totals = g_totals
                if isinstance(g_metrics, dict):
                    metrics = g_metrics

    total_cost = float(totals.get("cost", 0.0))
    event_count = int(totals.get("events", 0))
    chat_turns = int(totals.get("chat_turns", 0))
    chat_cost = float(totals.get("chat_cost", 0.0))
    chat_resolve_successes = int(totals.get("chat_resolve_successes", 0))

    chat_unit_cost = 0.0
    if chat_turns > 0:
        chat_unit_cost = chat_cost / chat_turns
    elif event_count > 0:
        chat_unit_cost = total_cost / event_count

    memory_unit_cost = float(
        metrics.get("chat_cost_per_1m_tokens")
        or metrics.get("memory_cost_per_1m_tokens")
        or metrics.get("memory_cost_per_10k_words", 0.0)
    )
    retrieval_rate = float(metrics.get("verifiable_response_rate", 0.0))
    resolved_per_turn = float(metrics.get("resolved_coords_per_turn", 0.0))
    if resolved_per_turn == 0.0 and chat_turns > 0:
        resolved_per_turn = chat_resolve_successes / chat_turns

    return {
        "chat_unit_cost": chat_unit_cost,
        "memory_unit_cost": memory_unit_cost,
        "retrieval_rate": retrieval_rate,
        "resolved_per_turn": resolved_per_turn,
        "accuracy_numerator": int(totals.get("resolve_successes", 0)),
        "accuracy_denominator": int(totals.get("emitted_refs", 0)),
        "totals": totals,
        "metrics": metrics,
    }

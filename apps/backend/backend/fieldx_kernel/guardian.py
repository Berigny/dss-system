"""Guardian agent that structures ledger knowledge after writes."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from openai.types.chat import ChatCompletionMessageParam

from backend.fieldx_kernel.kernel_origin_equations import (
    equation_6_consciousness_with_hysteresis,
    equation_6_operational,
    equation_7_coherence_mandate_with_hysteresis,
    equation_9_teleology,
    solve_ethics,
)
from backend.fieldx_kernel.kernel_divination import compute_configurational_foresight
from backend.fieldx_kernel.ledger import allow_mediator_writes
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.state import LAW_PRIME, GRACE_PRIME
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.fieldx_kernel.temporal import get_entity_engine
from backend.utils.system_prompts import build_system_prompt, load_system_prompts

LOGGER = logging.getLogger(__name__)

GUARDIAN_PROVIDER = os.getenv("GUARDIAN_PROVIDER", "openrouter")
GUARDIAN_MODEL = os.getenv("GUARDIAN_MODEL")
GUARDIAN_ENABLED = os.getenv("GUARDIAN_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
CONSOLIDATION_INTERVAL = 17  # Periodic enrichment trigger
GUARDIAN_MAX_TOKENS = int(os.getenv("GUARDIAN_MAX_TOKENS", "256"))
GUARDIAN_REASONING_MAX_CHARS = int(os.getenv("GUARDIAN_REASONING_MAX_CHARS", "320"))
GUARDIAN_INTRO_PROMPT = os.getenv("GUARDIAN_INTRO_PROMPT", "").strip()

GUARDIAN_SESSION_STATE: dict[str, dict[str, object]] = {}
GUARDIAN_PROMPT_STATE: dict[str, dict[str, object]] = {}

class GuardianOutput(BaseModel):
    summary: str = Field(default="", description="Canonical summary.")
    topics: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    eq6_commit_allowed: bool | None = None
    eq6_lawfulness_level: int | None = None
    eq6_mediator_prime: int | None = None
    appraisal_reasoning: str = Field(
        default="",
        description="Optional rationale for Law/Grace/Teleology scoring.",
    )
    appraisal: dict[str, float] = Field(default_factory=dict)
    teleology_alignment: float = 0.0
    maintenance_request: Literal["none", "reindex", "prune_context"] = "none"
    walk_assessment: dict[str, float] = Field(default_factory=dict)
    walk_rollup: dict[str, object] = Field(default_factory=dict)
    walk_recommendations: list[str] = Field(default_factory=list)
    walk_maintenance_request: dict[str, object] | None = None
    configurational_foresight: dict[str, object] = Field(default_factory=dict)

@dataclass(frozen=True)
class GuardianResult:
    payload: GuardianOutput
    summary_prime: int | None

# --- SAFETY HELPER: Prevents OOM by capping context size ---
def _truncate_state(data: Any, max_items: int = 15, max_str_len: int = 1000) -> Any:
    """Recursively truncate massive state objects to fit context window."""
    if isinstance(data, dict):
        items = list(data.items())
        if len(items) > max_items:
            items = items[-max_items:]
        return {k: _truncate_state(v, max_items, max_str_len) for k, v in items}
    if isinstance(data, list):
        # Keep only the last N items (most recent context)
        sliced = data[-max_items:] if len(data) > max_items else data
        return [_truncate_state(i, max_items, max_str_len) for i in sliced]
    if isinstance(data, str):
        if len(data) > max_str_len:
            return data[:max_str_len] + "...[TRUNCATED]"
        return data
    return data


def _load_recent_walks(store, namespace: str, limit: int = 20) -> list[dict[str, Any]]:
    if not store:
        return []
    try:
        entries = store.list_by_namespace(namespace, limit=limit)
    except Exception:
        return []
    walks: list[dict[str, Any]] = []
    for entry in entries:
        try:
            identifier = entry.key.identifier
        except Exception:
            identifier = ""
        if not isinstance(identifier, str) or not identifier.startswith("EV-WALK-"):
            continue
        metadata = entry.state.metadata or {}
        if metadata.get("kind") != "coord_walk":
            continue
        walks.append(metadata)
    return walks


def _walk_rollup(walks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(walks)
    path_lengths = []
    hop_lawfulness_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    hop_total = 0
    termination_low = 0
    hop_coords: list[str] = []

    for walk in walks:
        path = walk.get("path") or []
        if isinstance(path, list):
            path_lengths.append(len(path))
            for coord in path[1:]:
                if isinstance(coord, str):
                    hop_coords.append(coord)
        lawfulness = walk.get("hop_lawfulness") or []
        if isinstance(lawfulness, list):
            for item in lawfulness:
                if isinstance(item, int) and item in hop_lawfulness_counts:
                    hop_lawfulness_counts[item] += 1
                    hop_total += 1
        reason = walk.get("termination_reason")
        if reason in {"low_score", "blocked"}:
            termination_low += 1

    avg_path_len = float(sum(path_lengths)) / float(count) if count else 0.0
    lawfulness_rate = {
        "L3": (hop_lawfulness_counts[3] / hop_total) if hop_total else 0.0,
        "L2": (hop_lawfulness_counts[2] / hop_total) if hop_total else 0.0,
        "L1": (hop_lawfulness_counts[1] / hop_total) if hop_total else 0.0,
        "L0": (hop_lawfulness_counts[0] / hop_total) if hop_total else 0.0,
    }
    unlawful_rate = lawfulness_rate["L0"]
    low_score_rate = (termination_low / count) if count else 0.0

    top10_coverage = 0.0
    if hop_coords:
        counts: dict[str, int] = {}
        for coord in hop_coords:
            counts[coord] = counts.get(coord, 0) + 1
        top10 = sorted(counts.values(), reverse=True)[:10]
        total_hops = len(hop_coords)
        top10_coverage = float(sum(top10)) / float(total_hops) if total_hops else 0.0

    return {
        "count": count,
        "avg_path_len": round(avg_path_len, 3),
        "lawfulness_rate": lawfulness_rate,
        "unlawful_rate": round(unlawful_rate, 4),
        "low_score_rate": round(low_score_rate, 4),
        "top10_coverage": round(top10_coverage, 4),
    }


def _score_inverse(rate: float, threshold: float) -> float:
    if threshold <= 0:
        return 1.0
    value = 1.0 - (rate / threshold)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _walk_assessment(rollup: Mapping[str, Any]) -> tuple[dict[str, float], list[str], dict[str, object] | None]:
    unlawful_rate = float(rollup.get("unlawful_rate", 0.0) or 0.0)
    low_score_rate = float(rollup.get("low_score_rate", 0.0) or 0.0)
    top10_coverage = float(rollup.get("top10_coverage", 0.0) or 0.0)

    topology_health = _score_inverse(unlawful_rate, 0.05)
    stability = _score_inverse(low_score_rate, 0.30)
    diversity = _score_inverse(max(0.0, top10_coverage - 0.70), 0.30) if top10_coverage > 0.70 else 1.0

    recommendations: list[str] = []
    maintenance: dict[str, object] | None = None
    if topology_health < 0.5:
        recommendations.append("set_mediator=LAW")
    if stability < 0.5:
        recommendations.append("reduce_max_steps")
    if diversity < 0.5:
        recommendations.append("request_reindex_overlay")
        maintenance = {
            "type": "reindex_overlay",
            "reason": "walk diversity collapsing",
            "suggested_scope": "namespace",
        }

    return (
        {
            "topology_health": round(topology_health, 4),
            "stability": round(stability, 4),
            "diversity": round(diversity, 4),
        },
        recommendations,
        maintenance,
    )

def _parse_guardian_json(text: str) -> GuardianOutput | None:
    try:
        # cleanup markdown fences if present
        clean = text.strip().replace("```json", "").replace("```", "")
        # Remove potential leading/trailing non-json text
        if "{" in clean and "}" in clean:
            start = clean.find("{")
            end = clean.rfind("}") + 1
            clean = clean[start:end]
            
        data = json.loads(clean)
        parsed = GuardianOutput.model_validate(data)
        if parsed.appraisal_reasoning and len(parsed.appraisal_reasoning) > GUARDIAN_REASONING_MAX_CHARS:
            parsed.appraisal_reasoning = (
                parsed.appraisal_reasoning[:GUARDIAN_REASONING_MAX_CHARS].rstrip()
            )
        return parsed
    except (json.JSONDecodeError, ValidationError):
        return None


def _get_guardian_intro(entity: str) -> str | None:
    if not GUARDIAN_INTRO_PROMPT:
        return None
    state = GUARDIAN_SESSION_STATE.setdefault(entity, {})
    if state.get("intro_seen"):
        return None
    state["intro_seen"] = True
    return GUARDIAN_INTRO_PROMPT


def _should_include_guardian_prompts(
    entity: str,
    turn_count: int,
    interval: int = 7,
) -> bool:
    state = GUARDIAN_PROMPT_STATE.setdefault(
        entity,
        {
            "provider": GUARDIAN_PROVIDER,
            "model": GUARDIAN_MODEL,
            "last_prompt_turn": 0,
        },
    )
    provider_changed = state.get("provider") != GUARDIAN_PROVIDER
    model_changed = state.get("model") != GUARDIAN_MODEL
    if provider_changed or model_changed or turn_count <= 1:
        state["provider"] = GUARDIAN_PROVIDER
        state["model"] = GUARDIAN_MODEL
        state["last_prompt_turn"] = turn_count
        return True
    raw_last_prompt = state.get("last_prompt_turn", 0)
    last_prompt_turn = raw_last_prompt if isinstance(raw_last_prompt, int) else 0
    if turn_count - last_prompt_turn >= interval:
        state["last_prompt_turn"] = turn_count
        return True
    return False

def _build_guardian_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    s1_state: Mapping[str, Any],
    s2_state: Mapping[str, Any],
    intro_message: str | None = None,
    include_system_prompts: bool = True,
    walk_context: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    intro_block = ""
    if intro_message:
        intro_block = f"--- SYSTEM OVERVIEW ---\n{intro_message}\n\n"
    prompts = load_system_prompts()
    guardian_prompt = (prompts.get("guardian") or "").strip()
    if not guardian_prompt:
        guardian_prompt = (
            "You are the Ledger Guardian. Analyze the conversation and produce a JSON update.\n"
            "Output ONLY valid JSON matching this schema:\n"
            "{"
            "\"summary\": string,"
            "\"topics\": string[],"
            "\"claims\": string[],"
            "\"links\": string[],"
            "\"appraisal\": {\"score\": number, \"drift\": number, \"law_score\": number, \"grace_score\": number},"
            "\"teleology_alignment\": number,"
            "\"maintenance_request\": \"none\" | \"reindex\" | \"prune_context\""
            "}\n"
            "Alignment criteria:\n"
            "1) LAW (137): respects constraints, logic, and prior facts.\n"
            "2) GRACE (139): adds novelty, expansion, or connection.\n"
            "3) TELEOLOGY: moves toward coherence K=1 and minimizes drift.\n"
            f"Include 'appraisal_reasoning' with a brief explanation (max {GUARDIAN_REASONING_MAX_CHARS} chars).\n"
            "Evaluate 'teleology_alignment' (0.0-1.0) using these criteria."
        )
    reminder = (prompts.get("guardian_reminder") or "").strip()
    if include_system_prompts:
        system_prompt = build_system_prompt(
            guardian_prompt,
            "guardian",
            include_role=True,
            include_global=True,
        )
    else:
        system_prompt = guardian_prompt
        if reminder:
            system_prompt = f"{reminder}\n\n{system_prompt}"
    if intro_block:
        system_prompt = f"{intro_block.rstrip()}\n\n{system_prompt}"

    # Apply strict truncation to prevent OOM
    context = {
        "user_message": user_message[:2000],
        "assistant_reply": assistant_reply[:2000],
        "s1_state": _truncate_state(s1_state),
        "s2_state": _truncate_state(s2_state),
    }
    if walk_context:
        context["walk_context"] = _truncate_state(walk_context, max_items=20, max_str_len=500)
    
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
    ]

async def _call_guardian(messages: Sequence[Mapping[str, str]]) -> GuardianOutput | None:
    # IMPORT INSIDE FUNCTION TO PREVENT CIRCULAR DEPENDENCY
    from backend.fieldx_kernel.orchestrator import complete_chat

    chat_messages = cast(Sequence[ChatCompletionMessageParam], list(messages))
    try:
        text, _, _, _, _ = await complete_chat(
            provider=GUARDIAN_PROVIDER,
            messages=chat_messages,
            model=GUARDIAN_MODEL,
            max_tokens=GUARDIAN_MAX_TOKENS,
            log_incomplete=True,
            log_label="guardian",
        )
        return _parse_guardian_json(text)
    except Exception as exc:
        LOGGER.warning("Guardian model call failed: %s", exc)
        return None

def _build_psi_from_memory(values: list[float]) -> np.ndarray:
    if not values:
        return np.array([1.0 + 0j])
    vec = np.array(values[-8:], dtype=float)
    norm = float(np.linalg.norm(vec)) if vec.size else 1.0
    if norm == 0.0:
        vec = np.array([1.0], dtype=float)
        norm = 1.0
    return (vec / norm).astype(complex)


def _apply_hysteresis_metrics(entity: str, ethics_val: float) -> tuple[float, float]:
    engine = get_entity_engine(entity)
    engine.update_memory(ethics_val)
    temporal_state = getattr(engine, "temporal_state", 0)
    engine.temporal_state = engine.equation_2_temporalization(temporal_state)
    c_un_con = equation_6_consciousness_with_hysteresis(engine)
    psi = _build_psi_from_memory(engine.memory_buffer)
    k_unity = equation_7_coherence_mandate_with_hysteresis(psi, engine)
    return c_un_con, k_unity


def _calculate_local_teleology(entity: str) -> GuardianOutput:
    """Fast-path calculation using Genesis Equations only (No LLM)."""
    # Solve for the "Ideal" Ethics state (North Star)
    ethics = solve_ethics()
    
    # Extract scalar values
    ethics_val = float(ethics.get("Ethics_Value", 0.0))
    law_val = float(ethics.get("Law_Score", 0.0))
    grace_val = float(ethics.get("Grace_Score", 0.0))
    drift_val = float(ethics.get("Drift", 0.0))

    # Eq 9: E_ALL = C * K * E
    # For the fast path, we assume Unity (K=1) and Consciousness (C=1) are locally maintained
    c_un_con, k_unity = _apply_hysteresis_metrics(entity, ethics_val)
    alignment = equation_9_teleology(c_un_con, k_unity, ethics_val)

    return GuardianOutput(
        summary="",  # No summary on fast path
        appraisal={
            "score": ethics_val,
            "law_score": law_val,
            "grace_score": grace_val,
            "drift": drift_val,
        },
        teleology_alignment=alignment,
    )


def _apply_teleology_to_latest_entry(
    store: LedgerStoreV2 | None,
    *,
    entity: str,
    teleology_alignment: float,
    configurational_foresight: Mapping[str, Any] | None = None,
) -> None:
    if not store:
        return
    try:
        latest_entries = store.list_by_namespace(entity, limit=1)
    except Exception:
        LOGGER.exception("Failed to load latest entry for teleology update")
        return
    if not latest_entries:
        return
    entry = latest_entries[0]
    related_coord = entry.key.as_path()
    metadata: dict[str, Any] = {
        "kind": "teleology_update",
        "related_coord": related_coord,
        "related_identifier": entry.key.identifier,
        "teleology_alignment": float(teleology_alignment),
        "auth_score": float(teleology_alignment),
        "quality_tier": "express" if teleology_alignment >= 0.9 else "probe" if teleology_alignment <= 0.3 else "stabilise",
        "persistence_mode": "sidecar",
        "gravity_penalty": 0.0,
    }
    if isinstance(configurational_foresight, Mapping) and configurational_foresight:
        metadata["configurational_foresight"] = dict(configurational_foresight)
    identifier = f"TEL-{entry.key.identifier}-{int(time.time() * 1000)}"
    sidecar_entry = LedgerEntry(
        LedgerKey(namespace=entry.key.namespace, identifier=identifier),
        ContinuousState({}, "teleology", metadata),
    )
    try:
        store.write(sidecar_entry)
    except Exception:
        LOGGER.exception("Failed to write teleology sidecar entry")

async def guardian_enrich_turn(
    *,
    entity: str,
    user_message: str,
    assistant_reply: str,
    retrieval_payload: Mapping[str, Any] | list[Any] | None = None,
    draft_text: str | None = None,
    eq6_commit_allowed: bool | None = None,
    eq6_lawfulness_level: int | None = None,
    eq6_mediator_prime: int | None = None,
    ledger,
    substrate,
    store=None,
    dry_run: bool = False,
) -> GuardianResult | None:
    if not GUARDIAN_ENABLED:
        return None
    
    should_persist = not dry_run

    walk_context: dict[str, Any] = {}
    rollup: dict[str, Any] = {}
    assessment: dict[str, float] = {}
    recommendations: list[str] = []
    maintenance: dict[str, object] | None = None
    if store and isinstance(entity, str):
        recent_walks = _load_recent_walks(store, entity, limit=20)
        rollup = _walk_rollup(recent_walks)
        assessment, recommendations, maintenance = _walk_assessment(rollup)
        walk_context = {
            "recent_walks": recent_walks,
            "walk_rollup": rollup,
        }

    if retrieval_payload is not None and draft_text:
        eq6_result = equation_6_operational(
            query_text=draft_text,
            retrieval_payload=retrieval_payload,
            lawfulness_level=eq6_lawfulness_level,
            mediator_prime=eq6_mediator_prime,
        )
        eq6_commit_allowed = bool(eq6_result.get("commit_allowed"))
        eq6_lawfulness_level = int(eq6_result.get("lawfulness_level") or 0)
        eq6_mediator_prime = int(eq6_result.get("mediator_prime") or 0)
        logger = LOGGER
        logger.info(
            f"Eq6 gate: commit={eq6_commit_allowed} law={eq6_lawfulness_level} mediator={eq6_mediator_prime}"
        )

    # 1. Determine Turn Count for Periodicity
    turn_count = 0
    if store:
        summary = store.summarize(entity)
        turn_count = summary.get("total_entries", 0)

    # 2. Decide: Fast Path vs. Slow Path
    # Trigger Heavy Enrichment only on the 17th turn (or very first turn for init)
    is_periodic_trigger = (
        not dry_run
        and turn_count > 0
        and (turn_count == 1 or turn_count % CONSOLIDATION_INTERVAL == 0)
    )
    
    if not is_periodic_trigger:
        # --- FAST PATH (Metrics Only) ---
        # Returns immediately using Equation 9, saving memory and time
        light_output = _calculate_local_teleology(entity)
        light_output.walk_rollup = rollup
        light_output.walk_assessment = assessment
        light_output.walk_recommendations = recommendations
        light_output.walk_maintenance_request = maintenance
        light_output.eq6_commit_allowed = eq6_commit_allowed
        light_output.eq6_lawfulness_level = eq6_lawfulness_level
        light_output.eq6_mediator_prime = eq6_mediator_prime
        light_appraisal = light_output.appraisal if isinstance(light_output.appraisal, Mapping) else {}
        light_output.configurational_foresight = compute_configurational_foresight(
            teleology_alignment=light_output.teleology_alignment,
            law_score=float(light_appraisal.get("law_score", 0.5) or 0.5),
            grace_score=float(light_appraisal.get("grace_score", 0.5) or 0.5),
            drift=float(light_appraisal.get("drift", 0.0) or 0.0),
            walk_assessment=assessment,
        )
        
        # Persist metrics to mediators so Researcher UI sees them
        if ledger and should_persist:
            mediator_updates = {
                str(LAW_PRIME): {"metadata": {"guardian_appraisal": light_output.appraisal}},
                str(GRACE_PRIME): {"metadata": {"guardian_appraisal": light_output.appraisal}},
            }
            with allow_mediator_writes():
                ledger.update_mediators(entity, mediator_updates)

        if should_persist:
            _apply_teleology_to_latest_entry(
                cast(LedgerStoreV2 | None, store),
                entity=entity,
                teleology_alignment=light_output.teleology_alignment,
                configurational_foresight=light_output.configurational_foresight,
            )
                
        return GuardianResult(payload=light_output, summary_prime=None)

    # --- SLOW PATH (Heavy Enrichment) ---
    LOGGER.info(
        "Guardian slow-path consolidation triggered (entity=%s, turn_count=%s)",
        entity,
        turn_count,
    )
    
    s1_state = ledger.get_S1(entity) if ledger else {}
    s2_state = ledger.get_S2(entity) if ledger else {}

    intro_message = _get_guardian_intro(entity)
    include_system_prompts = _should_include_guardian_prompts(entity, turn_count)
    messages = _build_guardian_prompt(
        user_message=user_message,
        assistant_reply=assistant_reply,
        s1_state=s1_state,
        s2_state=s2_state,
        intro_message=intro_message,
        include_system_prompts=include_system_prompts,
        walk_context=walk_context or None,
    )
    
    output = await _call_guardian(messages)
    if output is None:
        # Fallback to local metrics if LLM fails/times out
        output = _calculate_local_teleology(entity)

    # Ensure metrics are present even if LLM output them poorly
    local_metrics = _calculate_local_teleology(entity)
    if output.teleology_alignment == 0.0:
        output.teleology_alignment = local_metrics.teleology_alignment
    if not output.appraisal:
        output.appraisal = local_metrics.appraisal
    output.walk_rollup = rollup
    output.walk_assessment = assessment
    output.walk_recommendations = recommendations
    output.walk_maintenance_request = maintenance
    output.eq6_commit_allowed = eq6_commit_allowed
    output.eq6_lawfulness_level = eq6_lawfulness_level
    output.eq6_mediator_prime = eq6_mediator_prime
    appraisal = output.appraisal if isinstance(output.appraisal, Mapping) else {}
    output.configurational_foresight = compute_configurational_foresight(
        teleology_alignment=output.teleology_alignment,
        law_score=float(appraisal.get("law_score", 0.5) or 0.5),
        grace_score=float(appraisal.get("grace_score", 0.5) or 0.5),
        drift=float(appraisal.get("drift", 0.0) or 0.0),
        walk_assessment=assessment,
    )

    # 3. Write Summary to Substrate (Immutable)
    summary_prime = None
    if substrate and output.summary and should_persist:
        summary_prime = substrate.allocate_body_prime(entity)
        substrate.write_body_prime(
            entity,
            summary_prime,
            output.summary,
            {"kind": "guardian_summary", "source": "guardian", "turn": turn_count},
        )

    # 4. Update Ledger State (Mutable)
    if ledger and should_persist:
        updates = {
            "11": {
                "summary_ref": summary_prime,
                "metadata": {
                    "guardian_summary": output.summary,
                    "teleology_alignment": output.teleology_alignment,
                    "configurational_foresight": output.configurational_foresight,
                },
            },
            "13": {"taxonomy": output.topics},
            "17": {"linkmap": output.links},
            "19": {"claims": output.claims},
        }
        ledger.replace_S2(entity, updates)

        mediator_updates = {
            str(LAW_PRIME): {"metadata": {"guardian_appraisal": output.appraisal}},
            str(GRACE_PRIME): {"metadata": {"guardian_appraisal": output.appraisal}},
        }
        with allow_mediator_writes():
            ledger.update_mediators(entity, mediator_updates)

    if should_persist:
        _apply_teleology_to_latest_entry(
            cast(LedgerStoreV2 | None, store),
            entity=entity,
            teleology_alignment=output.teleology_alignment,
            configurational_foresight=output.configurational_foresight,
        )

    return GuardianResult(payload=output, summary_prime=summary_prime)

async def guardian_consolidate(
    *,
    entity: str,
    entries: Iterable[LedgerEntry],
    ledger,
    substrate,
) -> GuardianResult | None:
    if not GUARDIAN_ENABLED:
        return None

    # Limit consolidation input to prevent OOM
    entries_list = list(entries)
    if len(entries_list) > 20:
        entries_list = entries_list[:20]

    combined = "\n\n".join(
        entry.notes or entry.state.metadata.get("content", "")
        for entry in entries_list
        if entry is not None
    )
    if not combined:
        return None

    return await guardian_enrich_turn(
        entity=entity,
        user_message="Consolidate recent knowledge.",
        assistant_reply=combined,
        ledger=ledger,
        substrate=substrate,
    )

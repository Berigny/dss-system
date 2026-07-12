"""Fuzzy retrieval with Retrocausal (Teleological) alignment.

DS-REVIEW-193 P2-04 adds a pure Qp retrieval branch: when
``settings.QP_PURE_ENABLED`` is true, candidates are ranked by genuine
ultrametric ``qp_distance`` on ``QpElement`` rational representatives.
The legacy mixed-signal path remains available when the flag is false.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence, List, cast

import numpy as np
from openai import OpenAI

from backend.config import settings as _settings
from backend.config.settings import qp_pure_enabled
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import qp_coordinate_distance
from backend.fieldx_kernel.qp_retrieval import (
    derive_query_coordinate_from_factors,
    extract_qp_coordinate,
    qp_pure_compatible,
)

logger = logging.getLogger(__name__)


# --- CONFIGURATION ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
DEFAULT_SEMANTIC_WEIGHT = 0.45

# THE OMEGA POINT (The Boundary Condition)
# This text defines the "Target Frequency" of the lattice.
# Memories that resonate with this are amplified; those that dissonate are dampened.
OMEGA_DEFINITION = (
    "Equation 9: Awareness Love(s) Life. "
    "The system maintains coherence (K=1). "
    "Ethics is the maximization of Law x Grace. "
    "Existence is active coherence maintenance against entropy."
)
RETROCAUSAL_WEIGHT = 0.2  # How much the "Future" influences the "Present" score
TELEOS_WEIGHT = 0.4
FORESIGHT_WEIGHT = 0.1

QUALITY_TIER_BONUS = {
    "express": 0.2,
    "stabilise": 0.0,
    "probe": -0.15,
    "halt": -0.5,
}


class MemoryService(Protocol):
    """Protocol describing the memory service abstraction used by routers."""

    def get_all_memories(self, entity: str | None = None) -> Sequence[Mapping[str, Any]]:
        ...

    def anchor(self, text: str, entity: str | None = None) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        ...


@dataclass(frozen=True)
class MemoryCandidate:
    """Lightweight wrapper for memory payloads used during ranking."""

    text: str
    factors: Sequence[Mapping[str, Any]]
    payload: Mapping[str, Any]


def _get_embedding_client() -> OpenAI:
    """Return a synchronous OpenAI client."""
    local_base = os.getenv("LLM_BASE_URL")
    local_key = os.getenv("LLM_API_KEY", "")

    if local_base:
        return OpenAI(api_key=local_key, base_url=local_base)

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = None

    if not api_key:
        api_key = os.getenv("OPENROUTER_API_KEY")
        base_url = "https://openrouter.ai/api/v1"

    if not api_key:
        logger.warning("No API key found for embeddings.")

    return OpenAI(api_key=api_key, base_url=base_url)


def _get_embeddings(text_list: List[str]) -> List[List[float]]:
    """Fetch embeddings from the API."""
    if not text_list:
        return []

    client = _get_embedding_client()
    try:
        clean_texts = [t.replace("\n", " ") for t in text_list]
        response = client.embeddings.create(input=clean_texts, model=EMBEDDING_MODEL)
        return [data.embedding for data in response.data]
    except Exception as e:
        logger.error(f"Embedding API call failed: {e}")
        return [[0.0] * EMBEDDING_DIM for _ in text_list]


@lru_cache(maxsize=1)
def _get_omega_vector() -> np.ndarray:
    """
    Retrieve and cache the embedding for the Omega Point (Future Boundary).
    This represents the 'Standing Wave' frequency we check against.
    """
    try:
        vectors = _get_embeddings([OMEGA_DEFINITION])
        if vectors:
            return _normalize_embedding(np.array(vectors[0]))
    except Exception as e:
        logger.warning(f"Failed to load Omega vector: {e}")

    # Return neutral zero vector if fail (no retrocausal effect)
    return np.zeros(EMBEDDING_DIM)


def _resolve_semantic_weight(value: float | None) -> float:
    if value is None:
        env_value = os.getenv("SEMANTIC_WEIGHT")
        try:
            value = float(env_value) if env_value is not None else DEFAULT_SEMANTIC_WEIGHT
        except ValueError:
            value = DEFAULT_SEMANTIC_WEIGHT
    return value


def _normalize_embedding(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec
    return vec / norm


def _semantic_similarity(
    query_vec: np.ndarray, candidate_vec: np.ndarray, *, normalized: bool = True
) -> float:
    if query_vec.size == 0 or candidate_vec.size == 0:
        return 0.0
    if query_vec.shape != candidate_vec.shape:
        return 0.0
    if not normalized:
        query_vec = _normalize_embedding(query_vec)
        candidate_vec = _normalize_embedding(candidate_vec)
    return float(np.dot(query_vec, candidate_vec))


def _extract_factors(value: Any) -> Sequence[Mapping[str, Any]]:
    if not value:
        return []
    if isinstance(value, Mapping):
        factors = value.get("factors")
        if isinstance(factors, Sequence):
            return [f for f in factors if isinstance(f, Mapping)]
    if isinstance(value, Sequence):
        return [f for f in value if isinstance(f, Mapping)]
    return []


def _extract_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _resolve_coord(payload: Mapping[str, Any]) -> str | None:
    for key in ("coord", "coordinate", "key", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    metadata = _extract_metadata(payload)
    for key in ("coord", "coordinate", "key", "id"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def _build_teleology_map(memories: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    teleology_map: dict[str, float] = {}
    for memory in memories:
        if not isinstance(memory, Mapping):
            continue
        metadata = _extract_metadata(memory)
        kind = metadata.get("kind") or memory.get("kind")
        if kind != "teleology_update":
            continue
        related_coord = metadata.get("related_coord") or metadata.get("related") or memory.get("related_coord")
        if not related_coord:
            continue
        alignment = metadata.get("teleology_alignment")
        try:
            alignment_val = float(alignment)
        except (TypeError, ValueError):
            alignment_val = 0.0
        teleology_map[str(related_coord)] = alignment_val
    return teleology_map


def _configurational_foresight_score(metadata: Mapping[str, Any]) -> float:
    foresight = metadata.get("configurational_foresight")
    if not isinstance(foresight, Mapping):
        return 0.0
    try:
        score = float(foresight.get("advisory_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def p_adic_distance(
    a_factors: Sequence[Mapping[str, Any]],
    b_factors: Sequence[Mapping[str, Any]],
    *,
    max_delta: int = 2,
    min_overlap: int = 1,
) -> tuple[float, int]:
    """Legacy factor-distance wrapper over ``p_adic_distance_for_factors``.

    The ``max_delta`` and ``min_overlap`` parameters are retained for backward
    compatibility with callers that expect the old signature.  The pure Qp path
    uses ``qp_distance`` directly and does not rely on this wrapper.
    """
    from backend.fieldx_kernel.p_adic import p_adic_distance_for_factors

    metric_prime = int(os.getenv("P_ADIC_DISTANCE_PRIME", "5"))
    return p_adic_distance_for_factors(
        a_factors,
        b_factors,
        metric_prime=metric_prime,
        min_overlap=min_overlap,
    )


def _prepare_candidates(memories: Sequence[Mapping[str, Any]]) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for memory in memories:
        text = ""
        if isinstance(memory, Mapping):
            metadata = _extract_metadata(memory)
            kind = metadata.get("kind") or memory.get("kind")
            if kind == "teleology_update":
                continue
            raw = memory.get("text") or memory.get("body") or memory.get("value")
            text = str(raw) if raw is not None else ""
            factors = _extract_factors(memory)
            candidates.append(MemoryCandidate(text=text, factors=factors, payload=memory))
    return candidates


def _extract_query_factors(
    query: str,
    memory_service: MemoryService,
    entity: str | None,
) -> Sequence[Mapping[str, Any]]:
    anchor_payload = getattr(memory_service, "anchor", None)
    if not callable(anchor_payload):
        return []
    try:
        anchored = anchor_payload(query, entity=entity)
    except Exception:
        return []
    if isinstance(anchored, Mapping):
        return _extract_factors(anchored) or _extract_factors(anchored.get("metadata"))
    return []


def _fuzzy_retrieve_qp_pure(
    query: str,
    *,
    entity: str | None = None,
    memory_service: MemoryService,
    top_k: int = 5,
) -> list[Mapping[str, Any]]:
    """Rank memories by genuine ultrametric Qp distance.

    Circulation-depth and mediator-state compatibility filters are applied
    separately from the distance computation.  No semantic or retrocausal
    mixing is performed.
    """
    memories = list(memory_service.get_all_memories(entity))
    candidates = _prepare_candidates(memories)

    query_factors = _extract_query_factors(query, memory_service, entity)
    query_coord = derive_query_coordinate_from_factors(query_factors)
    if query_coord is None:
        query_coord = extract_qp_coordinate(
            getattr(memory_service, "anchor", lambda *_a, **_kw: {})(query, entity=entity)
        )

    def _mark_fallback(result: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        for item in result:
            item["qp_pure_fallback"] = True
        return result

    if query_coord is None:
        logger.debug("Qp-pure retrieval has no query coordinate; falling back to legacy ranker.")
        return _mark_fallback(
            _fuzzy_retrieve_legacy(
                query,
                entity=entity,
                memory_service=memory_service,
                top_k=top_k,
            )
        )

    scored: list[tuple[float, float, Mapping[str, Any]]] = []
    for candidate in candidates:
        candidate_coord = extract_qp_coordinate(candidate.payload)
        if candidate_coord is None:
            continue
        if not qp_pure_compatible(query_coord, candidate_coord):
            continue
        try:
            distance = float(qp_coordinate_distance(query_coord, candidate_coord))
        except Exception as exc:
            logger.debug("Skipping candidate due to Qp distance error: %s", exc)
            continue

        score = float(qp_score(distance, query_coord.metric_prime, query_coord.working_precision))
        enriched = dict(candidate.payload)
        enriched.update({
            "qp_distance": distance,
            "qp_score": score,
            "p_adic_similarity": score,
            "score": score,
            "qp_pure": True,
        })
        scored.append((distance, score, enriched))

    if not scored:
        logger.debug("Qp-pure retrieval found no compatible candidates; falling back to legacy ranker.")
        return _mark_fallback(
            _fuzzy_retrieve_legacy(
                query,
                entity=entity,
                memory_service=memory_service,
                top_k=top_k,
            )
        )

    # Rank by ultrametric distance (ascending); closer is better.
    scored.sort(key=lambda triple: (triple[0], -triple[1]))
    return [payload for _, _, payload in scored[:top_k]]


def _fuzzy_retrieve_legacy(
    query: str,
    *,
    entity: str | None = None,
    memory_service: MemoryService,
    top_k: int = 5,
    semantic_weight: float | None = None,
    max_delta: int = 2,
    min_overlap: int = 1,
) -> list[Mapping[str, Any]]:
    """Mixed-signal retrieval path (semantic + p-adic + retrocausal)."""

    semantic_weight = _resolve_semantic_weight(semantic_weight)

    # 1. Fetch Candidates
    memories = list(memory_service.get_all_memories(entity))
    teleology_map = _build_teleology_map(memories)
    candidates = _prepare_candidates(memories)
    if not candidates:
        return []

    # 2. Prepare Vectors
    query_vec = np.array(_get_embeddings([query])[0])
    zero_dim = int(query_vec.shape[0]) if query_vec.ndim == 1 and query_vec.size > 0 else EMBEDDING_DIM

    # --- RETROCAUSAL STEP ---
    # Load the "Future" Boundary Condition
    omega_vec = _get_omega_vector()
    # ------------------------

    # Prepare Candidate Embeddings
    texts_to_embed = []
    indices_to_update = []
    candidate_vectors: List[np.ndarray | None] = []

    for i, c in enumerate(candidates):
        stored = c.payload.get("embedding") if isinstance(c.payload, Mapping) else None
        if stored is None:
            texts_to_embed.append(c.text)
            indices_to_update.append(i)
            candidate_vectors.append(None)
        else:
            candidate_vectors.append(np.array(stored))

    if texts_to_embed:
        new_embs = _get_embeddings(texts_to_embed)
        for idx, emb in zip(indices_to_update, new_embs):
            candidate_vectors[idx] = np.array(emb)

    # 3. Prepare P-adic Factors
    query_factors = _extract_query_factors(query, memory_service, entity)

    # 4. Rank with Retrocausality
    ranked: list[tuple[float, Mapping[str, Any]]] = []

    for i, candidate in enumerate(candidates):
        candidate_vec = candidate_vectors[i]
        if candidate_vec is None:
            candidate_vec = np.zeros(zero_dim)
        candidate_vec = _normalize_embedding(candidate_vec)

        # A. Grace Score (Present Alignment)
        # Does the memory match the user's current query?
        semantic_sim = _semantic_similarity(query_vec, candidate_vec)

        # B. Law Score (Structural Alignment)
        distance, overlap = p_adic_distance(query_factors, candidate.factors, max_delta=max_delta, min_overlap=min_overlap)
        p_adic_sim = 0.0 if distance == float("inf") else 1.0 / (1.0 + distance)

        # C. Retrocausal Score (Future Alignment)
        # Does this memory resonate with the Omega Point (Equation 9)?
        future_alignment = _semantic_similarity(candidate_vec, omega_vec)

        # D. Unified Score
        # We blend Present (Query) + Future (Omega) + Structure (Primes)

        # Base score (Standard RAG)
        base_score = (semantic_weight * semantic_sim) + ((1.0 - semantic_weight) * p_adic_sim)

        # Apply Retrocausal Filter
        # If the memory opposes the Future (negative alignment), we dampen it.
        # If it resonates (positive alignment), we boost it.
        final_score = base_score + (future_alignment * RETROCAUSAL_WEIGHT)

        metadata = _extract_metadata(candidate.payload)
        teleology_alignment = metadata.get("teleology_alignment")
        if teleology_alignment is None:
            teleology_alignment = metadata.get("auth_score")
        if teleology_alignment is None:
            candidate_coord = _resolve_coord(candidate.payload)
            if candidate_coord:
                teleology_alignment = teleology_map.get(candidate_coord)
        try:
            teleology_alignment = float(teleology_alignment or 0.0)
        except (TypeError, ValueError):
            teleology_alignment = 0.0
        final_score += TELEOS_WEIGHT * teleology_alignment

        foresight_score = _configurational_foresight_score(metadata)
        final_score += FORESIGHT_WEIGHT * foresight_score

        quality_tier = metadata.get("quality_tier") or "stabilise"
        if isinstance(quality_tier, str):
            final_score += QUALITY_TIER_BONUS.get(quality_tier, 0.0)

        gravity_penalty = metadata.get("gravity_penalty")
        if gravity_penalty is None:
            gravity_penalty = metadata.get("gravity_cost")
        try:
            gravity_penalty = float(gravity_penalty or 0.0)
        except (TypeError, ValueError):
            gravity_penalty = 0.0
        final_score = final_score / (1.0 + gravity_penalty)

        enriched = dict(candidate.payload)
        enriched.update({
            "semantic_similarity": semantic_sim,
            "p_adic_similarity": p_adic_sim,
            "future_alignment": future_alignment,  # Visible in debug logs
            "teleology_alignment": teleology_alignment,
            "configurational_foresight_score": foresight_score,
            "quality_tier": quality_tier,
            "gravity_penalty": gravity_penalty,
            "score": final_score,
        })
        ranked.append((final_score, enriched))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [payload for _, payload in ranked[:top_k]]


def fuzzy_retrieve(
    query: str,
    *,
    entity: str | None = None,
    memory_service: MemoryService,
    top_k: int = 5,
    semantic_weight: float | None = None,
    max_delta: int = 2,
    min_overlap: int = 1,
) -> list[Mapping[str, Any]]:
    """
    Retrieve memories.  In pure Qp mode, rank by genuine ultrametric distance;
    otherwise fall back to the legacy mixed-signal ranker.
    """
    if qp_pure_enabled():
        return _fuzzy_retrieve_qp_pure(
            query,
            entity=entity,
            memory_service=memory_service,
            top_k=top_k,
        )
    return _fuzzy_retrieve_legacy(
        query,
        entity=entity,
        memory_service=memory_service,
        top_k=top_k,
        semantic_weight=semantic_weight,
        max_delta=max_delta,
        min_overlap=min_overlap,
    )


__all__ = ["MemoryService", "MemoryCandidate", "fuzzy_retrieve", "p_adic_distance"]

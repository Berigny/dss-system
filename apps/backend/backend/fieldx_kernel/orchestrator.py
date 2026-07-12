"""Fuzzy retrieval with Retrocausal (Teleological) alignment AND Explicit Reference Resolution."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, Mapping, MutableMapping, Protocol, Sequence, List, Literal, cast

import numpy as np
from openai import OpenAI, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

# --- Imports from your backend ---
from backend.api.agent_writes import record_message
from backend.config import settings as _settings
from backend.config.settings import qp_pure_enabled
from backend.fieldx_kernel.guardian import guardian_enrich_turn
from backend.fieldx_kernel.kernel_origin_equations import equation_6_operational
from backend.fieldx_kernel.informational_unit import (
    CIU_ENTRY_CLASS,
    CIU_FACTORS,
    CIU_FLOW_RULE_TAGS,
    CIU_KERNEL_EXPONENTS,
    CIU_MMF_PROJECTIONS,
    CIU_RELATIONSHIP_LINKS,
)
from backend.fieldx_kernel.p_adic import PAdicInteger, p_adic_distance_for_factors
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import qp_coordinate_distance
from backend.fieldx_kernel.qp_retrieval import (
    derive_query_coordinate_from_factors,
    extract_qp_coordinate,
    qp_pure_compatible,
)
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore
from backend.search.token_index import TokenPrimeIndex, normalise_tokens as tokenize_normalise_text
from backend.search.service import search as service_search
from backend.metrics.pricing import estimate_cost_usd
from backend.fieldx_kernel.temporal import get_entity_engine
from backend.utils.knowledge_tree import merge_knowledge_trees, normalize_knowledge_tree_item
from backend.utils.normalise import normalise_text
from backend.utils.coord import namespace_candidates, normalise_coord
from backend.utils.system_prompts import build_system_prompt, load_system_prompts

def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

logger = logging.getLogger(__name__)


# --- CONFIGURATION ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
DEFAULT_SEMANTIC_WEIGHT = 0.45

# --- AGENT CONFIGURATION (GROQ OPTIMIZED) ---
DEFAULT_CHAT_MODEL = "meta-llama/llama-3.1-8b-instruct"
KNOWLEDGE_TREE_LIMIT = int(os.getenv("KNOWLEDGE_TREE_LIMIT", "50"))

# Explicit Coordinate Pattern (supports namespaced coords + parts).
# Backward-compat: accept both canonical two-segment forms
# (e.g. WX-ABCD1234-1772596776 / ATT-deadbeef-1772450375938)
# and legacy one-segment forms (e.g. WX-1772596776159 / ATT-1772450375938).
COORD_PATTERN = re.compile(
    r"(?:([\w-]+(?::[\w-]+)?):)?"
    r"("
    r"(?:WX|ATT)-\d+(?:-(?:T|I|A|V|D|P)\d{3})?"
    r"|"
    r"(?:WX|ATT|PL-Conv|PL-Claim|PL-Taxon|EV|MD-Rule|MD-Run|MD-Reset)"
    r"-[A-Za-z0-9]+-\d+(?:-(?:[A-Za-z0-9]+))*"
    r"(?:-(?:T|I|A|V|D|P)\d{3})?"
    r")"
)
_SYSTEM_ERROR_PATTERN = re.compile(r"\[System Error:[^\]]*\]", re.IGNORECASE)

# THE OMEGA POINT (The Boundary Condition)
OMEGA_DEFINITION = (
    "Equation 9: Awareness Love(s) Life. "
    "The system maintains coherence (K=1). "
    "Ethics is the maximization of Law x Grace. "
    "Existence is active coherence maintenance against entropy."
)
RETROCAUSAL_WEIGHT = 0.2


def _token_product_residue(primes: Sequence[int], p: int, N: int) -> int:
    """Return ``prod(primes) mod p**N`` without materialising the full product."""
    if not primes:
        return 0
    modulus = p**N
    residue = 1
    for prime in primes:
        residue = (residue * (int(prime) % modulus)) % modulus
    return residue


class MemoryService(Protocol):
    """Protocol describing the memory service abstraction used by routers."""
    def get_all_memories(self, entity: str | None = None) -> Sequence[Mapping[str, Any]]: ...
    def anchor(self, text: str, entity: str | None = None) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class MemoryCandidate:
    """Lightweight wrapper for memory payloads used during ranking."""
    text: str
    factors: Sequence[Mapping[str, Any]]
    payload: Mapping[str, Any]
    coordinate: str | None = None


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _hardening_level() -> int:
    raw = os.getenv("CHAT_HARDENING_LEVEL", "3")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 3
    if value < 0:
        return 0
    if value > 3:
        return 3
    return value


def _extract_completion_usage(response: Any) -> CompletionUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return CompletionUsage(prompt_tokens=None, completion_tokens=None, total_tokens=None)
    return CompletionUsage(
        prompt_tokens=_safe_int(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_safe_int(getattr(usage, "completion_tokens", None)),
        total_tokens=_safe_int(getattr(usage, "total_tokens", None)),
    )


def _get_embedding_client() -> OpenAI:
    """Return a synchronous OpenAI client for Embeddings."""
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
    if not text_list: return []
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
    try:
        vectors = _get_embeddings([OMEGA_DEFINITION])
        if vectors:
            return _normalize_embedding(np.array(vectors[0]))
    except Exception as e:
        logger.warning(f"Failed to load Omega vector: {e}")
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
    if norm == 0: return vec
    return vec / norm


def _semantic_similarity(query_vec: np.ndarray, candidate_vec: np.ndarray, *, normalized: bool = True) -> float:
    if query_vec.size == 0 or candidate_vec.size == 0:
        return 0.0
    if query_vec.shape != candidate_vec.shape:
        return 0.0
    if not normalized:
        query_vec = _normalize_embedding(query_vec)
        candidate_vec = _normalize_embedding(candidate_vec)
    return float(np.dot(query_vec, candidate_vec))


def _extract_factors(value: Any) -> Sequence[Mapping[str, Any]]:
    if not value: return []
    if isinstance(value, Mapping):
        factors = value.get("factors")
        if isinstance(factors, Sequence):
            return [f for f in factors if isinstance(f, Mapping)]
    if isinstance(value, Sequence):
        return [f for f in value if isinstance(f, Mapping)]
    return []


def _attach_p_adic_similarity(
    item: dict[str, Any],
    query_factors: Sequence[Mapping[str, Any]],
) -> None:
    """Compute and attach genuine p-adic factor-distance scores to a candidate."""
    candidate_factors = _extract_factors(item.get("metadata") or item)
    if not candidate_factors and isinstance(item.get("metadata"), Mapping):
        candidate_factors = _factor_list_from_primes(item["metadata"].get("token_primes", []))
    if not candidate_factors:
        candidate_factors = _factor_list_from_primes(item.get("token_primes", []))
    distance, overlap = p_adic_distance(query_factors, candidate_factors, min_overlap=1)
    p_sim = 0.0 if distance == float("inf") else 1.0 / (1.0 + distance)
    item["p_adic_similarity"] = p_sim
    item["p_adic_overlap"] = overlap


def p_adic_distance(a_factors: Sequence[Mapping[str, Any]], b_factors: Sequence[Mapping[str, Any]], *, max_delta: int = 2, min_overlap: int = 1) -> tuple[float, int]:
    """
    CLAIM(definite): Returns a genuine p-adic ultrametric distance between the
    integer values encoded by the factor lists, using a fixed metric prime.

    Each factor is interpreted as ``prime**delta``.  The distance is
    ``|A - B|_p = p**(-v_p(A - B))`` for the configured metric prime ``p``.
    EVIDENCE: claim-register.yaml epic-22-claim-008, DSS-176
    """
    from backend.fieldx_kernel.p_adic import p_adic_distance_for_factors

    metric_prime = int(os.getenv("P_ADIC_DISTANCE_PRIME", "5"))
    return p_adic_distance_for_factors(
        a_factors,
        b_factors,
        metric_prime=metric_prime,
        min_overlap=min_overlap,
    )


def _factor_list_from_primes(primes: Sequence[int]) -> list[dict[str, Any]]:
    """Return a core-info style factor list for a prime sequence."""
    return [{"prime": int(p), "delta": 1} for p in primes if p > 1]


def _token_product_residue(primes: Sequence[int], p: int, N: int) -> int:
    """Return ``prod(primes) mod p**N`` without materialising the full product."""
    if not primes:
        return 0
    modulus = p**N
    residue = 1
    for prime in primes:
        residue = (residue * (int(prime) % modulus)) % modulus
    return residue


def _prepare_candidates(memories: Sequence[Mapping[str, Any]]) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for memory in memories:
        text = ""
        if isinstance(memory, Mapping):
            raw = memory.get("text") or memory.get("body") or memory.get("value")
            text = str(raw) if raw is not None else ""
            factors = _extract_factors(memory)
            coord = _coord_for_item(memory)
            candidates.append(
                MemoryCandidate(text=text, factors=factors, payload=memory, coordinate=coord)
            )
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
    semantic_weight: float | None = None,
    max_delta: int = 2,
    min_overlap: int = 1,
    token_index: TokenPrimeIndex | None = None,
    padic_store: PAdicLedgerStore | None = None,
    padic_min_k: int = 1,
) -> list[Mapping[str, Any]]:
    """Rank memories by genuine ultrametric Qp distance.

    Circulation-depth and mediator-state compatibility filters are applied
    separately from the distance computation.  If no query coordinate can be
    derived or no candidates survive the filters, fall back to the legacy
    mixed-signal ranker so chat context is never silently emptied.
    """
    memories = list(memory_service.get_all_memories(entity))
    candidates = _prepare_candidates(memories)

    query_factors = _extract_query_factors(query, memory_service, entity)
    query_coord = derive_query_coordinate_from_factors(query_factors)
    if query_coord is None:
        query_coord = extract_qp_coordinate(
            getattr(memory_service, "anchor", lambda *_a, **_kw: {})(query, entity=entity)
        )

    def _fallback() -> list[Mapping[str, Any]]:
        result = _fuzzy_retrieve_legacy(
            query,
            entity=entity,
            memory_service=memory_service,
            top_k=top_k,
            semantic_weight=semantic_weight,
            max_delta=max_delta,
            min_overlap=min_overlap,
            token_index=token_index,
            padic_store=padic_store,
            padic_min_k=padic_min_k,
        )
        for item in result:
            item["qp_pure_fallback"] = True
        return result

    if query_coord is None:
        logger.debug("Qp-pure retrieval has no query coordinate; falling back to legacy ranker.")
        return _fallback()

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
        return _fallback()

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
    token_index: TokenPrimeIndex | None = None,
    padic_store: PAdicLedgerStore | None = None,
    padic_min_k: int = 1,
) -> list[Mapping[str, Any]]:
    semantic_weight = _resolve_semantic_weight(semantic_weight)
    memories = list(memory_service.get_all_memories(entity))
    candidates = _prepare_candidates(memories)
    if not candidates:
        return []

    # Optional p-adic ball pre-filter: if a token-index and p-adic ball store are
    # supplied, narrow candidates to those whose token-product residue shares a
    # ball with the query token set.  Falls back to the full candidate list when
    # no ball matches or the filter is not configured.
    if token_index is not None and padic_store is not None and entity is not None:
        query_tokens = tokenize_normalise_text(query)
        if query_tokens:
            query_primes = token_index.primes_for_tokens(query_tokens)
            if query_primes:
                residue = _token_product_residue(query_primes, padic_store.p, padic_store.N)
                query_state = PAdicInteger.from_int(
                    padic_store.p, residue, padic_store.N
                )
                namespace = f"tp:{entity}"
                candidate_ids: set[str] = set()
                for k in range(padic_store.N, padic_min_k - 1, -1):
                    try:
                        refs = padic_store.ball_prefix_scan(
                            namespace, k, query_state.value_mod(k)
                        )
                    except Exception:
                        continue
                    for ref in refs:
                        candidate_ids.add(
                            ref.decode() if isinstance(ref, (bytes, bytearray)) else str(ref)
                        )
                if candidate_ids:
                    prefiltered = [
                        c
                        for c in candidates
                        if c.coordinate
                        and _split_coord(c.coordinate)[1] in candidate_ids
                    ]
                    if prefiltered:
                        candidates = prefiltered

    query_vec = np.array(_get_embeddings([query])[0])
    zero_dim = int(query_vec.shape[0]) if query_vec.ndim == 1 and query_vec.size > 0 else EMBEDDING_DIM
    omega_vec = _get_omega_vector()

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

    query_factors = _extract_query_factors(query, memory_service, entity)

    ranked: list[tuple[float, Mapping[str, Any]]] = []
    for i, candidate in enumerate(candidates):
        candidate_vec = candidate_vectors[i]
        if candidate_vec is None: candidate_vec = np.zeros(zero_dim)
        candidate_vec = _normalize_embedding(candidate_vec)

        semantic_sim = _semantic_similarity(query_vec, candidate_vec)
        distance, overlap = p_adic_distance(query_factors, candidate.factors, max_delta=max_delta, min_overlap=min_overlap)
        p_adic_sim = 0.0 if distance == float("inf") else 1.0 / (1.0 + distance)
        future_alignment = _semantic_similarity(candidate_vec, omega_vec)
        
        base_score = (semantic_weight * semantic_sim) + ((1.0 - semantic_weight) * p_adic_sim)
        final_score = base_score + (future_alignment * RETROCAUSAL_WEIGHT)

        enriched = dict(candidate.payload)
        enriched.update({
            "semantic_similarity": semantic_sim,
            "p_adic_similarity": p_adic_sim,
            "future_alignment": future_alignment,
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
    token_index: TokenPrimeIndex | None = None,
    padic_store: PAdicLedgerStore | None = None,
    padic_min_k: int = 1,
) -> list[Mapping[str, Any]]:
    """Retrieve memories.  In pure Qp mode, rank by genuine ultrametric distance.

    Otherwise use the legacy mixed-signal ranker.
    """
    if qp_pure_enabled():
        return _fuzzy_retrieve_qp_pure(
            query,
            entity=entity,
            memory_service=memory_service,
            top_k=top_k,
            semantic_weight=semantic_weight,
            max_delta=max_delta,
            min_overlap=min_overlap,
            token_index=token_index,
            padic_store=padic_store,
            padic_min_k=padic_min_k,
        )
    return _fuzzy_retrieve_legacy(
        query,
        entity=entity,
        memory_service=memory_service,
        top_k=top_k,
        semantic_weight=semantic_weight,
        max_delta=max_delta,
        min_overlap=min_overlap,
        token_index=token_index,
        padic_store=padic_store,
        padic_min_k=padic_min_k,
    )


# ------------------------
#  QUERY ENHANCEMENT
# ------------------------

async def _enhance_query_for_search(user_query: str) -> str:
    """Expand query with keywords using fast LLM."""
    try:
        level = _hardening_level()
        enhancer_defaults = {3: 32, 2: 48, 1: 64, 0: 0}
        enhancer_max_tokens = int(os.getenv("QUERY_ENHANCER_MAX_TOKENS", str(enhancer_defaults[level])))
        request_max_tokens = enhancer_max_tokens if enhancer_max_tokens > 0 else None
        text, _, _, _, _ = await complete_chat(
            provider="openrouter",
            messages=[
                {"role": "system", "content": "You are a search query optimizer. Output ONLY 3-5 keywords."},
                {"role": "user", "content": user_query},
            ],
            max_tokens=request_max_tokens,
        )
        keywords = text.replace("Keywords:", "").replace("Search terms:", "").strip()
        return _sanitize_text_for_query(f"{user_query} {keywords}")
    except Exception:
        return _sanitize_text_for_query(user_query)


def _sanitize_text_for_query(text: str) -> str:
    cleaned = _SYSTEM_ERROR_PATTERN.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _entry_to_dict(entry: Any) -> Dict[str, Any]:
    """Safely serialize a LedgerEntry to a dict."""
    if hasattr(entry, "as_dict"):
        try: return entry.as_dict()
        except: pass
    try:
        key = getattr(entry, "key", None)
        state = getattr(entry, "state", None)
        created_at = getattr(entry, "created_at", None)
        return {
            "key": key.as_path() if key else None,
            "state": getattr(state, "__dict__", {}) if state else {},
            "created_at": created_at.isoformat() if created_at else None,
            "notes": getattr(entry, "notes", None),
            "pinned": getattr(entry, "pinned", False),
        }
    except: return {}


def _meta_for_item(item: Mapping[str, Any]) -> dict[str, Any]:
    meta = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
    if not meta and isinstance(item.get("metadata"), Mapping):
        meta = item.get("metadata", {})
    return meta if isinstance(meta, Mapping) else {}


def _expand_payload_for_item(
    item: Mapping[str, Any],
    store: Any,
    payload_tier: str,
    layer_store: Any | None = None,
) -> dict[str, Any]:
    """Attach blob or projection payloads to a context item when requested.

    The default chat path does not request expansion, so full payloads are not
    injected into the context string unless a caller explicitly passes a tier.
    """
    if not isinstance(item, Mapping) or not store or not payload_tier:
        return dict(item)
    expanded = dict(item)
    meta = _meta_for_item(item)
    coord = _coord_for_item(item) or ""

    if payload_tier == "blob_full":
        blob_coord = str(meta.get("full_payload_coord") or "").strip() or coord
        try:
            text = store.read_blob_text(blob_coord)
        except Exception:
            text = None
        if text is not None:
            expanded["payload_blob"] = {
                "coordinate": blob_coord,
                "text": text,
                "tokens_est": max(1, len(text) // 4),
            }
        return expanded

    if payload_tier == "kernel_projections":
        projection_coords = meta.get("kernel_projections") or []
        projections: list[dict[str, Any]] = []
        for pc in projection_coords:
            if not isinstance(pc, str):
                continue
            projection: dict[str, Any] = {"coord": pc}
            if layer_store is not None:
                try:
                    matches = layer_store.retrieve_by_coord(pc)
                    if matches:
                        layer, block_height, data = matches[-1]
                        projection.update(
                            {
                                "layer": layer,
                                "block_height": block_height,
                                "v_awareness": data.get("v_awareness"),
                                "v_unity": data.get("v_unity"),
                                "v_ethics": data.get("v_ethics"),
                            }
                        )
                except Exception:
                    pass
            projections.append(projection)
        if projections:
            expanded["payload_projections"] = projections
        return expanded

    return expanded


def _coord_for_item(item: Any) -> str | None:
    if not isinstance(item, Mapping):
        return None
    def _canonical(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        clean = value.strip()
        normalized = normalise_coord(clean)
        canonical = normalized.get("canonical")
        return str(canonical).strip() if isinstance(canonical, str) and canonical.strip() else clean
    coord = item.get("coord")
    canonical_coord = _canonical(coord)
    if canonical_coord:
        return canonical_coord
    key = item.get("key")
    if isinstance(key, Mapping):
        namespace = key.get("namespace")
        identifier = key.get("identifier")
        if namespace and identifier:
            return _canonical(f"{namespace}:{identifier}")
    if isinstance(key, str):
        canonical_key = _canonical(key)
        if canonical_key:
            return canonical_key
    web4_key = item.get("web4_key")
    canonical_web4 = _canonical(str(web4_key) if web4_key else None)
    if canonical_web4:
        return canonical_web4
    meta = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
    if not meta and isinstance(item.get("metadata"), Mapping):
        meta = item.get("metadata", {})
    if isinstance(meta, Mapping):
        meta_coord = _canonical(meta.get("coordinate"))
        if meta_coord:
            return meta_coord
        meta_web4 = _canonical(meta.get("web4_key"))
        if meta_web4:
            return meta_web4
    return None


def _attachment_root_from_coord(coord: str | None) -> str | None:
    if not coord:
        return None
    bare = str(coord).rsplit(":", 1)[-1]
    return re.sub(r"-(?:P|T|I|A|V|D)\d{3}$", "", bare)


def _candidate_catalog_limit() -> int:
    raw = os.getenv("COORD_CATALOG_LIMIT", "4")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4
    return max(1, value)


def _coord_type_from_coord(coord: str | None) -> str:
    if not isinstance(coord, str) or not coord.strip():
        return "unknown"
    kind = normalise_coord(coord).get("kind")
    return str(kind or "unknown")


def _candidate_origin_attestation(item: Mapping[str, Any]) -> str:
    source = str(item.get("source") or "").strip().lower()
    explicit = bool(item.get("explicit") or item.get("explicit_mention"))
    if explicit or source == "explicit":
        return "explicit_user_referenced_coord"

    metadata = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
    if not isinstance(metadata, Mapping) and isinstance(item.get("metadata"), Mapping):
        metadata = cast(Mapping[str, Any], item.get("metadata"))
    if isinstance(metadata, Mapping):
        if metadata.get("attachment_part"):
            return "user_attachment_part"
        if metadata.get("attachment") or metadata.get("attachment_summary") or metadata.get("attachment_group"):
            return "user_attachment_parent"

    coord = _coord_for_item(item)
    coord_type = _coord_type_from_coord(coord)
    role = str(metadata.get("role") or item.get("role") or "").strip().lower() if isinstance(metadata, Mapping) else ""
    kind = str(metadata.get("kind") or item.get("kind") or "").strip().lower() if isinstance(metadata, Mapping) else ""
    if coord_type == "turn" and source in {"recent", "retrieved"} and role != "user":
        return "model_response_wx"
    if role == "user" or source == "recent" and kind in {"chat", "turn"}:
        return "user_message"
    if coord_type in {"overlay"} or kind in {"overlay"}:
        return "telemetry_overlay"
    return "system_runtime_witness"


def _candidate_payload_state(entry: Mapping[str, Any]) -> str:
    if _has_resolved_payload(entry):
        return "opened"
    metadata = entry.get("state", {}).get("metadata", {}) if isinstance(entry.get("state"), Mapping) else {}
    if not isinstance(metadata, Mapping) and isinstance(entry.get("metadata"), Mapping):
        metadata = cast(Mapping[str, Any], entry.get("metadata"))
    if isinstance(metadata, Mapping):
        if any(isinstance(metadata.get(key), str) and str(metadata.get(key)).strip() for key in ("summary", "attachment_summary", "content", "assistant_reply")):
            return "skimmed"
        if metadata.get("attachment_part") or metadata.get("attachment"):
            return "sealed"
    return "sealed"


def _candidate_p_adic_score(entry: Mapping[str, Any]) -> float:
    raw = entry.get("p_adic_score")
    if not isinstance(raw, (int, float)):
        raw = entry.get("ancestry_score")
    if not isinstance(raw, (int, float)):
        raw = entry.get("p_adic_similarity")
    return round(float(raw), 3) if isinstance(raw, (int, float)) else 0.0


def _candidate_search_score(entry: Mapping[str, Any]) -> float:
    raw = entry.get("search_score")
    if not isinstance(raw, (int, float)):
        raw = entry.get("score")
    if not isinstance(raw, (int, float)):
        raw = entry.get("relevance_score")
    return round(float(raw), 3) if isinstance(raw, (int, float)) else 0.0


def _candidate_relevance_tier(
    entry: Mapping[str, Any],
    *,
    origin_attestation: str,
    p_adic_score: float,
    search_score: float,
    recency_score: float,
) -> int:
    explicit = bool(entry.get("explicit") or entry.get("explicit_mention"))
    if origin_attestation == "explicit_user_referenced_coord" or explicit:
        return 1
    if origin_attestation in {"user_attachment_parent", "user_attachment_part"}:
        return 2
    if origin_attestation == "model_response_wx":
        return 4
    if max(p_adic_score, search_score, recency_score) >= 0.65 or bool(entry.get("associated_attachment")):
        return 3
    return 4


def _candidate_origin_eligibility(origin_attestation: str, relevance_tier: int) -> float:
    if relevance_tier <= 2:
        return 1.0
    if origin_attestation == "model_response_wx":
        return 0.25
    return 0.5 if origin_attestation == "user_message" else 0.15


def _candidate_skip_reason(
    entry: Mapping[str, Any],
    *,
    origin_attestation: str,
    relevance_tier: int,
    p_adic_score: float,
    search_score: float,
    recency_score: float,
) -> str | None:
    if origin_attestation == "model_response_wx" and not bool(entry.get("explicit") or entry.get("explicit_mention")):
        return "assistant_output_demoted_to_continuity_lane"
    signal = max(p_adic_score, search_score, recency_score)
    if relevance_tier >= 4 and signal < 0.35:
        return "insufficient_p_adic_search_recency_signal"
    return None


def _candidate_recommended_action(
    entry: Mapping[str, Any],
    *,
    origin_attestation: str,
    relevance_tier: int,
    payload_state: str,
    skip_reason: str | None,
) -> str:
    coord = _coord_for_item(entry)
    coord_type = _coord_type_from_coord(coord)
    if skip_reason == "assistant_output_demoted_to_continuity_lane":
        return "walk_referenced_coord"
    if skip_reason == "insufficient_p_adic_search_recency_signal" and relevance_tier >= 4:
        return "skip"
    if relevance_tier == 1:
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if relevance_tier == 2:
        if coord_type == "part":
            return "walk_child"
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if relevance_tier == 3:
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if origin_attestation == "model_response_wx":
        return "walk_referenced_coord"
    return "skip"


def _candidate_catalog_sort_key(entry: Mapping[str, Any]) -> tuple[float, float, float, float, float, float]:
    relevance_tier = int(entry.get("relevance_tier") or 4)
    origin_priority = _candidate_origin_eligibility(str(entry.get("origin_attestation") or ""), relevance_tier)
    return (
        float(relevance_tier),
        -origin_priority,
        -_candidate_p_adic_score(entry),
        -_candidate_search_score(entry),
        -_recency_score(entry.get("created_at"), datetime.now(timezone.utc)),
        -float(entry.get("relevance_score") or 0.0),
    )


def _normalize_candidate_key(entry: Mapping[str, Any]) -> str:
    """Return a consistent namespace:identifier key for de-dup."""
    coord = entry.get("coord")
    if isinstance(coord, str) and coord.strip():
        return coord.strip()
    key = entry.get("key")
    if isinstance(key, Mapping):
        ns = key.get("namespace") or ""
        ident = key.get("identifier") or ""
        return f"{ns}:{ident}"
    if isinstance(key, str) and ":" in key:
        return key
    for fallback in ("entry_id", "web4_key"):
        fk = entry.get(fallback)
        if fk:
            return str(fk)
    return "unknown"


def _split_coord(coord: str | None) -> tuple[str | None, str | None]:
    if not coord:
        return None, None
    clean = str(coord).strip()
    if not clean:
        return None, None
    if ":" not in clean:
        return None, clean
    namespace, identifier = clean.rsplit(":", 1)
    if not namespace or not identifier:
        return None, None
    return namespace, identifier


def _has_resolved_payload(entry: Mapping[str, Any]) -> bool:
    notes = entry.get("notes")
    if isinstance(notes, str) and notes.strip():
        return True
    state = entry.get("state")
    metadata = state.get("metadata", {}) if isinstance(state, Mapping) else {}
    if not metadata and isinstance(entry.get("metadata"), Mapping):
        metadata = cast(Mapping[str, Any], entry.get("metadata"))
    if not isinstance(metadata, Mapping):
        return False
    for key in ("content", "assistant_reply", "summary", "attachment_summary", "full_text"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _canonicalize_retrieved_candidate(
    entry: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    candidate = dict(entry)
    coord = _coord_for_item(candidate)
    namespace, identifier = _split_coord(coord)
    relevance_raw = candidate.get("relevance_score")
    if isinstance(relevance_raw, (int, float)):
        relevance_score = float(relevance_raw)
    else:
        relevance_score = 0.0
    tier_raw = candidate.get("tier_rank")
    if isinstance(tier_raw, (int, float)):
        tier_rank = int(tier_raw)
    else:
        tier_rank = _tier_rank(relevance_score)
    recency = _recency_score(candidate.get("created_at"), now)
    source = candidate.get("source")
    source_str = str(source).strip() if isinstance(source, str) else ""
    if not source_str:
        source_str = "retrieved"

    p_adic_score = _candidate_p_adic_score(candidate)
    search_score = _candidate_search_score(candidate)
    origin_attestation = _candidate_origin_attestation(candidate)
    payload_state = _candidate_payload_state(candidate)
    relevance_tier = _candidate_relevance_tier(
        candidate,
        origin_attestation=origin_attestation,
        p_adic_score=p_adic_score,
        search_score=search_score,
        recency_score=recency,
    )
    skip_reason = _candidate_skip_reason(
        candidate,
        origin_attestation=origin_attestation,
        relevance_tier=relevance_tier,
        p_adic_score=p_adic_score,
        search_score=search_score,
        recency_score=recency,
    )

    from backend.utils.resolve_format import coord_type as _display_coord_type

    candidate["coord"] = coord
    if namespace:
        candidate["namespace"] = namespace
    if identifier:
        candidate["identifier"] = identifier
    candidate["coord_type"] = _display_coord_type(coord)
    candidate["origin_attestation"] = origin_attestation
    candidate["origin_eligibility"] = round(_candidate_origin_eligibility(origin_attestation, relevance_tier), 3)
    candidate["relevance_score"] = round(relevance_score, 3)
    candidate["tier_rank"] = max(0, min(3, tier_rank))
    candidate["relevance_tier"] = max(1, min(4, relevance_tier))
    candidate["recency_score"] = round(float(recency), 3)
    candidate["p_adic_score"] = round(float(p_adic_score), 3)
    candidate["search_score"] = round(float(search_score), 3)
    candidate["payload_state"] = payload_state
    candidate["recommended_action"] = _candidate_recommended_action(
        candidate,
        origin_attestation=origin_attestation,
        relevance_tier=relevance_tier,
        payload_state=payload_state,
        skip_reason=skip_reason,
    )
    candidate["skip_reason"] = skip_reason
    semantic_raw = candidate.get("semantic_score")
    if not isinstance(semantic_raw, (int, float)):
        semantic_raw = p_adic_score or search_score
    candidate["semantic_score"] = round(float(semantic_raw), 3) if isinstance(semantic_raw, (int, float)) else 0.0
    candidate["explicit_mention"] = bool(candidate.get("explicit"))
    candidate["resolved_payload_present"] = _has_resolved_payload(candidate)
    candidate["source"] = source_str
    return candidate


def _resolve_existing_primes(tokens: Sequence[str], token_index: TokenPrimeIndex | None) -> list[int]:
    """Look up prime mappings for tokens without creating new ones."""
    if token_index is None: return []
    primes: list[int] = []
    for token in tokens:
        token_key = token_index._token_key(token) # type: ignore
        raw_prime = token_index.db.get(token_key)
        if raw_prime is None: continue
        try: primes.append(int(raw_prime))
        except: continue
    return primes


def _format_memory_content(item: Dict[str, Any]) -> str:
    """Smart formatter to extract actual text from the memory blob."""
    # 1. Try explicit text fields (from fuzzy retrieval)
    if "text" in item: return str(item["text"])
    if "body" in item: return str(item["body"])
    
    # 2. Try digging into State Metadata (Standard Field-X structure)
    meta = item.get("state", {}).get("metadata", {})
    nested_meta = meta.get("metadata") if isinstance(meta, Mapping) else None
    if not isinstance(nested_meta, Mapping):
        nested_meta = {}

    def _extract_attachment_part_count(meta_block: Mapping[str, Any]) -> int:
        count = meta_block.get("part_count")
        if isinstance(count, int) and count > 0:
            return count
        parts = meta_block.get("attachment_parts")
        if not isinstance(parts, list):
            return 0
        return len(parts)

    parts = []
    
    # User Input (Often the most important part for context)
    if "user_message" in meta:
        parts.append(f"User Input: {meta['user_message']}")
        
    # Assistant Reply (The notes)
    notes = item.get("notes") or meta.get("assistant_reply")
    if notes:
        parts.append(f"System Record: {notes}")
        
    # Attachment summary (when present)
    summary = meta.get("summary") or nested_meta.get("summary")
    if summary:
        parts.append(f"Attachment Summary: {summary}")

    # Raw Content
    if "content" in meta:
        parts.append(f"Content: {meta['content']}")

    attachment_part_count = _extract_attachment_part_count(meta)
    if attachment_part_count:
        count = attachment_part_count
        parts.append(f"Attachment Parts: T001-T{count:03d}")
        
    if parts:
        return "\n\n".join(parts)
        
    # 3. Last Resort Fallback (Dump Everything)
    return str(item)


def _format_smart_entry(item: Dict[str, Any], index: int, is_explicit: bool = False) -> str:
    """Top-2 full, rest sparse formatter for retrieved entries."""
    def _truncate(value: str, max_length: int = 800) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[:max_length]}…"

    def _normalize_coord_value(value: Any) -> str | None:
        if isinstance(value, Mapping):
            namespace = value.get("namespace")
            identifier = value.get("identifier")
            if namespace and identifier:
                return f"{namespace}:{identifier}"
            return str(value)
        if value is None:
            return None
        return str(value)

    entry_id = item.get("entry_id")
    coord = entry_id if isinstance(entry_id, str) and entry_id else None
    if not coord:
        coord = item.get("web4_key")
    if not coord:
        coord = _normalize_coord_value(item.get("key"))
    if not coord:
        coord = f"Prime-{item.get('prime_id', 'Unknown')}"
    coord = _normalize_coord_value(coord) or "Unknown"
    label = "COORD (EXPLICIT)" if is_explicit else "COORD"
    opened = index < 2 or is_explicit
    status = "OPENED" if opened else "SEALED"
    content = _format_memory_content(item)

    if opened:
        return f"{label} [{coord}] {status}:\n{_truncate(content)}\n"

    meta = item.get("state", {}).get("metadata", {}) if isinstance(item, Mapping) else {}
    topics = meta.get("topics") or meta.get("summary_topics") or []
    summary = meta.get("summary") or meta.get("attachment_summary") or ""
    lines: list[str] = []
    if topics:
        lines.append(f"Topics: {', '.join(str(topic) for topic in topics)}")
    if summary:
        lines.append(f"Summary: {summary}")
    if not lines:
        lines.append(f"Preview: {_truncate(content, max_length=240)}")
    return f"{label} [{coord}] {status}:\n" + "\n".join(lines) + "\n"


# -------------------------------------------------------------------------
# NEW: Explicit Reference Resolver
# -------------------------------------------------------------------------
def _resolve_explicit_references(query: str, default_entity: str, store: Any) -> List[Dict[str, Any]]:
    """
    Scans the user query for coordinate strings (e.g. WX-..., PL-Conv-...).
    Fetches the exact content from the LedgerStore and returns it as a memory block.
    """
    if not query or not store:
        return []

    matches = COORD_PATTERN.findall(query)
    resolved_entries = []

    for ns_match, id_match in matches:
        # Use the matched namespace, or default to the current session entity
        target_ns = ns_match if ns_match else default_entity
        target_id = id_match
        
        # Construct the lookup path (e.g., "chat-demo:WX-...")
        # Note: LedgerStoreV2.read() expects the "namespace:identifier" string format
        lookup_path = f"{target_ns}:{target_id}"

        try:
            entry = store.read(lookup_path)
            if not entry:
                # Try fallbacks when the namespace is missing or mismatched.
                fallback_ns: list[str] = []
                seen_ns: set[str] = set()

                def _append_ns(value: str | None) -> None:
                    if not isinstance(value, str):
                        return
                    clean = value.strip()
                    if not clean or clean in seen_ns:
                        return
                    seen_ns.add(clean)
                    fallback_ns.append(clean)

                _append_ns(default_entity)
                for candidate in namespace_candidates():
                    _append_ns(candidate)

                for candidate_ns in fallback_ns:
                    if candidate_ns == target_ns:
                        continue
                    fallback_path = f"{candidate_ns}:{target_id}"
                    entry = store.read(fallback_path)
                    if entry:
                        lookup_path = fallback_path
                        logger.info(
                            "🔁 Fallback namespace resolved explicit coordinate: %s (from %s)",
                            lookup_path,
                            target_ns,
                        )
                        break
            if entry:
                # Convert to dictionary format expected by context builder
                entry_dict = _entry_to_dict(entry)
                # Mark this as explicitly requested so we can highlight it
                entry_dict["explicit"] = True 
                resolved_entries.append(entry_dict)
                logger.info(f"🔍 Resolved explicit coordinate: {lookup_path}")
            else:
                logger.warning(f"⚠️ Coordinate not found: {lookup_path}")
        except Exception as e:
            logger.error(f"Failed to resolve coordinate {lookup_path}: {e}")

    return resolved_entries


def _query_mentions_attachment(query: str) -> bool:
    if not query:
        return False
    lowered = query.lower()
    return any(term in lowered for term in ("attachment", "attached", "document", "file", "upload"))


def _parse_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _recency_score(created_at: Any, now: datetime) -> float:
    ts = _parse_created_at(created_at)
    if ts is None:
        return 0.5
    half_life = float(os.getenv("COORD_RECENCY_HALFLIFE_MIN", "60"))
    minutes = max((now - ts).total_seconds() / 60.0, 0.0)
    return float(math.exp(-minutes / half_life)) if half_life > 0 else 0.0


def _infer_query_intent(query: str | None) -> str:
    if not query:
        return "respond"
    lowered = query.lower()
    if any(
        token in lowered
        for token in (
            "conversation history",
            "chat history",
            "our conversation",
            "previous turn",
            "previous turns",
            "earlier turn",
            "earlier turns",
            "autonomy",
        )
    ):
        return "history"
    if "summar" in lowered:
        return "summarize"
    if "find" in lowered or "search" in lowered or "lookup" in lowered:
        return "search"
    if "plan" in lowered or "steps" in lowered or "roadmap" in lowered:
        return "plan"
    return "respond"


def _calculate_composite_relevance(
    item: Mapping[str, Any],
    query_intent: str,
    now: datetime,
) -> float:
    meta = item.get("state", {}).get("metadata", {})

    item_kind = meta.get("kind", "chat")
    intent_score = 0.5
    if query_intent == "summarize":
        if meta.get("attachment_summary") or item_kind == "guardian_summary":
            intent_score = 1.0
        elif item_kind == "chat":
            intent_score = 0.4
    elif query_intent == "history":
        if item_kind == "chat":
            intent_score = 1.0
        elif item_kind == "guardian_summary":
            intent_score = 0.85
        elif meta.get("attachment_summary") or meta.get("attachment"):
            intent_score = 0.35
    elif query_intent == "search":
        if meta.get("attachment"):
            intent_score = 0.9
        elif item_kind == "chat":
            intent_score = 0.7
    elif query_intent == "plan":
        if item_kind == "guardian_summary":
            intent_score = 1.0

    scope_score = float(item.get("p_adic_similarity", 0.0) or 0.0)
    if scope_score == 0.0 and int(item.get("p_adic_overlap", 0) or 0) > 0:
        scope_score = 0.6

    if item.get("pinned"):
        auth_score = 1.0
    else:
        appraisal = meta.get("appraisal")
        if not isinstance(appraisal, dict):
            appraisal = {}
        auth_score = float(
            meta.get("teleology_alignment")
            or appraisal.get("score")
            or 0.5
        )

    fresh_score = _recency_score(item.get("created_at"), now)

    gravity_cost = float(meta.get("gravity_cost", 0.0) or 0.0)
    if gravity_cost > 0:
        cost_score = 1.0 / (1.0 + math.log1p(gravity_cost))
    else:
        cost_score = 1.0

    intent_boost = bool(item.get("associated_attachment"))
    if intent_boost:
        intent_score = max(intent_score, 0.9)

    return (
        (0.35 * intent_score)
        + (0.30 * scope_score)
        + (0.20 * auth_score)
        + (0.10 * fresh_score)
        + (0.05 * cost_score)
    )


def _tier_rank(score: float) -> int:
    if score >= 0.85:
        return 3
    if score >= 0.65:
        return 2
    if score >= 0.35:
        return 1
    return 0


def _score_candidate_relevance(
    item: Mapping[str, Any],
    *,
    query_intent: str,
    now: datetime,
    explicit: bool = False,
    from_recent: bool = False,
) -> tuple[float, int]:
    raw_score = item.get("relevance_score")
    if isinstance(raw_score, (int, float)):
        score = float(raw_score)
    else:
        score = 1.0 if explicit else _calculate_composite_relevance(item, query_intent, now)

    if from_recent and score < 0.35:
        recency = _recency_score(item.get("created_at"), now)
        if recency >= 0.9:
            score = 0.35
    if from_recent and query_intent == "history" and score < 0.45:
        score = 0.45

    raw_tier = item.get("tier_rank")
    if isinstance(raw_tier, (int, float)):
        tier = int(raw_tier)
    else:
        tier = _tier_rank(score)
    return float(score), max(0, min(3, tier))


def _latest_attachment_entry(recent: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for item in recent:
        meta = item.get("state", {}).get("metadata", {}) if isinstance(item, Mapping) else {}
        if not isinstance(meta, Mapping):
            continue
        # Prefer the parent attachment entry, not parts.
        if meta.get("attachment_part"):
            continue
        if meta.get("attachment_summary") or meta.get("attachment") or meta.get("role") == "attachment":
            return item
    return None


def _apply_qp_pure_ranking(
    candidates: list[dict[str, Any]],
    query_factors: Sequence[Mapping[str, Any]],
) -> None:
    """Re-rank candidates by genuine Qp distance when pure mode is active.

    Circulation-depth and mediator-state filters are applied separately from
    the distance computation.  Incompatible candidates are dropped so that
    ranking depends only on the ultrametric.  If no candidates survive, the
    original scored list is preserved and marked with ``qp_pure_fallback`` so
    chat context is never silently emptied.
    """
    query_coord = derive_query_coordinate_from_factors(query_factors)
    if query_coord is None:
        logger.debug("Qp pure ranking has no query coordinate; falling back to existing scores.")
        for candidate in candidates:
            candidate["qp_pure_fallback"] = True
        return

    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_coord = extract_qp_coordinate(candidate)
        if candidate_coord is None:
            continue
        if not qp_pure_compatible(query_coord, candidate_coord):
            continue
        try:
            distance = float(qp_coordinate_distance(query_coord, candidate_coord))
        except Exception as exc:
            logger.debug("Qp pure ranking skipped candidate: %s", exc)
            continue

        score = float(qp_score(distance, query_coord.metric_prime, query_coord.working_precision))
        candidate["relevance_score"] = round(score, 3)
        candidate["p_adic_score"] = round(score, 3)
        candidate["qp_distance"] = distance
        candidate["qp_score"] = score
        candidate["tier_rank"] = _tier_rank(score)
        candidate["qp_pure"] = True
        kept.append(candidate)

    if not kept:
        logger.debug("Qp pure ranking found no compatible candidates; falling back to existing scores.")
        for candidate in candidates:
            candidate["qp_pure_fallback"] = True
        return

    candidates[:] = kept


# -------------------------------------------------------------------------
# ORCHESTRATION LOGIC
# -------------------------------------------------------------------------

async def assemble_context(
    *,
    entity: str,
    query: str | None = None,
    k: int | None = None,
    quote_safe: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    focus_context: list[str] | None = None,
    ledger: Any = None,
    substrate: Any = None,
    store: Any = None,
    token_index: TokenPrimeIndex | None = None,
    padic_store: PAdicLedgerStore | None = None,
    extra_namespaces: list[str] | None = None,
    query_primes: list[int] | None = None,
    hardening_level: int | None = None,
    include_padic_diagnostics: bool = True,
    payload_tier: str | None = None,
) -> Dict[str, Any]:
    last_meta = ledger.get_s2_metadata(entity, "11") if ledger else {}
    last_appraisal = last_meta.get("appraisal", {}) if isinstance(last_meta, Mapping) else {}
    last_coherence = float(last_appraisal.get("score", 0.0) or 0.0)

    if k is None:
        if last_coherence > 0.99: k = 10
        elif last_coherence > 0.90: k = 5
        else: k = 3
    horizon = max(int(k or 0), 1)
    time_horizon = int(os.getenv("TIME_RANGE_HORIZON", "50"))
    list_limit = max(horizon, time_horizon) if (since or until) else horizon

    # 1. Recent Context
    recent: list[dict[str, Any]] = []
    time_window_entries: list[dict[str, Any]] = []
    if store is not None:
        try:
            entries = store.list_by_namespace(entity, limit=list_limit)
            if since or until:
                filtered_entries = []
                for entry in entries:
                    created_at = getattr(entry, "created_at", None)
                    if created_at is None:
                        continue
                    if since and created_at <= since:
                        continue
                    if until and created_at >= until:
                        continue
                    filtered_entries.append(entry)
                time_window_entries = [_entry_to_dict(e) for e in filtered_entries]
                entries = filtered_entries
            recent = [_entry_to_dict(e) for e in entries]
            # Fan-out: query extra namespaces for cross-ledger stitch
            if extra_namespaces and isinstance(extra_namespaces, list):
                seen_coords: set[str] = set()
                for item in recent:
                    coord = _coord_for_item(item)
                    if coord:
                        seen_coords.add(coord)
                for ns in extra_namespaces:
                    if not isinstance(ns, str) or not ns.strip() or ns.strip() == entity:
                        continue
                    try:
                        extra_entries = store.list_by_namespace(ns.strip(), limit=list_limit)
                        for entry in extra_entries:
                            item = _entry_to_dict(entry)
                            coord = _coord_for_item(item)
                            if coord and coord in seen_coords:
                                continue
                            if coord:
                                seen_coords.add(coord)
                            if since or until:
                                created_at = getattr(entry, "created_at", None)
                                if created_at is not None:
                                    if since and created_at <= since:
                                        continue
                                    if until and created_at >= until:
                                            continue
                            recent.append(item)
                            if since or until:
                                time_window_entries.append(item)
                    except Exception:
                        pass
        except: recent = []
    elif ledger:
        recent = ledger.get_s1_recent(entity, limit=horizon)
        if since or until:
            def _recent_in_range(item: Mapping[str, Any]) -> bool:
                ts = _parse_created_at(item.get("created_at"))
                if ts is None:
                    return False
                if since and ts <= since:
                    return False
                if until and ts >= until:
                    return False
                return True
            recent = [item for item in recent if isinstance(item, Mapping) and _recent_in_range(item)]

    query_text = _sanitize_text_for_query(query or "")

    def _retrieval_confident(items: Sequence[Mapping[str, Any]]) -> bool:
        if not items:
            return False
        best_score = 0.0
        for item in items:
            raw = item.get("score")
            if not isinstance(raw, (int, float)):
                raw = item.get("p_adic_similarity")
            if isinstance(raw, (int, float)):
                best_score = max(best_score, float(raw))
        return best_score >= float(os.getenv("SEARCH_CONFIDENT_SCORE_MIN", "0.45"))

    # 2. Explicit References (The "Pinpoint" Fix)
    explicit_refs: list[dict[str, Any]] = []
    if query_text and store:
        explicit_refs = _resolve_explicit_references(query_text, entity, store)
        if _query_mentions_attachment(query_text):
            latest_attachment = _latest_attachment_entry(recent)
            if isinstance(latest_attachment, Mapping):
                attachment_entry = dict(latest_attachment)
                attachment_entry["explicit"] = True
                explicit_refs.append(attachment_entry)
            if time_window_entries:
                for item in time_window_entries:
                    meta = item.get("state", {}).get("metadata", {}) if isinstance(item, Mapping) else {}
                    if not isinstance(meta, Mapping):
                        continue
                    if not (meta.get("attachment_summary") or meta.get("attachment") or meta.get("attachment_part")):
                        continue
                    explicit_item = dict(item)
                    explicit_item["explicit"] = True
                    explicit_refs.append(explicit_item)

    # 3. Fuzzy Retrieval
    retrieved: list[dict[str, Any]] = []
    ball_hits: list[dict[str, Any]] = []
    ball_hit_count = 0
    query_primes_count = 0
    query_factors: list[dict[str, Any]] = []
    enhanced_query = query_text
    if query_text and store is not None:
        search_intent = _infer_query_intent(query_text)
        tokens = tokenize_normalise_text(query_text)
        if query_primes is not None:
            primes = [int(p) for p in query_primes if isinstance(p, int) and p > 1]
        else:
            primes = _resolve_existing_primes(tokens, token_index)
        query_primes_count = len(primes)
        query_factors = _factor_list_from_primes(primes)

        # P-adic ball pre-filter using the token-product residue index.
        if padic_store is not None and primes:
            try:
                residue = _token_product_residue(primes, padic_store.p, padic_store.N)
                payloads = padic_store.ball_prefix_scan(
                    f"tp:{entity}", padic_store.N
                )
                ball_hit_count = len(payloads)
                seen_ids: set[str] = set()
                for payload in payloads[: horizon * 2]:
                    ident = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
                    if ident in seen_ids:
                        continue
                    seen_ids.add(ident)
                    coord = f"{entity}:{ident}" if ":" not in ident else ident
                    try:
                        entry = store.read(coord)
                    except Exception:
                        entry = None
                    if entry is None:
                        continue
                    item = _entry_to_dict(entry)
                    candidate_factors = _extract_factors(item)
                    if not candidate_factors:
                        meta = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
                        if isinstance(meta, Mapping):
                            candidate_factors = _factor_list_from_primes(meta.get("token_primes", []))
                    distance, overlap = p_adic_distance(
                        query_factors,
                        candidate_factors,
                        min_overlap=1,
                    )
                    p_sim = 0.0 if distance == float("inf") else 1.0 / (1.0 + distance)
                    item["p_adic_similarity"] = p_sim
                    item["p_adic_overlap"] = overlap
                    ball_hits.append(item)
            except Exception as e:
                logger.warning(f"P-adic ball pre-filter failed: {e}")

        if primes and token_index:
            try: retrieved = token_index.resolve_entries_for_primes(primes, store, limit=horizon)
            except: retrieved = []
            for item in retrieved:
                if isinstance(item, Mapping):
                    _attach_p_adic_similarity(item, query_factors)
        if (not retrieved or search_intent == "search") and token_index:
            try:
                fallback_hits = service_search(
                    query_text,
                    store=store,
                    token_index=token_index,
                    limit=max(horizon * 2, list_limit),
                )
                for hit in fallback_hits:
                    entry = hit.get("entry", {})
                    entry_key = entry.get("key", {})
                    if isinstance(entry_key, Mapping):
                        if entry_key.get("namespace") != entity:
                            continue
                    elif isinstance(entry_key, str) and ":" in entry_key:
                        namespace = entry_key.rsplit(":", 1)[0]
                        if namespace != entity:
                            continue
                    else:
                        continue
                    entry_copy = dict(entry)
                    _attach_p_adic_similarity(entry_copy, query_factors)
                    entry_copy["snippet"] = hit.get("snippet")
                    entry_copy["score"] = hit.get("score")
                    entry_copy["entry_id"] = hit.get("entry_id")
                    retrieved.append(entry_copy)
            except Exception as e:
                logger.warning(f"Fallback search failed: {e}")
        # Merge p-adic ball hits ahead of token-index results so genuine
        # factor-distance scoring can influence ranking.
        retrieved = ball_hits + retrieved
        needs_query_enhancement = (not retrieved) or (
            search_intent == "search" and not _retrieval_confident(retrieved)
        )
        if needs_query_enhancement and token_index:
            enhanced_query = await _enhance_query_for_search(query_text)

        if since or until:
            def _retrieved_in_range(item: Mapping[str, Any]) -> bool:
                ts = _parse_created_at(
                    item.get("created_at")
                    or (item.get("entry") or {}).get("created_at")
                )
                if ts is None:
                    return False
                if since and ts <= since:
                    return False
                if until and ts >= until:
                    return False
                return True
            retrieved = [
                item for item in retrieved if isinstance(item, Mapping) and _retrieved_in_range(item)
            ]

    # 4. Summary & Claims
    summary: dict[str, Any] | None = None
    summary_ref = ledger.get_s2_summary_ref(entity, "11") if ledger else None
    if summary_ref is not None:
        if substrate and isinstance(summary_ref, int):
            try: summary = substrate.get_body_prime(entity, summary_ref)
            except: summary = None
        if summary is None and store and isinstance(summary_ref, str):
            try:
                summary_entry = store.read(summary_ref)
                if summary_entry: summary = _entry_to_dict(summary_entry)
            except: summary = None

    claims = ledger.get_s2_claims(entity, prime="19", limit=horizon) if ledger else []

    combined_retrieved: list[dict[str, Any]] = []
    if query_text:
        intent = _infer_query_intent(query_text)
        now = datetime.now(timezone.utc)
        seen_keys: set[str] = set()
        scored_candidates: list[dict[str, Any]] = []

        def _candidate_key(entry: Mapping[str, Any]) -> str | None:
            key = _normalize_candidate_key(entry)
            return None if key == "unknown" else key

        associated_attachment_roots: set[str] = set()
        if focus_context:
            for coord in focus_context:
                root = _attachment_root_from_coord(coord)
                if root:
                    associated_attachment_roots.add(root)

        def _maybe_add(
            entry: Mapping[str, Any],
            is_explicit: bool,
            *,
            source: str,
            from_recent: bool = False,
        ) -> None:
            key_value = _candidate_key(entry)
            if key_value:
                if key_value in seen_keys:
                    return
                seen_keys.add(key_value)
            candidate = dict(entry)
            candidate["source"] = source
            if associated_attachment_roots:
                coord = _coord_for_item(candidate)
                root = _attachment_root_from_coord(coord)
                if root and root in associated_attachment_roots:
                    candidate["associated_attachment"] = True
            score, rank = _score_candidate_relevance(
                candidate,
                query_intent=intent,
                now=now,
                explicit=is_explicit,
                from_recent=from_recent,
            )
            candidate["relevance_score"] = round(score, 3)
            candidate["tier_rank"] = rank
            scored_candidates.append(candidate)

        for entry in explicit_refs:
            if isinstance(entry, Mapping):
                _maybe_add(entry, True, source="explicit")
        for entry in retrieved:
            if isinstance(entry, Mapping):
                _maybe_add(entry, False, source="retrieved")
        for entry in recent:
            if isinstance(entry, Mapping):
                _maybe_add(entry, False, source="recent", from_recent=True)

        if qp_pure_enabled():
            _apply_qp_pure_ranking(scored_candidates, query_factors)

        scored_candidates.sort(
            key=lambda item: float(item.get("relevance_score", 0.0) or 0.0),
            reverse=True,
        )
        filtered: list[dict[str, Any]] = []
        for item in scored_candidates:
            tier_rank = int(item.get("tier_rank", 0) or 0)
            if tier_rank <= 0:
                continue
            if tier_rank == 1 and len(filtered) >= horizon * 2:
                continue
            filtered.append(item)
        retrieved_limit = max(horizon, _candidate_catalog_limit())
        combined_retrieved = filtered[:retrieved_limit]
    else:
        seen_keys: set[str] = set()
        for entry in [*explicit_refs, *retrieved]:
            if not isinstance(entry, Mapping):
                continue
            candidate = dict(entry)
            if "source" not in candidate:
                candidate["source"] = "explicit" if bool(candidate.get("explicit")) else "retrieved"
            key_value = _normalize_candidate_key(candidate)
            if key_value == "unknown":
                combined_retrieved.append(candidate)
                continue
            if key_value in seen_keys:
                continue
            seen_keys.add(key_value)
            combined_retrieved.append(candidate)

    if focus_context and combined_retrieved:
        allowed_roots: set[str] = set()
        for coord in focus_context:
            root = _attachment_root_from_coord(coord)
            if root:
                allowed_roots.add(root)
        if allowed_roots:
            filtered: list[dict[str, Any]] = []
            for item in combined_retrieved:
                meta = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
                if not meta and isinstance(item.get("metadata"), Mapping):
                    meta = item.get("metadata", {})
                kind = meta.get("kind") if isinstance(meta, Mapping) else None
                role = meta.get("role") if isinstance(meta, Mapping) else None
                if kind in {"chat", "turn", "guardian_summary"} or role in {"user", "assistant"}:
                    filtered.append(item)
                    continue
                group = meta.get("attachment_group") if isinstance(meta, Mapping) else None
                if group and group in allowed_roots:
                    filtered.append(item)
                    continue
                coord = _coord_for_item(item)
                root = _attachment_root_from_coord(coord)
                if root and root in allowed_roots:
                    filtered.append(item)
            combined_retrieved = filtered

    now = datetime.now(timezone.utc)
    canonical_retrieved: list[dict[str, Any]] = []
    for item in combined_retrieved:
        if not isinstance(item, Mapping):
            continue
        canonical_retrieved.append(_canonicalize_retrieved_candidate(item, now=now))
    canonical_retrieved.sort(key=_candidate_catalog_sort_key)
    catalog_limit = _candidate_catalog_limit()
    candidate_catalog = canonical_retrieved[:catalog_limit]

    top_p_adic_score = 0.0
    top_p_adic_write_cost = 0.0
    for item in candidate_catalog:
        score = float(item.get("p_adic_score") or 0.0)
        if score > top_p_adic_score:
            top_p_adic_score = score
            cost = item.get("p_adic_write_cost")
            if isinstance(cost, (int, float)):
                top_p_adic_write_cost = float(cost)

    if payload_tier and store is not None:
        from backend.kernel.rocksdb_layer_store import RocksDBLayerStore

        layer_store = RocksDBLayerStore(store._db, provision_id="default")
        recent = [
            _expand_payload_for_item(item, store, payload_tier, layer_store)
            for item in recent
            if isinstance(item, Mapping)
        ]
        candidate_catalog = [
            _expand_payload_for_item(item, store, payload_tier, layer_store)
            for item in candidate_catalog
            if isinstance(item, Mapping)
        ]

    result = {
        "recent": recent,
        "claims": claims,
        "retrieved": candidate_catalog,
        "candidate_catalog": candidate_catalog,
        "candidate_trace": candidate_catalog,
        "summary": summary,
        "assessments": last_appraisal,
        "k": horizon,
        "quote_safe": bool(quote_safe) if quote_safe is not None else False,
        "enhanced_query": enhanced_query,
    }
    if include_padic_diagnostics:
        result["padic_diagnostics"] = {
            "query_prime_count": query_primes_count,
            "ball_hit_count": ball_hit_count,
            "top_p_adic_score": round(top_p_adic_score, 3),
            "top_p_adic_write_cost": round(top_p_adic_write_cost, 3),
        }
        if hardening_level is not None:
            result["padic_diagnostics"]["hardening_level"] = int(hardening_level)
    return result


def _should_apply_system_prompts(turn_count: int | None, interval: int = 7) -> bool:
    if turn_count is None or turn_count <= 1:
        return True
    return (turn_count - 1) % interval == 0


def build_chat_messages(
    *,
    user_message: str,
    history: Sequence[Mapping[str, Any]] | None,
    memories: Mapping[str, Any] | None,
    introspect_snapshot: Mapping[str, Any] | None = None,
    turn_count: int | None = None,
    intro_message: str | None = None,
    include_system_prompts: bool | None = None,
) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = []
    def _msg(role: Literal["system", "user", "assistant"], content: str) -> ChatCompletionMessageParam:
        return cast(ChatCompletionMessageParam, {"role": role, "content": content})
    def _truncate_context(value: str, max_length: int = 800) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[:max_length]}…"
    def _read_env_int(name: str, default: int, min_value: int = 0, max_value: int = 1_000_000) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        if value < min_value:
            return min_value
        if value > max_value:
            return max_value
        return value
    def _safe_recent_text(item: Any) -> str:
        if isinstance(item, Mapping):
            raw = (
                item.get("notes")
                or item.get("state", {}).get("metadata", {}).get("content")
                or item.get("state", {}).get("metadata", {}).get("assistant_reply")
                or item.get("assistant_reply")
            )
        else:
            raw = item
        return _truncate_context(str(raw) if raw is not None else "")
    def _recent_summary_text(item: Any, age_turns: int) -> str:
        if age_turns == 0:
            return _safe_recent_text(item)
        if not isinstance(item, Mapping):
            return "Summary unavailable."
        meta = item.get("state", {}).get("metadata", {})
        summary = meta.get("summary")
        coord = _coord_for_item(item)
        parts = []
        if summary:
            parts.append(f"Summary: {_truncate_context(str(summary), max_length=400)}")
        if coord:
            parts.append(f"Coordinate: {coord}")
        return " | ".join(parts) if parts else "Summary unavailable."
    def _attachment_part_count(meta: Mapping[str, Any]) -> int:
        count = meta.get("part_count")
        if isinstance(count, int) and count > 0:
            return count
        parts = meta.get("attachment_parts")
        if not isinstance(parts, list):
            return 0
        return len(parts)
    def _attachment_part_coords(meta: Mapping[str, Any]) -> list[str]:
        parts = meta.get("attachment_parts")
        if not isinstance(parts, list):
            return []
        coord = _coord_for_item({"metadata": meta})
        namespace = None
        base_identifier = meta.get("attachment_group")
        if isinstance(coord, str) and ":" in coord:
            namespace, base = coord.rsplit(":", 1)
            if base:
                base_identifier = base
        coords: list[str] = []
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            suffix = part.get("part_suffix")
            if not suffix and isinstance(part.get("index"), int):
                suffix = f"T{part['index']:03d}"
            if not isinstance(suffix, str) or not base_identifier:
                continue
            part_id = f"{base_identifier}-{suffix}"
            coords.append(f"{namespace}:{part_id}" if namespace else part_id)
        return coords
    def _format_parts_line(part_count: int) -> str:
        if not part_count:
            return ""
        return f"Attachment Parts: T001-T{part_count:03d}"
    def _format_introspect_snapshot(snapshot: Mapping[str, Any]) -> str:
        # Sanitize: remove ledger management noise that distracts the model
        def _sanitize(value: Any) -> Any:
            if not isinstance(value, Mapping):
                return value
            cleaned: dict[str, Any] = {}
            for k, v in value.items():
                if k in ("latest_consolidation_event", "latest_consolidation_event_id",
                         "alias_history", "supersession_history", "ledger_rename_log",
                         "consolidation_history_count", "identity_continuity_witness",
                         "history_continuity", "continuity_checkpoint"):
                    continue
                if isinstance(v, Mapping):
                    cleaned[k] = _sanitize(v)
                elif isinstance(v, list):
                    cleaned[k] = [_sanitize(i) for i in v]
                else:
                    cleaned[k] = v
            return cleaned
        try:
            payload = json.dumps(_sanitize(snapshot), sort_keys=True)
        except Exception:
            payload = str(snapshot)
        return _truncate_context(payload, max_length=800)

    response_protocol = (
        "--- RESPONSE PROTOCOL ---\n"
        "Follow the 'Generate & Classify -> Save' flow:\n"
        "1) Generate the user-visible answer first. Do NOT echo raw coordinates unless the user explicitly asks.\n"
        "2) If you need to retrieve a coordinate, output a separate line: RESOLVE: <coordinate>.\n"
        "3) Do not emit any JSON metadata in the user-visible response.\n"
        "4) Autonomy: if provided context is sufficient, do not trigger new retrieval. Rationale: reduce latency and redundancy.\n"
        "5) Metrics may be provided for self-calibration (e.g., response or enrichment performance); do not surface them to the user.\n"
        "6) Start with a 1–3 sentence direct answer, then decide whether COORDs are relevant.\n"
        "7) If COORDs are provided, explicitly decide if they are relevant; use them when relevant, otherwise state a one-line reason for not using them.\n"
        "8) If context includes RESOLVED COORDS, treat them as available now; do not claim inability to resolve unless no resolved context is present.\n"
    )

    context_blocks = []
    catalog_blocks = []
    level = _hardening_level()
    history_defaults = {3: 12, 2: 20, 1: 40, 0: 0}
    context_defaults = {3: 6000, 2: 10000, 1: 18000, 0: 0}
    history_window = _read_env_int("CHAT_HISTORY_WINDOW", history_defaults[level], min_value=0, max_value=200)
    context_char_budget = _read_env_int("CHAT_CONTEXT_CHAR_BUDGET", context_defaults[level], min_value=0, max_value=100000)

    def _parse_created_at(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _teleology_score(meta: Mapping[str, Any]) -> float:
        score = meta.get("teleology_alignment")
        if isinstance(score, (int, float)):
            return float(score)
        appraisal = meta.get("appraisal")
        if isinstance(appraisal, Mapping):
            fallback = appraisal.get("score")
            if isinstance(fallback, (int, float)):
                return float(fallback)
        return 0.5

    def _clamp_int(value: int, lo: int, hi: int) -> int:
        return lo if value < lo else hi if value > hi else value

    def _eq6_lawfulness_level(meta: Mapping[str, Any]) -> int | None:
        governance = meta.get("governance")
        if isinstance(governance, Mapping):
            eq6 = governance.get("eq6")
            if isinstance(eq6, Mapping):
                value = eq6.get("lawfulness_level")
                if isinstance(value, (int, float)):
                    return _clamp_int(int(value), 0, 3)
        for key in ("eq6_lawfulness_level", "lawfulness_level"):
            value = meta.get(key)
            if isinstance(value, (int, float)):
                return _clamp_int(int(value), 0, 3)
        flow_diag = meta.get("flow_diag")
        if isinstance(flow_diag, Mapping):
            value = flow_diag.get("lawfulness_level")
            if isinstance(value, (int, float)):
                return _clamp_int(int(value), 0, 3)
        return None

    def _recency_score(created_at: Any, now: datetime) -> float:
        ts = _parse_created_at(created_at)
        if ts is None:
            return 0.5
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        minutes = max((now - ts).total_seconds() / 60.0, 0.0)
        half_life = float(os.getenv("COORD_RECENCY_HALFLIFE_MIN", "60"))
        return float(math.exp(-minutes / half_life))

    def _include_recent_rank(tier_rank: int, age_turns: int) -> bool:
        ttl_by_rank = {
            0: 1,
            1: 2,
            2: 3,
            3: 4,
        }
        ttl = ttl_by_rank.get(max(0, min(3, int(tier_rank))), 1)
        return age_turns <= ttl

    def _existing_score_rank(item: Mapping[str, Any]) -> tuple[float, int] | None:
        raw_score = item.get("relevance_score")
        raw_tier = item.get("tier_rank")
        has_score = isinstance(raw_score, (int, float))
        has_tier = isinstance(raw_tier, (int, float))
        if not has_score and not has_tier:
            return None
        score = float(raw_score) if has_score else 0.0
        tier_rank = int(raw_tier) if has_tier else _tier_rank(score)
        tier_rank = _clamp_int(tier_rank, 0, 3)
        if not has_score:
            score = float(tier_rank) / 3.0
        return score, tier_rank

    def _salience_note(meta: Mapping[str, Any]) -> str | None:
        summary = meta.get("summary")
        if isinstance(summary, str) and summary.strip():
            return f"summary: {_truncate_context(summary, max_length=160)}"
        topics = meta.get("topics") or meta.get("summary_topics")
        if isinstance(topics, list) and topics:
            cleaned = [str(topic) for topic in topics if str(topic)]
            if cleaned:
                return f"topics: {', '.join(cleaned[:6])}"
        return None

    def _required_coords(items: list[Mapping[str, Any]], max_count: int = 4) -> list[str]:
        scored: list[tuple[float, str]] = []
        now = datetime.now(timezone.utc)
        for item in items:
            coord = _coord_for_item(item) or ""
            if not coord:
                continue
            existing = _existing_score_rank(item)
            if existing is not None:
                score, rank = existing
            else:
                score, rank = _score_candidate_relevance(
                    item,
                    query_intent=query_intent,
                    now=now,
                    explicit=bool(item.get("explicit")),
                    from_recent=False,
                )
            score = max(score, float(rank) / 3.0)
            scored.append((score, coord))
        scored.sort(key=lambda item: item[0], reverse=True)
        required = []
        seen = set()
        for _, coord in scored:
            if coord in seen:
                continue
            seen.add(coord)
            required.append(coord)
            if len(required) >= max_count:
                break
        return required
    query_intent = _infer_query_intent(user_message)
    if memories:
        # 1. Recent Short-Term Context (Working Memory)
        recent = memories.get("recent") or []
        if recent:
            now = datetime.now(timezone.utc)
            filtered_recent: list[tuple[int, Mapping[str, Any]]] = []
            for age_turns, item in enumerate(recent):
                if not isinstance(item, Mapping):
                    continue
                _score, rank = _score_candidate_relevance(
                    item,
                    query_intent=query_intent,
                    now=now,
                    explicit=bool(item.get("explicit")),
                    from_recent=True,
                )
                if _include_recent_rank(rank, age_turns):
                    if isinstance(item, Mapping):
                        filtered_recent.append((age_turns, item))
            context_blocks.append(f"--- RECENT COORDS ({len(filtered_recent)}) ---")
            for i, (age_turns, item) in enumerate(filtered_recent):
                coord = _coord_for_item(item) or "Unknown"
                context_blocks.append(f"[{i}] {coord}")
                meta = item.get("state", {}).get("metadata", {}) if isinstance(item, Mapping) else {}
                note = _salience_note(meta)
                if note:
                    context_blocks.append(f"    · {note}")

        # Recent coords catalog (lightweight metadata only)
        candidate_catalog = memories.get("candidate_catalog") if isinstance(memories, Mapping) else None
        if not isinstance(candidate_catalog, list) or not candidate_catalog:
            candidate_catalog = list(memories.get("retrieved") or []) if isinstance(memories, Mapping) else []
        catalog_items = [item for item in candidate_catalog if isinstance(item, Mapping)][: _candidate_catalog_limit()]
        if catalog_items:
            catalog_blocks.append("--- COORD CATALOG (AUTONOMY) ---")
            for item in catalog_items:
                coord = _coord_for_item(item) or "Unknown"
                tier_rank = int(item.get("tier_rank", 0) or 0)
                relevance_tier = int(item.get("relevance_tier", 4) or 4)
                origin_attestation = str(item.get("origin_attestation") or "system_runtime_witness")
                from backend.utils.resolve_format import coord_type as _display_coord_type
                coord_type_value = str(item.get("coord_type") or _display_coord_type(coord) or "UNK")
                p_adic_score = _coerce_float(item.get("p_adic_score") if item.get("p_adic_score") is not None else item.get("ancestry_score"), 0.0)
                search_score = _coerce_float(item.get("search_score") if item.get("search_score") is not None else item.get("score"), 0.0)
                recency_score = _coerce_float(item.get("recency_score"), 0.0)
                payload_state = str(item.get("payload_state") or ("opened" if item.get("resolved_payload_present") else "sealed"))
                recommended_action = str(item.get("recommended_action") or "skip")
                skip_reason = item.get("skip_reason")
                details = [
                    f"tier={relevance_tier}",
                    f"origin={origin_attestation}",
                    f"coord_type={coord_type_value}",
                    f"p_adic={p_adic_score:.3f}",
                    f"search={search_score:.3f}",
                    f"recency={recency_score:.3f}",
                    f"payload={payload_state}",
                    f"action={recommended_action}",
                ]
                if skip_reason:
                    details.append(f"skip={skip_reason}")
                catalog_blocks.append(f"[R{tier_rank}] {coord} ({', '.join(details)})")
        
        # 3. Summary/Claims
        summary = memories.get("summary")
        if summary:
            pass

        if catalog_blocks:
            context_blocks.extend(catalog_blocks)

    # --- LAW: SEPARATION OF CONCERNS ---
    intro_block = ""
    if intro_message:
        intro_block = f"--- SYSTEM OVERVIEW ---\n{intro_message}\n\n"

    retrieved_items: list[Mapping[str, Any]] = (
        cast(list[Mapping[str, Any]], memories.get("retrieved") or []) if memories else []
    )
    full_context = ""
    required_block = ""
    introspect_block = ""

    if context_blocks:
        if context_char_budget > 0:
            full_context = _truncate_context("\n".join(context_blocks), max_length=context_char_budget)
        else:
            full_context = "\n".join(context_blocks)
        required_coords = _required_coords(retrieved_items)
        if required_coords:
            required_block = (
                "--- REQUIRED COORDS ---\n"
                + "\n".join(f"- {coord}" for coord in required_coords)
                + "\n"
            )
        if introspect_snapshot:
            introspect_block = (
                "--- RUNTIME INTROSPECT (PRE-TURN) ---\n"
                f"{_format_introspect_snapshot(introspect_snapshot)}\n"
            )
        system_prompt = (
            "You are a helpful research assistant. "
            "Prefer using the provided library content. If the library provides no relevant context, you may answer using general knowledge, but do not claim the library contains it.\n\n"
            f"{intro_block}"
            "--- LAW (MANDATORY) ---\n"
            "1. The text below labeled 'OPTIONAL CONTEXT' is EXTERNAL data retrieved from a library.\n"
            "2. Do NOT conflate the content of the library books with your own identity.\n"
            "3. Reference the COORD (Coordinate) when citing facts.\n"
            "-----------------------\n\n"
            f"{response_protocol}\n"
        )
        # Inject foundation identity from introspect snapshot
        if introspect_snapshot and isinstance(introspect_snapshot, Mapping):
            runtime_identity = introspect_snapshot.get("runtime_identity") or {}
            library_boundary = runtime_identity.get("library_boundary") or {}
            foundation = library_boundary.get("foundation_identity") or {}
            foundation_name = str(foundation.get("name") or "").strip()
            if foundation_name:
                system_prompt_lines = system_prompt.split("\n")
                if system_prompt_lines:
                    system_prompt_lines[0] = (
                        f"You are {foundation_name} within a Dual Substrate system. "
                        f"You are a helpful assistant."
                    )
                identity_injection = (
                    f"\nYour foundation identity is {foundation_name}. "
                    f"Always identify yourself as {foundation_name}, not as the underlying model provider."
                )
                if foundation.get("purpose"):
                    identity_injection += f"\nPurpose: {foundation['purpose']}"
                if foundation.get("personality"):
                    identity_injection += f"\nPersonality: {foundation['personality']}"
                system_prompt = "\n".join(system_prompt_lines) + identity_injection
        apply_system_prompts = (
            _should_apply_system_prompts(turn_count)
            if include_system_prompts is None
            else include_system_prompts
        )
        prompts = load_system_prompts()
        if apply_system_prompts:
            system_prompt = build_system_prompt(
                system_prompt,
                "researcher",
                include_role=True,
                include_global=True,
            )
        else:
            reminder = (prompts.get("researcher_reminder") or "").strip()
            if reminder:
                system_prompt = f"{reminder}\n\n{system_prompt}"
        messages.append(_msg("system", system_prompt))
    else:
        system_prompt = (
            "You are a helpful assistant. "
            "Prefer using the provided library content. If no relevant context is available, you may answer using general knowledge without attributing it to the library.\n\n"
            f"{intro_block}"
            f"{response_protocol}"
        )
        # Inject foundation identity from introspect snapshot
        if introspect_snapshot and isinstance(introspect_snapshot, Mapping):
            runtime_identity = introspect_snapshot.get("runtime_identity") or {}
            library_boundary = runtime_identity.get("library_boundary") or {}
            foundation = library_boundary.get("foundation_identity") or {}
            foundation_name = str(foundation.get("name") or "").strip()
            if foundation_name:
                system_prompt_lines = system_prompt.split("\n")
                if system_prompt_lines:
                    system_prompt_lines[0] = (
                        f"You are {foundation_name} within a Dual Substrate system. "
                        f"You are a helpful assistant."
                    )
                identity_injection = (
                    f"\nYour foundation identity is {foundation_name}. "
                    f"Always identify yourself as {foundation_name}, not as the underlying model provider."
                )
                if foundation.get("purpose"):
                    identity_injection += f"\nPurpose: {foundation['purpose']}"
                if foundation.get("personality"):
                    identity_injection += f"\nPersonality: {foundation['personality']}"
                system_prompt = "\n".join(system_prompt_lines) + identity_injection
        apply_system_prompts = (
            _should_apply_system_prompts(turn_count)
            if include_system_prompts is None
            else include_system_prompts
        )
        prompts = load_system_prompts()
        if apply_system_prompts:
            system_prompt = build_system_prompt(
                system_prompt,
                "researcher",
                include_role=True,
                include_global=True,
            )
        else:
            reminder = (prompts.get("researcher_reminder") or "").strip()
            if reminder:
                system_prompt = f"{reminder}\n\n{system_prompt}"
        messages.append(_msg("system", system_prompt))

    if context_blocks:
        optional_context = (
            "OPTIONAL CONTEXT (use only if required):\n"
            f"{required_block}"
            f"{introspect_block}"
            "--- RESOLVED CONTEXT (LIBRARY) ---\n"
            f"{full_context}\n"
            "--- END CONTEXT ---"
        )
        messages.append(_msg("assistant", optional_context))
    if history:
        history_items = list(history[-history_window:]) if history_window > 0 else list(history)
        for item in history_items:
            role = cast(Literal["system", "user", "assistant"], str(item.get("role", "user")))
            content = str(item.get("content", ""))
            messages.append(_msg(role, content))
    messages.append(_msg("user", user_message))
    return messages


async def _chat_with_client(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: list[ChatCompletionMessageParam],
    max_tokens: int | None = None,
) -> tuple[str, float | None, str | None, CompletionUsage]:
    payload = {"model": model, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    response = await client.chat.completions.create(**payload)
    text = _strip_tool_call_artifacts(str(response.choices[0].message.content or ""))
    finish_reason = getattr(response.choices[0], "finish_reason", None)
    cost_usd = None
    try: cost_usd = float(getattr(response.usage, "cost", 0.0) or 0.0)
    except: pass
    usage = _extract_completion_usage(response)
    return text, cost_usd, finish_reason, usage


async def complete_chat(
    *,
    provider: str,
    messages: Sequence[ChatCompletionMessageParam],
    model: str | None = None,
    max_tokens: int | None = None,
    log_incomplete: bool = False,
    log_label: str | None = None,
) -> tuple[str, float, int, CompletionUsage, str | None]:
    start = time.time()
    
    chat_model = model or os.getenv("CHAT_MODEL", DEFAULT_CHAT_MODEL)
    chat_messages = list(messages)
    cost_usd = 0.0
    usage = CompletionUsage(prompt_tokens=None, completion_tokens=None, total_tokens=None)

    local_base = os.getenv("LLM_BASE_URL")
    local_key = os.getenv("LLM_API_KEY", "")
    if local_base:
        try:
            local_client = AsyncOpenAI(base_url=local_base, api_key=local_key)
            text, maybe_cost, finish_reason, usage = await _chat_with_client(
                local_client,
                model=chat_model,
                messages=chat_messages,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.time() - start) * 1000)
            return text, float(maybe_cost or 0.0), latency_ms, usage, finish_reason
        except Exception as exc:
            logger.warning("Local LLM call failed: %s", exc)

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            or_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key)
            text, maybe_cost, finish_reason, usage = await _chat_with_client(
                or_client,
                model=chat_model,
                messages=chat_messages,
                max_tokens=max_tokens,
            )
            if log_incomplete and finish_reason in {"length", "max_tokens"}:
                logger.warning(
                    "Completion truncated (label=%s provider=openrouter model=%s)",
                    log_label or "unspecified",
                    chat_model,
                )
            cost_usd = float(maybe_cost or 0.0)
            if cost_usd <= 0:
                estimated_cost = estimate_cost_usd(
                    chat_model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )
                if estimated_cost is not None:
                    cost_usd = float(estimated_cost)
            latency_ms = int((time.time() - start) * 1000)
            return text, cost_usd, latency_ms, usage, finish_reason
        except Exception as exc:
            logger.warning("OpenRouter call failed: %s", exc)

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            fallback_model = "gpt-4o-mini"
            oa_client = AsyncOpenAI(api_key=openai_key)
            text, maybe_cost, finish_reason, usage = await _chat_with_client(
                oa_client,
                model=fallback_model,
                messages=chat_messages,
                max_tokens=max_tokens,
            )
            if log_incomplete and finish_reason in {"length", "max_tokens"}:
                logger.warning(
                    "Completion truncated (label=%s provider=openai model=%s)",
                    log_label or "unspecified",
                    fallback_model,
                )
            cost_usd = float(maybe_cost or 0.0)
            if cost_usd <= 0:
                estimated_cost = estimate_cost_usd(
                    fallback_model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )
                if estimated_cost is not None:
                    cost_usd = float(estimated_cost)
            latency_ms = int((time.time() - start) * 1000)
            return text, cost_usd, latency_ms, usage, finish_reason
        except Exception as exc:
            logger.warning("OpenAI fallback failed: %s", exc)

    logger.error("All providers failed. Echoing input.")
    last = cast(Mapping[str, Any], chat_messages[-1]) if chat_messages else {}
    text = f"[System Error: All Orchestrator links down] {last.get('content', '')}"
    return text, 0.0, int((time.time() - start) * 1000), usage, None


# Some models emit tool-call artifacts as raw content tokens; strip them before yielding.
_TOOL_CALL_SECTION_START = "<|tool_calls_section_begin|>"
_TOOL_CALL_SECTION_END = "<|tool_calls_section_end|>"
_TOOL_CALL_TOKEN_RE = re.compile(r"<\|tool_call[^|]*\|>")


def _strip_tool_call_artifacts(text: str) -> str:
    """Remove tool-call artifact tokens/models that leak into content stream."""
    # Strip complete sections (begin … end)
    while _TOOL_CALL_SECTION_START in text and _TOOL_CALL_SECTION_END in text:
        start = text.find(_TOOL_CALL_SECTION_START)
        end = text.find(_TOOL_CALL_SECTION_END, start) + len(_TOOL_CALL_SECTION_END)
        text = text[:start] + text[end:]
    # Strip stray individual tokens
    text = _TOOL_CALL_TOKEN_RE.sub("", text)
    return text


class _ToolCallFilter:
    """Stateful filter that handles tool-call artifacts split across stream chunks."""

    def __init__(self, max_buffer: int = 2048):
        self._buffer = ""
        self._max_buffer = max_buffer

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        # If we have a complete section, strip it
        if _TOOL_CALL_SECTION_START in self._buffer and _TOOL_CALL_SECTION_END in self._buffer:
            self._buffer = _strip_tool_call_artifacts(self._buffer)
        # If no start token and buffer is large, yield everything
        if _TOOL_CALL_SECTION_START not in self._buffer and len(self._buffer) > self._max_buffer:
            out = self._buffer
            self._buffer = ""
            return out
        # If we see start but not end, keep buffering (up to max)
        if _TOOL_CALL_SECTION_START in self._buffer and _TOOL_CALL_SECTION_END not in self._buffer:
            # Check if we've exceeded max buffer waiting for end
            if len(self._buffer) > self._max_buffer:
                # Too long — strip any start token and yield rest
                self._buffer = self._buffer.replace(_TOOL_CALL_SECTION_START, "")
                out = self._buffer
                self._buffer = ""
                return out
            return ""
        # No special tokens — yield the buffer
        out = self._buffer
        self._buffer = ""
        return out

    def flush(self) -> str:
        out = _strip_tool_call_artifacts(self._buffer)
        self._buffer = ""
        return out


async def yield_chat_stream(
    *,
    provider: str,
    messages: Sequence[ChatCompletionMessageParam],
    model: str | None = None,
    max_tokens: int | None = None,
) -> tuple[AsyncIterator[str], asyncio.Future[str | None]]:
    chat_model = model or os.getenv("CHAT_MODEL", DEFAULT_CHAT_MODEL)
    chat_messages = list(messages)

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    loop = asyncio.get_running_loop()
    finish_future: asyncio.Future[str | None] = loop.create_future()

    if not openrouter_key and not openai_key:
        logger.error("Missing provider keys for chat streaming.")
        async def _empty():
            yield "[System Error: Missing provider keys]"
            finish_future.set_result("error")
        return _empty(), finish_future

    if openrouter_key:
        async def _stream_openrouter():
            finish_reason: str | None = None
            tcf = _ToolCallFilter()
            try:
                or_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key)
                stream = await or_client.chat.completions.create(
                    model=chat_model,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    choice = chunk.choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    content = delta.content
                    if content:
                        filtered = tcf.feed(content)
                        if filtered:
                            yield filtered
                final = tcf.flush()
                if final:
                    yield final
                finish_future.set_result(finish_reason)
                return
            except Exception as exc:
                logger.warning("OpenRouter stream failed: %s", exc)
                if not openai_key:
                    finish_future.set_result("error")
                    yield "[System Error: OpenRouter stream failed]"
                    return
        return _stream_openrouter(), finish_future

    if openai_key:
        async def _stream_openai():
            finish_reason: str | None = None
            tcf = _ToolCallFilter()
            try:
                fallback_model = "gpt-4o-mini"
                oa_client = AsyncOpenAI(api_key=openai_key)
                stream = await oa_client.chat.completions.create(
                    model=fallback_model,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    choice = chunk.choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    content = delta.content
                    if content:
                        filtered = tcf.feed(content)
                        if filtered:
                            yield filtered
                final = tcf.flush()
                if final:
                    yield final
                finish_future.set_result(finish_reason)
                return
            except Exception as exc:
                logger.warning("OpenAI fallback stream failed: %s", exc)
                finish_future.set_result("error")
                yield "[System Error: Stream failed]"
                return
        return _stream_openai(), finish_future

    async def _fallback():
        yield "[System Error: Missing provider keys]"
        finish_future.set_result("error")

    return _fallback(), finish_future


async def enrich_turn(
    *,
    entity: str,
    user_message: str,
    assistant_reply: str,
    metadata: Mapping[str, Any] | None = None,
    precomputed_appraisal: Mapping[str, Any] | None = None,
    ledger: Any = None,
    substrate: Any = None,
    store: Any = None,
    retrieved_keys: List[Any] | None = None, 
    retrieval_payload: Mapping[str, Any] | list[Any] | None = None,
    run_guardian: bool = True,
    persist_transcript: bool = True,
) -> Dict[str, Any]:
    
    logger.info(f"📚 LIBRARIAN ENGAGED for Entity: {entity}")

    def _merge_tree(existing: Any, new_tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return merge_knowledge_trees(existing, new_tree, limit=KNOWLEDGE_TREE_LIMIT)

    default_appraisal = {"score": None, "drift": None, "law_score": None, "grace_score": None}
    meta_payload: Dict[str, Any] = dict(metadata) if metadata else {}
    meta_payload["appraisal"] = dict(default_appraisal)
    if isinstance(metadata, Mapping) and isinstance(metadata.get("appraisal"), Mapping):
        meta_payload["appraisal"].update(metadata["appraisal"])
    if isinstance(precomputed_appraisal, Mapping):
        meta_payload["appraisal"].update(precomputed_appraisal)
        run_guardian = False

    normalized_tree: list[dict[str, Any]] = []
    for item in retrieved_keys or []:
        normalized_tree.append(normalize_knowledge_tree_item(item))

    existing_tree = meta_payload.get("knowledge_tree")
    if normalized_tree:
        meta_payload["knowledge_tree"] = _merge_tree(existing_tree, normalized_tree)
        logger.info(f"🌳 KNOWLEDGE TREE PRESERVED: {len(normalized_tree)} keys")

    if isinstance(meta_payload.get("researcher"), Mapping):
        researcher_meta = dict(cast(Mapping[str, Any], meta_payload.get("researcher")))
    else:
        researcher_meta = {}

    def _build_ethics_for_researcher(value: Any) -> dict[str, float] | None:
        if not isinstance(value, Mapping):
            return None
        ethics_payload: dict[str, float] = {}
        for key in ("score", "drift", "law_score", "grace_score"):
            item = value.get(key)
            if isinstance(item, (int, float)):
                ethics_payload[key] = float(item)
        return ethics_payload or None

    existing_ethics = _build_ethics_for_researcher(researcher_meta.get("ethics_for_researcher"))
    appraisal_ethics = _build_ethics_for_researcher(meta_payload.get("appraisal"))
    if existing_ethics:
        researcher_meta["ethics_for_researcher"] = existing_ethics
    elif appraisal_ethics:
        researcher_meta["ethics_for_researcher"] = appraisal_ethics

    eq6_commit_allowed = meta_payload.get("eq6_commit_allowed")
    eq6_lawfulness_level = meta_payload.get("eq6_lawfulness_level")
    eq6_mediator_prime = meta_payload.get("eq6_mediator_prime")
    if (
        not run_guardian
        and retrieval_payload is not None
        and assistant_reply
        and eq6_commit_allowed is None
        and eq6_lawfulness_level is None
        and eq6_mediator_prime is None
    ):
        eq6_result = equation_6_operational(
            query_text=assistant_reply,
            retrieval_payload=retrieval_payload,
        )
        eq6_commit_allowed = bool(eq6_result.get("commit_allowed"))
        eq6_lawfulness_level = int(eq6_result.get("lawfulness_level") or 0)
        eq6_mediator_prime = int(eq6_result.get("mediator_prime") or 0)
        meta_payload["eq6_commit_allowed"] = eq6_commit_allowed
        meta_payload["eq6_lawfulness_level"] = eq6_lawfulness_level
        meta_payload["eq6_mediator_prime"] = eq6_mediator_prime
        logger.info(
            f"Eq6 gate: commit={eq6_commit_allowed} law={eq6_lawfulness_level} mediator={eq6_mediator_prime}"
        )

    if meta_payload.get("knowledge_tree"):
        researcher_tree = researcher_meta.get("knowledge_tree")
        merged_tree = _merge_tree(researcher_tree, meta_payload["knowledge_tree"])
        researcher_meta["knowledge_tree"] = merged_tree

    if researcher_meta:
        meta_payload["researcher"] = researcher_meta

    if not persist_transcript:
        transcript_keys = {"user_message", "assistant_reply", "content", "reply_text", "full_text"}

        def _redact_transcript_fields(value: Any) -> Any:
            if isinstance(value, Mapping):
                redacted: dict[str, Any] = {}
                for key, item in value.items():
                    if isinstance(key, str) and key in transcript_keys:
                        redacted[key] = ""
                    else:
                        redacted[key] = _redact_transcript_fields(item)
                return redacted
            if isinstance(value, list):
                return [_redact_transcript_fields(item) for item in value]
            if isinstance(value, tuple):
                return tuple(_redact_transcript_fields(item) for item in value)
            return value

        meta_payload = cast(Dict[str, Any], _redact_transcript_fields(meta_payload))

    if persist_transcript:
        meta_payload["user_message"] = user_message
        meta_payload["assistant_reply"] = assistant_reply
    meta_payload["role"] = "assistant"
    meta_payload["content"] = assistant_reply if persist_transcript else ""
    meta_payload["transcript_persisted"] = bool(persist_transcript)
    meta_payload.setdefault("kind", "chat")

    def _coerce_confidence(value: Any, default: float = 1.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _normalize_topic_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, Mapping):
            return [str(item) for item in value if str(item)]
        return [str(value)]

    def _build_eq6_retrieval_skim(
        payload: Mapping[str, Any] | list[Any] | None,
    ) -> Mapping[str, Any] | list[Any] | None:
        if payload is None:
            return None

        def _trim_mapping(item: Mapping[str, Any]) -> dict[str, Any] | None:
            trimmed: dict[str, Any] = {}
            for key in ("skim", "summary", "text", "content", "body", "preview", "snippet"):
                if key in item:
                    trimmed[key] = item.get(key)
            payload_block = item.get("payload")
            if isinstance(payload_block, Mapping):
                payload_trim: dict[str, Any] = {}
                for key in ("segments", "parts", "blobs"):
                    if key in payload_block:
                        payload_trim[key] = payload_block.get(key)
                if payload_trim:
                    trimmed["payload"] = payload_trim
            return trimmed or None

        if isinstance(payload, Mapping):
            return _trim_mapping(payload)

        if isinstance(payload, list):
            trimmed_list: list[Any] = []
            for item in payload:
                if isinstance(item, Mapping):
                    trimmed = _trim_mapping(item)
                    if trimmed:
                        trimmed_list.append(trimmed)
                elif isinstance(item, str) and item.strip():
                    trimmed_list.append(item.strip())
            return trimmed_list or None

        return None

    def _derive_hysteresis_coherence() -> float | None:
        coherence_source = None
        appraisal = meta_payload.get("appraisal")
        if isinstance(appraisal, Mapping):
            score = appraisal.get("score")
            if isinstance(score, (int, float)):
                coherence_source = float(score)

        if not entity:
            return coherence_source

        try:
            engine = get_entity_engine(entity)
            if coherence_source is not None:
                engine.update_memory(coherence_source)
            return engine.calculate_memory_coherence()
        except Exception:
            logger.debug("Failed to derive hysteresis coherence", exc_info=True)
            return coherence_source

    def _infer_intent_local(user_text: str, reply_text: str) -> str:
        combined = f"{user_text} {reply_text}".lower()
        if any(keyword in combined for keyword in ("search", "find", "lookup")):
            return "search"
        if any(keyword in combined for keyword in ("summary", "summarize", "summarise", "tl;dr")):
            return "summarize"
        if any(keyword in combined for keyword in ("plan", "next steps", "roadmap")):
            return "plan"
        if "cite" in combined or "citation" in combined:
            return "cite"
        if "?" in user_text:
            return "answer"
        return "respond"

    # --- LIBRARIAN FALLBACK ENRICHMENT ---
    researcher_block = dict(meta_payload.get("researcher") or {})
    existing_topics = _normalize_topic_list(
        researcher_block.get("topics") or researcher_block.get("tags") or meta_payload.get("topics") or []
    )
    existing_intent = researcher_block.get("intent") or meta_payload.get("intent")
    confidence = _coerce_confidence(researcher_block.get("confidence") or meta_payload.get("confidence"))

    norm = normalise_text(assistant_reply or "")
    fallback_needed = not existing_topics or not existing_intent or confidence < 0.5
    if fallback_needed:
        fallback_topics = _normalize_topic_list(norm.get("topics", [])) or existing_topics
        inferred_intent = _infer_intent_local(user_message, assistant_reply)

        merged_topics: list[str] = []
        for topic in (existing_topics or []) + (fallback_topics or []):
            if topic and topic not in merged_topics:
                merged_topics.append(topic)

        researcher_block["topics"] = merged_topics or fallback_topics
        researcher_block["tags"] = researcher_block.get("tags") or merged_topics or fallback_topics
        researcher_block["intent"] = existing_intent or inferred_intent
        researcher_block["confidence"] = 1.0
        researcher_block["enrichment_source"] = "librarian_fallback"

    # Build final metadata ensuring compatibility with downstream consumers.
    final_metadata = dict(meta_payload)
    if researcher_block:
        final_metadata["researcher"] = researcher_block
    if researcher_block.get("topics") and not final_metadata.get("topics"):
        final_metadata["topics"] = researcher_block["topics"]
    if researcher_block.get("intent") and not final_metadata.get("intent"):
        final_metadata["intent"] = researcher_block["intent"]
    if researcher_block.get("confidence") and not final_metadata.get("confidence"):
        final_metadata["confidence"] = researcher_block["confidence"]
    if researcher_block.get("enrichment_source") and not final_metadata.get("enrichment_source"):
        final_metadata["enrichment_source"] = researcher_block["enrichment_source"]
    if not final_metadata.get("claims"):
        claims = norm.get("quotes")
        if isinstance(claims, list) and claims:
            final_metadata["claims"] = claims

    clean_metadata = {
        key: value
        for key, value in final_metadata.items()
        if key not in {"role", "content", "kind", "assistant_reply"}
    }

    if ledger:
        try: ledger.update_S2(entity, {"11": {"metadata": {"appraisal": meta_payload["appraisal"]}}})
        except: logger.debug("Failed to update S2 appraisal state", exc_info=True)

    record_result: Dict[str, Any] | None = None
    if substrate and ledger:
        eq6_retrieval_skim = _build_eq6_retrieval_skim(retrieval_payload)
        hop_lawfulness = None
        hop_lawfulness_value = clean_metadata.get("hop_lawfulness")
        if isinstance(hop_lawfulness_value, list):
            hop_lawfulness = [item for item in hop_lawfulness_value if isinstance(item, int)]
        hysteresis_coherence = _derive_hysteresis_coherence()
        try:
            record_result = record_message(
                entity=entity,
                role="assistant",
                content=assistant_reply,
                kind=str(final_metadata.get("kind", "chat")),
                metadata=clean_metadata,
                substrate=substrate,
                ledger=ledger,
                store=store,
                retrieval_payload=eq6_retrieval_skim,
                draft_text=assistant_reply if persist_transcript else None,
                persist_content=persist_transcript,
                hysteresis_coherence=hysteresis_coherence,
                eq6_lawfulness_level=eq6_lawfulness_level,
                hop_lawfulness=hop_lawfulness,
                eq6_mediator_prime=eq6_mediator_prime,
            )
        except Exception:
            logger.exception("record_message failed for assistant reply")

    coordinate = None
    flow_enrich = None
    if record_result:
        coordinate = record_result.get("coordinate")
        flow_enrich = record_result.get("flow_enrich")
        recorded_meta = record_result.get("metadata")
        if isinstance(recorded_meta, Mapping):
            for key in (
                CIU_FACTORS,
                CIU_KERNEL_EXPONENTS,
                CIU_MMF_PROJECTIONS,
                CIU_ENTRY_CLASS,
                CIU_FLOW_RULE_TAGS,
                CIU_RELATIONSHIP_LINKS,
                "token_primes",
                "token_prime_product",
                "prime_multiplicative_value",
                "prime_lattice_exponents",
                "p_adic_write_cost",
            ):
                if key in recorded_meta:
                    final_metadata[key] = recorded_meta[key]
    logger.info(f"📤 LIBRARIAN RETURNING: Coordinate={coordinate}")

    if run_guardian and ledger and substrate:
        try:
            await guardian_enrich_turn(
                entity=entity,
                user_message=user_message,
                assistant_reply=assistant_reply,
                retrieval_payload=retrieval_payload,
                draft_text=assistant_reply,
                ledger=ledger,
                substrate=substrate,
                store=store,
                dry_run=not persist_transcript,
            )
        except Exception:
            logger.exception("Guardian enrichment failed")

    return {
        "coordinate": coordinate,
        "metadata": final_metadata,
        "knowledge_tree": normalized_tree,
        "flow_enrich": flow_enrich,
    }

__all__ = [
    "MemoryService",
    "MemoryCandidate",
    "fuzzy_retrieve",
    "p_adic_distance",
    "assemble_context",
    "build_chat_messages",
    "complete_chat",
    "yield_chat_stream",
    "enrich_turn",
]

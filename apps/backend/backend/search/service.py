"""Search helpers for ledger entries indexed by token primes."""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Iterable, List, Sequence

from backend.config import settings as _settings
from backend.config.settings import qp_pure_enabled
from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import qp_coordinate_distance
from backend.fieldx_kernel.qp_retrieval import (
    derive_query_coordinate_from_primes,
    extract_qp_coordinate,
    qp_pure_compatible,
)
from backend.fieldx_kernel.substrate.ledger_store_v2 import _collect_text_fragments
from backend.search.token_index import TokenPrimeIndex, normalise_tokens


logger = logging.getLogger(__name__)

# Strict Stopwords to improve "River Bank" quality
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "of", "on", "or", "that", "the", "to", "was",
    "were", "with", "it", "this", "but", "they", "have", "had", "what",
    "how", "can", "do", "does", "did", "why"
}


def _preview_text(text: str, limit: int = 160) -> str:
    """Return a short, single-line preview for result snippets."""
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def _load_index_entries(index: TokenPrimeIndex, prime: int) -> set[str]:
    """Return entry identifiers associated with ``prime`` from the inverted index."""
    raw = index.db.get(index._prime_key(prime))
    if raw is None:
        return set()

    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        payload = json.loads(decoded)
        return {str(item) for item in payload}
    except (TypeError, json.JSONDecodeError):
        return set()


def search_by_primes(
    primes: Sequence[int], index: TokenPrimeIndex, mode: str = "any"
) -> List[str]:
    """Collect candidate entry identifiers for the provided ``primes``."""
    cleaned_mode = (mode or "any").strip().lower()
    if cleaned_mode not in {"any", "all"}:
        raise ValueError("mode must be 'any' or 'all'")

    postings: list[set[str]] = []
    for prime in primes:
        entries = _load_index_entries(index, int(prime))
        if entries:
            postings.append(entries)

    if not postings:
        return []

    if cleaned_mode == "all":
        candidates = set.intersection(*postings)
    else:
        candidates = set().union(*postings)

    return sorted(candidates)


def _combine_text_fragments(metadata: dict | None) -> str:
    if not metadata:
        return ""
    if full_text := metadata.get("full_text"):
        return str(full_text)
    fragments: Iterable[str] = _collect_text_fragments(metadata)
    return " ".join(str(fragment) for fragment in fragments)


def full_text_score(text: str, tokens: Sequence[str]) -> tuple[float, str]:
    """Return a tuple of ``(score, snippet)`` for ``text`` against ``tokens``."""
    if not text:
        return 0.0, ""

    cleaned_tokens = [token for token in tokens if token]
    if not cleaned_tokens:
        return 0.0, ""

    lowered_text = text.lower()
    score = float(sum(lowered_text.count(token) for token in cleaned_tokens))
    
    if score == 0:
        return 0.0, ""

    # Snippet generation
    first_hit = None
    for token in cleaned_tokens:
        position = lowered_text.find(token)
        if position != -1 and (first_hit is None or position < first_hit):
            first_hit = position

    window = 60
    if first_hit is None:
        snippet = text[:200].strip()
    else:
        start = max(0, first_hit - window)
        end = min(len(text), first_hit + window)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."

    return score, snippet


def search(
    query: str,
    *,
    store,
    token_index: TokenPrimeIndex,
    mode: str = "any",
    limit: int = 50,
) -> List[dict]:
    """
    Search with Strict Prime Intersection enforcement (The River Banks).
    """
    raw_tokens = normalise_tokens(query)
    tokens = [token for token in raw_tokens if token and token not in STOPWORDS]

    if not tokens:
        return []

    # 1. Get Query Primes (Law)
    token_primes = token_index.primes_for_tokens(tokens)
    query_lattice = PrimeLatticeState.from_primes(token_primes)

    # Pure Qp query coordinate: derived from kernel token primes, if any.
    query_coord = derive_query_coordinate_from_primes(token_primes)
    qp_pure_active = bool(qp_pure_enabled() and query_coord is not None)

    # 2. Get Candidates
    candidate_ids = search_by_primes(token_primes, token_index, mode="any")
    keyword_weights = token_index.keyword_weights_for_primes(token_primes)
    mode_multiplier = {
        2: 1.15,
        11: 1.15,
        3: 1.1,
        13: 1.1,
        5: 1.1,
        17: 1.1,
        7: 1.15,
        19: 1.15,
    }

    def _entry_lattice(entry) -> PrimeLatticeState | None:
        """Build the candidate's prime lattice from persisted metadata or text."""
        metadata = entry.state.metadata or {}
        lattice_exponents = metadata.get("prime_lattice_exponents")
        if isinstance(lattice_exponents, dict):
            return PrimeLatticeState(lattice_exponents)

        entry_primes = metadata.get("token_primes", [])
        if entry_primes:
            return PrimeLatticeState.from_primes(entry_primes)

        text = _combine_text_fragments(metadata)
        if text:
            entry_tokens = normalise_tokens(text)
            return PrimeLatticeState.from_primes(token_index.primes_for_tokens(entry_tokens))

        return None

    def _score_entry(entry, entry_id: str, pure_mode: bool) -> dict | None:
        """Return a scored result dict, or None if the entry should be skipped."""
        metadata = entry.state.metadata or {}
        text = _combine_text_fragments(metadata)

        entry_lattice = _entry_lattice(entry)
        if entry_lattice is None:
            return None

        # Orthogonal (coprime) entries share no concept primes -> discard.
        if entry_lattice.is_orthogonal_to(query_lattice):
            return None

        # Lattice meet = shared primes (greatest common divisor).
        meet = query_lattice.meet(entry_lattice)
        overlap = len(meet.exponents)

        if pure_mode:
            candidate_coord = extract_qp_coordinate(metadata)
            if candidate_coord is None:
                return None
            if not qp_pure_compatible(query_coord, candidate_coord):  # type: ignore[arg-type]
                return None
            try:
                distance = float(qp_coordinate_distance(query_coord, candidate_coord))  # type: ignore[arg-type]
            except Exception as exc:
                logger.debug("Qp pure search skipped candidate: %s", exc)
                return None
            final_score = float(qp_score(distance, query_coord.metric_prime, query_coord.working_precision))  # type: ignore[union-attr]
            _, snippet = full_text_score(text, tokens)
            return {
                "entry": {
                    "key": {
                        "namespace": entry.key.namespace,
                        "identifier": entry.key.identifier,
                    },
                    "state": {
                        "coordinates": entry.state.coordinates,
                        "phase": entry.state.phase,
                        "metadata": entry.state.metadata,
                    },
                    "created_at": entry.created_at.isoformat(),
                    "notes": entry.notes,
                },
                "score": final_score,
                "snippet": snippet,
                "entry_id": entry_id,
                "p_adic_overlap": overlap,
                "qp_distance": distance,
                "qp_score": final_score,
                "qp_pure": True,
            }

        # Legacy mixed-signal scoring.
        score, snippet = full_text_score(text, tokens)

        # Bonus: Boost score based on number of intersecting primes
        # This rewards memories that match multiple concepts (High GCD)
        prime_boost = overlap * 1.5
        keyword_bonus = 0.0
        for prime in token_primes:
            weights = keyword_weights.get(prime)
            if not weights:
                continue
            weight = weights.get(entry_id)
            if weight is None:
                continue
            keyword_bonus += float(weight) * mode_multiplier.get(int(prime), 1.0)

        cons_tokens: list[str] = []
        for key in ("claims", "topics", "tags"):
            value = metadata.get(key)
            if isinstance(value, str):
                cons_tokens.extend(normalise_tokens(value))
            elif isinstance(value, list):
                cons_tokens.extend(normalise_tokens(" ".join(str(item) for item in value)))
        cons_overlap = len(set(cons_tokens).intersection(tokens))
        cons_ratio = cons_overlap / max(len(tokens), 1)
        cons_bonus = 0.3 * cons_ratio

        gravity_cost = float(metadata.get("gravity_cost", 0.0) or 0.0)
        gravity_penalty = 0.1 * math.log1p(gravity_cost) if gravity_cost > 0 else 0.0

        final_score = score + prime_boost + keyword_bonus + cons_bonus - gravity_penalty

        return {
            "entry": {
                "key": {
                    "namespace": entry.key.namespace,
                    "identifier": entry.key.identifier,
                },
                "state": {
                    "coordinates": entry.state.coordinates,
                    "phase": entry.state.phase,
                    "metadata": entry.state.metadata,
                },
                "created_at": entry.created_at.isoformat(),
                "notes": entry.notes,
            },
            "score": final_score,
            "snippet": snippet,
            "entry_id": entry_id,
            "p_adic_overlap": overlap,
        }

    results: list[dict] = []

    for entry_id in candidate_ids:
        entry = store.read(entry_id)
        if entry is None:
            continue
        scored = _score_entry(entry, entry_id, pure_mode=qp_pure_active)
        if scored is not None:
            results.append(scored)

    # If the pure Qp path produced no results, fall back to the legacy ranker
    # so callers always get useful context.
    if qp_pure_active and not results:
        for entry_id in candidate_ids:
            entry = store.read(entry_id)
            if entry is None:
                continue
            scored = _score_entry(entry, entry_id, pure_mode=False)
            if scored is not None:
                scored["qp_pure_fallback"] = True
                results.append(scored)

    # 3. Rank and Trim
    ranked = sorted(results, key=lambda row: row["score"], reverse=True)
    return ranked[:limit]


def list_recent_entries(
    store,
    *,
    entity: str | None,
    limit: int = 50,
    namespace_filter: list[str] | None = None,
    namespace_mode: str = "any",
) -> list[dict]:
    """Return the most recent ledger entries. If ``entity`` is None, scan all namespaces."""
    ns_filter = namespace_filter or []
    ns_mode = (namespace_mode or "any").lower()

    def _namespace_allowed(ns: str | None) -> bool:
        if entity and ns != entity:
            return False
        if not ns_filter:
            return True
        if ns_mode == "any":
            return ns in ns_filter
        if ns_mode == "none":
            return ns not in ns_filter
        if ns_mode == "only":
            return ns in ns_filter
        return True

    try:
        with store._lock:
            snapshots = list(store._db.items())
    except Exception:
        return []

    entries = []
    for raw_key, raw_entry in snapshots:
        try:
            entry_id = raw_key.decode() if isinstance(raw_key, (bytes, bytearray)) else str(raw_key)
            entry = store._decode(raw_entry)
            ns = entry.key.namespace
            if not _namespace_allowed(ns):
                continue

            snippet_source = _combine_text_fragments(entry.state.metadata)
            snippet = _preview_text(snippet_source)
            
            entries.append({
                "entry": {
                    "key": {"namespace": entry.key.namespace, "identifier": entry.key.identifier},
                    "created_at": entry.created_at.isoformat(),
                    "notes": entry.notes,
                },
                "snippet": snippet,
                "entry_id": entry_id,
                "created_ts": entry.created_at.timestamp()
            })
        except:
            continue

    entries.sort(key=lambda x: x["created_ts"], reverse=True)
    return entries[:limit]


# Legacy fallback placeholder
def _scan_all_entries(store, tokens: Sequence[str], *, limit: int) -> list[dict]:
    return []

__all__ = [
    "full_text_score",
    "list_recent_entries",
    "search",
    "search_by_primes",
]

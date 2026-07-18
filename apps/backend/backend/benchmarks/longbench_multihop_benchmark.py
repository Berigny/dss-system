"""LongBench-style multi-hop QA retrieval benchmark.

This harness tests the claim that circulation-hysteresis coherence helps multi-hop
reasoning.  Each query requires combining facts from a chain of memories.  Qp
routing uses dual-overlay and circulation-pass filters to keep the chain together;
the vector-RAG baseline ranks by bag-of-words cosine and is expected to scatter
across keyword-overlapping distractor chains.

Because the backend does not include an LLM, the "reasoning" step is evaluated as
retrieval of the complete evidence chain: a query is counted as answerable when
all required memories appear in the top-k results.

Metrics
-------
- chain_recall@k per query
- chain_precision@k per query
- full_chain_recovery rate
- paired permutation-test p-value on per-query chain_recall@k differences

Output
------
A validated ``BenchmarkArtifact`` is written under
``backend/benchmarks/output/longbench_multihop/``.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    _DUAL_COMPLEMENT,
    _METRIC_PRIME,
    _NODE_DIGIT,
    _TETRAHEDRON,
    _coordinate_hash,
    qp_coordinate_distance,
)
from backend.fieldx_kernel.qp_retrieval import qp_pure_compatible
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "longbench_multihop"
DEFAULT_CHAIN_COUNT = 9
DEFAULT_CHAIN_LENGTH = 3
DEFAULT_TOP_K = 5
DEFAULT_PERMUTATIONS = 5_000
DEFAULT_SEED = 193
ALPHA = 0.05
WORKING_PRECISION = 16
KERNEL_NODES = ("Eq0", "Eq1", "Eq2", "Eq3", "Eq4", "Eq5", "Eq6", "Eq7", "Eq8", "Eq9")


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    chain_count: int
    chain_length: int
    top_k: int
    permutations: int
    seed: int


@dataclass(frozen=True)
class Memory:
    memory_id: str
    text: str
    coordinate: QpCoordinate
    chain_id: str
    hop: int


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    coordinate: QpCoordinate
    chain_id: str
    required_ids: frozenset[str]


@dataclass(frozen=True)
class PerQueryResult:
    query_id: str
    chain_id: str
    qp_chain_recall: float
    vector_chain_recall: float
    qp_chain_precision: float
    vector_chain_precision: float
    qp_full_chain: bool
    vector_full_chain: bool


@dataclass(frozen=True)
class BenchmarkSummary:
    queries: int
    qp_chain_recall: float
    vector_chain_recall: float
    qp_chain_precision: float
    vector_chain_precision: float
    qp_full_chain_rate: float
    vector_full_chain_rate: float
    p_value: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "qp_chain_recall": self.qp_chain_recall,
            "vector_chain_recall": self.vector_chain_recall,
            "qp_chain_precision": self.qp_chain_precision,
            "vector_chain_precision": self.vector_chain_precision,
            "qp_full_chain_rate": self.qp_full_chain_rate,
            "vector_full_chain_rate": self.vector_full_chain_rate,
            "p_value": self.p_value,
        }


# -----------------------------------------------------------------------------
# Coordinate construction
# -----------------------------------------------------------------------------


def _make_coordinate(
    *,
    kernel_node: str,
    valuation_offset: int,
    circulation_pass: int = 0,
    hysteresis_depth: float = 0.0,
    dual_valid: bool | None = None,
) -> QpCoordinate:
    """Build a QpCoordinate with controlled depth, pass, and dual state."""
    from backend.fieldx_kernel.qp_arithmetic import QpElement

    metric_prime = _METRIC_PRIME[kernel_node]
    digit = _NODE_DIGIT[kernel_node]
    unit_digits = tuple(digit for _ in range(valuation_offset))
    coordinate_id = _coordinate_hash(metric_prime, valuation_offset, unit_digits)

    rational_value = metric_prime**valuation_offset if valuation_offset >= 0 else 0
    rational_representative = QpElement.from_int(
        metric_prime, rational_value, working_precision=WORKING_PRECISION
    )

    dual_state: QpCoordinate | None = None
    if dual_valid is not None:
        dual_node = _DUAL_COMPLEMENT[kernel_node]
        if not dual_valid:
            dual_node = "Eq7" if dual_node != "Eq7" else "Eq6"
        dual_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME[dual_node],
            working_precision=WORKING_PRECISION,
            kernel_node=dual_node,
        )

    mediator_state: QpCoordinate | None = None
    tetra = _TETRAHEDRON.get(kernel_node, "S1")
    if tetra == "S1":
        mediator_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME["Eq8"],
            working_precision=WORKING_PRECISION,
            kernel_node="Eq8",
        )
    elif tetra == "S2":
        mediator_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME["Eq9"],
            working_precision=WORKING_PRECISION,
            kernel_node="Eq9",
        )

    return QpCoordinate(
        coordinate_id=coordinate_id,
        kernel_node=kernel_node,
        metric_prime=metric_prime,
        tetrahedron=tetra,
        dual_complement=_DUAL_COMPLEMENT[kernel_node],
        unit_digits=unit_digits,
        valuation_offset=valuation_offset,
        working_precision=WORKING_PRECISION,
        rational_representative=rational_representative,
        circulation_pass=circulation_pass,
        hysteresis_depth=hysteresis_depth,
        dual_state=dual_state,
        mediator_state=mediator_state,
    )


# -----------------------------------------------------------------------------
# Synthetic multi-hop corpus
# -----------------------------------------------------------------------------


_ENTITY_BANKS: dict[str, list[str]] = {
    "person": [
        "Alice", "Bob", "Carol", "David", "Eva", "Frank", "Grace", "Henry",
        "Iris", "Jack", "Kate", "Liam", "Mia", "Noah", "Olivia", "Pete",
    ],
    "company": [
        "Acme", "Nebula", "Orbit", "Prism", "Quanta", "Radius", "Solara",
        "Terra", "Umbra", "Vortex", "Wisp", "Xylon", "Yotta", "Zenith",
    ],
    "city": [
        "Paris", "Tokyo", "Lagos", "Lima", "Oslo", "Dakar", "Sofia", "Riga",
        "Accra", "Baku", "Cebu", "Doha", "Essen", "Fez", "Giza", "Hanoi",
    ],
    "industry": [
        "fashion", "finance", "biotech", "robotics", "energy", "logistics",
        "media", "agritech", "aerospace", "nanotech", "cleantech", "edtech",
    ],
    "animal": [
        "wolf", "falcon", "turtle", "octopus", "lynx", "crane", "badger",
        "ibex", "koala", "moose", "newt", "okapi", "puma", "quokka", "raven",
    ],
    "habitat": [
        "tundra", "reef", "savanna", "wetland", "taiga", "estuary", "prairie",
        "fjord", "marsh", "canyon", "steppe", "bog", "dune", "meadow", "grove",
    ],
    "food": [
        "berries", "fish", "nectar", "roots", "seeds", "insects", "kelp",
        "moss", "fungi", "larvae", "shoots", "algae", "plankton", "fruit",
    ],
    "scientist": [
        "Ivan", "Jun", "Kara", "Leo", "Mira", "Nico", "Opal", "Paz",
        "Quinn", "Ravi", "Sage", "Tara", "Uma", "Vince", "Wren", "Xara",
    ],
    "field": [
        "topology", "genomics", "cryptography", "optics", "thermodynamics",
        "ecology", "linguistics", "neuroscience", "robotics", "meteorology",
    ],
    "discovery": [
        "a new invariant", "a folding protein", "a secure protocol",
        "a coherent beam", "a heat engine", "a keystone species",
        "a universal grammar", "a memory circuit", "a gait pattern",
    ],
    "book": [
        "The Silent Reef", "Glass Horizon", "Mapped Stars", "Dust Algorithm",
        "Salt Garden", "Iron Lullaby", "Paper Meridian", "Carbon Choir",
    ],
    "author": [
        "Maya Lin", "Orhan Vee", "Pilar Yu", "Roland Zed", "Suki Ash",
        "Tomas Bex", "Uma Cress", "Vera Dorn", "Wes Eban", "Xia Ford",
    ],
    "country": [
        "Estonia", "Ghana", "Iceland", "Japan", "Kenya", "Laos", "Malta",
        "Nepal", "Oman", "Peru", "Qatar", "Samoa", "Togo", "Vanuatu",
    ],
    "river": [
        "Nile", "Danube", "Mekong", "Ganges", "Yukon", "Orange", "Congo",
        "Volga", "Zambezi", "Loire", "Tagus", "Murray", "Paraná", "Limpopo",
    ],
    "continent": [
        "Africa", "Asia", "Europe", "South America", "North America",
        "Oceania", "Antarctica",
    ],
}

_TEMPLATE_TYPES: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str]] = [
    # (slot names for memory 0, slot names for memory 1, slot names for memory 2, question template)
    (("person", "company"), ("company", "city"), ("city", "industry"),
     "What industry is {person} connected to through work?"),
    (("animal", "habitat"), ("habitat", "food"), ("food", "continent"),
     "On which continent does the {animal} find its typical food source?"),
    (("scientist", "field"), ("field", "discovery"), ("discovery", "country"),
     "In which country did the work of {scientist} lead to a major finding?"),
    (("book", "author"), ("author", "country"), ("country", "river"),
     "Which river is associated with the country of the author of {book}?"),
    (("river", "country"), ("country", "city"), ("city", "company"),
     "Which company is based in the city in the country through which the {river} flows?"),
]


def _slot_text(template: tuple[str, ...], entities: dict[str, str]) -> str:
    if len(template) == 2 and template[0] == "person" and template[1] == "company":
        return f"{entities['person']} works at {entities['company']}."
    if len(template) == 2 and template[0] == "company" and template[1] == "city":
        return f"{entities['company']} is headquartered in {entities['city']}."
    if len(template) == 2 and template[0] == "city" and template[1] == "industry":
        return f"{entities['city']} is known for its {entities['industry']} sector."
    if len(template) == 2 and template[0] == "animal" and template[1] == "habitat":
        return f"The {entities['animal']} lives in the {entities['habitat']}."
    if len(template) == 2 and template[0] == "habitat" and template[1] == "food":
        return f"The {entities['habitat']} provides abundant {entities['food']}."
    if len(template) == 2 and template[0] == "food" and template[1] == "continent":
        return f"{entities['food'].capitalize()} are commonly found in {entities['continent']}."
    if len(template) == 2 and template[0] == "scientist" and template[1] == "field":
        return f"{entities['scientist']} works in {entities['field']}."
    if len(template) == 2 and template[0] == "field" and template[1] == "discovery":
        return f"Research in {entities['field']} led to {entities['discovery']}."
    if len(template) == 2 and template[0] == "discovery" and template[1] == "country":
        return f"{entities['discovery'].capitalize()} was first confirmed in {entities['country']}."
    if len(template) == 2 and template[0] == "book" and template[1] == "author":
        return f"{entities['book']} was written by {entities['author']}."
    if len(template) == 2 and template[0] == "author" and template[1] == "country":
        return f"{entities['author']} was born in {entities['country']}."
    if len(template) == 2 and template[0] == "country" and template[1] == "river":
        return f"The longest river in {entities['country']} is the {entities['river']}."
    if len(template) == 2 and template[0] == "river" and template[1] == "country":
        return f"The {entities['river']} flows through {entities['country']}."
    if len(template) == 2 and template[0] == "country" and template[1] == "city":
        return f"The capital of {entities['country']} is {entities['city']}."
    if len(template) == 2 and template[0] == "city" and template[1] == "company":
        return f"{entities['company']} has its main office in {entities['city']}."
    raise ValueError(f"unsupported template: {template}")


def _build_chain(
    chain_id: str,
    template: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str],
    entity_offset: int,
    circulation_pass: int,
    kernel_node: str,
) -> tuple[list[Memory], Query]:
    """Build one target chain and its multi-hop query."""
    slot_templates, question_template = template[:3], template[3]
    slots = tuple(sorted(set(slot for step in slot_templates for slot in step)))

    entities: dict[str, str] = {}
    for slot in slots:
        bank = _ENTITY_BANKS[slot]
        idx = (entity_offset + hash(chain_id + slot)) % len(bank)
        entities[slot] = bank[idx]

    memories: list[Memory] = []
    for hop, step_template in enumerate(slot_templates):
        text = _slot_text(step_template, entities)
        coord = _make_coordinate(
            kernel_node=kernel_node,
            valuation_offset=hop + 1,
            circulation_pass=circulation_pass,
            hysteresis_depth=round(circulation_pass * 0.1, 2),
            dual_valid=True,
        )
        memories.append(
            Memory(
                memory_id=f"{chain_id}:m{hop}",
                text=text,
                coordinate=coord,
                chain_id=chain_id,
                hop=hop,
            )
        )

    query_text = question_template.format(**entities)
    query_coord = _make_coordinate(
        kernel_node=kernel_node,
        valuation_offset=len(slot_templates),
        circulation_pass=circulation_pass,
        hysteresis_depth=round(circulation_pass * 0.1, 2),
        dual_valid=True,
    )
    query = Query(
        query_id=f"{chain_id}:q",
        text=query_text,
        coordinate=query_coord,
        chain_id=chain_id,
        required_ids=frozenset(m.memory_id for m in memories),
    )
    return memories, query


def _build_confounder(
    *,
    confounder_id: str,
    target_template: tuple,
    target_entities: dict[str, str],
    shared_slot: str,
    entity_offset: int,
    circulation_pass: int,
    kernel_node: str,
) -> list[Memory]:
    """Build a distractor chain that shares one entity with the target chain."""
    slot_templates = target_template[:3]
    slots = tuple(sorted(set(slot for step in slot_templates for slot in step)))
    entities: dict[str, str] = {shared_slot: target_entities[shared_slot]}
    for slot in slots:
        if slot == shared_slot:
            continue
        bank = _ENTITY_BANKS[slot]
        idx = (entity_offset + hash(confounder_id + slot) + 7) % len(bank)
        entities[slot] = bank[idx]

    memories: list[Memory] = []
    for hop, step_template in enumerate(slot_templates):
        text = _slot_text(step_template, entities)
        coord = _make_coordinate(
            kernel_node=kernel_node,
            valuation_offset=hop + 1,
            circulation_pass=circulation_pass,
            hysteresis_depth=round(circulation_pass * 0.1, 2),
            dual_valid=True,
        )
        memories.append(
            Memory(
                memory_id=f"{confounder_id}:m{hop}",
                text=text,
                coordinate=coord,
                chain_id=confounder_id,
                hop=hop,
            )
        )
    return memories


def generate_corpus(
    chain_count: int = DEFAULT_CHAIN_COUNT,
    *,
    seed: int = DEFAULT_SEED,
) -> tuple[list[Memory], list[Query]]:
    """Generate a deterministic multi-hop corpus.

    Each target chain has ``chain_length`` memories and a multi-hop query.  A
    confounding chain that shares one intermediate entity is added for every
    target chain, plus a small set of unrelated distractors.  Confounders have a
    different circulation pass, so Qp filters them while vector similarity may
    rank them highly.
    """
    rng = random.Random(seed)

    memories: list[Memory] = []
    queries: list[Query] = []

    for i in range(chain_count):
        chain_id = f"chain{i}"
        template = _TEMPLATE_TYPES[i % len(_TEMPLATE_TYPES)]
        pass_idx = i + 2
        kernel_node = KERNEL_NODES[i % len(KERNEL_NODES)]

        chain_memories, query = _build_chain(
            chain_id=chain_id,
            template=template,
            entity_offset=i * 7,
            circulation_pass=pass_idx,
            kernel_node=kernel_node,
        )
        memories.extend(chain_memories)
        queries.append(query)

        # Confounder chain shares a middle entity (slot 1 of the bridge) with target.
        slots = tuple(sorted(set(slot for step in template[:3] for slot in step)))
        shared_slot = slots[1]
        confounder_id = f"chain{i}:conf"
        conf_kernel_node = KERNEL_NODES[(i + 1) % len(KERNEL_NODES)]
        conf_memories = _build_confounder(
            confounder_id=confounder_id,
            target_template=template,
            target_entities={
                slot: _ENTITY_BANKS[slot][
                    (i * 7 + hash(chain_id + slot)) % len(_ENTITY_BANKS[slot])
                ]
                for slot in slots
            },
            shared_slot=shared_slot,
            entity_offset=i * 7,
            circulation_pass=pass_idx + 10,
            kernel_node=conf_kernel_node,
        )
        memories.extend(conf_memories)

    # Random unrelated distractors with random coordinates.
    for j in range(chain_count * 2):
        coord = _make_coordinate(
            kernel_node=rng.choice(KERNEL_NODES),
            valuation_offset=rng.randint(1, 4),
            circulation_pass=rng.randint(0, chain_count + 10),
            hysteresis_depth=round(rng.uniform(0.0, 0.5), 2),
            dual_valid=None,
        )
        memories.append(
            Memory(
                memory_id=f"random{j}",
                text=f"The quick brown fox jumps over the lazy dog number {j}.",
                coordinate=coord,
                chain_id="random",
                hop=0,
            )
        )

    return memories, queries


# -----------------------------------------------------------------------------
# Baselines
# -----------------------------------------------------------------------------


class VectorRAGBaseline:
    """Deterministic bag-of-words cosine nearest-neighbour baseline."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)
        self._vocab = self._build_vocabulary()
        self._vectors = {m.memory_id: self._vectorize(m.text) for m in memories}

    def _build_vocabulary(self) -> dict[str, int]:
        vocab: set[str] = set()
        for memory in self._memories:
            vocab.update(normalise_tokens(memory.text))
        return {token: idx for idx, token in enumerate(sorted(vocab))}

    def _vectorize(self, text: str) -> np.ndarray:
        vec = np.zeros(len(self._vocab), dtype=np.float64)
        for token in normalise_tokens(text):
            idx = self._vocab.get(token)
            if idx is not None:
                vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    def rank(self, query_text: str, top_k: int) -> list[tuple[str, float]]:
        query_vec = self._vectorize(query_text)
        scored: list[tuple[str, float]] = []
        for memory in self._memories:
            sim = float(np.dot(query_vec, self._vectors[memory.memory_id]))
            scored.append((memory.memory_id, sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


class QpRouter:
    """Genuine Qp routing with architecture filters enabled."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)

    def rank(self, query: Query, top_k: int) -> list[tuple[str, float]]:
        scored: list[tuple[float, float, str]] = []
        for memory in self._memories:
            if not qp_pure_compatible(query.coordinate, memory.coordinate):
                continue
            try:
                distance = float(qp_coordinate_distance(query.coordinate, memory.coordinate))
            except Exception:
                continue
            score = float(
                qp_score(distance, query.coordinate.metric_prime, query.coordinate.working_precision)
            )
            scored.append((distance, score, memory.memory_id))
        scored.sort(key=lambda triple: (triple[0], -triple[1]))
        return [(mid, score) for _, score, mid in scored[:top_k]]


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------


def _chain_metrics(
    ranked_ids: Sequence[str], required_ids: frozenset[str], top_k: int
) -> tuple[float, float, bool]:
    retrieved = [mid for mid in ranked_ids[:top_k] if mid in required_ids]
    recall = len(retrieved) / len(required_ids) if required_ids else 0.0
    precision = len(retrieved) / len(ranked_ids[:top_k]) if ranked_ids[:top_k] else 0.0
    full = len(retrieved) == len(required_ids)
    return recall, precision, full


def _evaluate_query(
    query: Query,
    *,
    qp_router: QpRouter,
    vector_baseline: VectorRAGBaseline,
    top_k: int,
) -> PerQueryResult:
    qp_ranked = [mid for mid, _ in qp_router.rank(query, top_k=top_k)]
    vector_ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k=top_k)]

    qp_recall, qp_precision, qp_full = _chain_metrics(qp_ranked, query.required_ids, top_k)
    vec_recall, vec_precision, vec_full = _chain_metrics(
        vector_ranked, query.required_ids, top_k
    )

    return PerQueryResult(
        query_id=query.query_id,
        chain_id=query.chain_id,
        qp_chain_recall=qp_recall,
        vector_chain_recall=vec_recall,
        qp_chain_precision=qp_precision,
        vector_chain_precision=vec_precision,
        qp_full_chain=qp_full,
        vector_full_chain=vec_full,
    )


def _permutation_test_pvalue(
    differences: Sequence[float], permutations: int, seed: int
) -> float | None:
    if len(differences) < 2:
        return None
    observed = statistics.mean(differences)
    rng = random.Random(seed)
    count_extreme = 0
    for _ in range(permutations):
        permuted = [d if rng.random() < 0.5 else -d for d in differences]
        if abs(statistics.mean(permuted)) >= abs(observed):
            count_extreme += 1
    return count_extreme / permutations


def evaluate(
    memories: Sequence[Memory],
    queries: Sequence[Query],
    *,
    top_k: int,
    permutations: int,
    seed: int,
) -> tuple[BenchmarkSummary, list[PerQueryResult]]:
    qp_router = QpRouter(memories)
    vector_baseline = VectorRAGBaseline(memories)

    per_query = [
        _evaluate_query(
            q,
            qp_router=qp_router,
            vector_baseline=vector_baseline,
            top_k=top_k,
        )
        for q in queries
    ]

    differences = [r.qp_chain_recall - r.vector_chain_recall for r in per_query]
    p_value = _permutation_test_pvalue(differences, permutations, seed)

    def _mean(getter: callable) -> float:
        values = [getter(r) for r in per_query]
        return statistics.mean(values) if values else 0.0

    summary = BenchmarkSummary(
        queries=len(per_query),
        qp_chain_recall=_mean(lambda r: r.qp_chain_recall),
        vector_chain_recall=_mean(lambda r: r.vector_chain_recall),
        qp_chain_precision=_mean(lambda r: r.qp_chain_precision),
        vector_chain_precision=_mean(lambda r: r.vector_chain_precision),
        qp_full_chain_rate=_mean(lambda r: 1.0 if r.qp_full_chain else 0.0),
        vector_full_chain_rate=_mean(lambda r: 1.0 if r.vector_full_chain else 0.0),
        p_value=p_value,
    )
    return summary, per_query


# -----------------------------------------------------------------------------
# Artifact and CLI
# -----------------------------------------------------------------------------


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _build_artifact(
    summary: BenchmarkSummary,
    per_query: Sequence[PerQueryResult],
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
    runtime_ms: float,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"longbench-multihop-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="longbench-multihop",
        suite_version="v1",
        executed_at=executed_at,
        mode="coordinate_guided",
        status="partial",
        repos=[
            {
                "name": "ds-backend-local",
                "commit_sha": repo_sha,
                "role": "canonical_benchmark_engine",
                "required_for_run": True,
            }
        ],
        datasets=[
            {
                "name": "longbench_multihop_synthetic_v1",
                "version": "v1",
                "split": "benchmark",
                "record_count": len(per_query),
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "qp_chain_recall": {
                        "value": summary.qp_chain_recall,
                        "unit": "ratio",
                        "description": f"Qp chain recall within top {config.top_k}.",
                    },
                    "vector_chain_recall": {
                        "value": summary.vector_chain_recall,
                        "unit": "ratio",
                        "description": f"Vector-RAG chain recall within top {config.top_k}.",
                    },
                    "qp_chain_precision": {
                        "value": summary.qp_chain_precision,
                        "unit": "ratio",
                        "description": "Qp chain precision within top k.",
                    },
                    "vector_chain_precision": {
                        "value": summary.vector_chain_precision,
                        "unit": "ratio",
                        "description": "Vector-RAG chain precision within top k.",
                    },
                    "qp_full_chain_rate": {
                        "value": summary.qp_full_chain_rate,
                        "unit": "ratio",
                        "description": "Fraction of queries where Qp retrieved the full chain.",
                    },
                    "vector_full_chain_rate": {
                        "value": summary.vector_full_chain_rate,
                        "unit": "ratio",
                        "description": "Fraction of queries where vector-RAG retrieved the full chain.",
                    },
                    "p_value": {
                        "value": summary.p_value if summary.p_value is not None else -1.0,
                        "unit": "ratio",
                        "description": "Two-tailed paired permutation-test p-value for chain-recall difference.",
                    },
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "total_runtime_ms": {
                        "value": runtime_ms,
                        "unit": "ms",
                        "description": "Total harness runtime.",
                    }
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "embedding_queries": {
                        "value": summary.queries,
                        "unit": "count",
                        "description": "Number of query embeddings computed.",
                    }
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "Traceability metrics are out of scope for this reasoning-proxy benchmark.",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "Governance metrics are out of scope for this reasoning-proxy benchmark.",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={
            "chain_count": config.chain_count,
            "chain_length": config.chain_length,
            "top_k": config.top_k,
            "permutations": config.permutations,
            "seed": config.seed,
            "alpha": ALPHA,
        },
    )


def run_benchmark(config: BenchmarkConfig) -> tuple[BenchmarkSummary, list[PerQueryResult], Path]:
    start = time.perf_counter()
    memories, queries = generate_corpus(config.chain_count, seed=config.seed)
    summary, per_query = evaluate(
        memories,
        queries,
        top_k=config.top_k,
        permutations=config.permutations,
        seed=config.seed,
    )
    runtime_ms = (time.perf_counter() - start) * 1000.0

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(summary, per_query, config=config, executed_at=executed_at, runtime_ms=runtime_ms)

    output_path = config.output_root / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    # Emit KSR-EVAL v0.4 manifest alongside the artifact.
    from backend.benchmarks.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        artifact,
        eval_script_version="longbench_multihop_benchmark_v1.0",
        seeds=[config.seed],
        conditions={
            "chain_count": config.chain_count,
            "chain_length": config.chain_length,
            "top_k": config.top_k,
            "permutations": config.permutations,
            "transport": "R1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    return summary, per_query, output_path


def print_summary(summary: BenchmarkSummary, top_k: int) -> None:
    print("LongBench Multi-hop Retrieval Benchmark")
    print("=======================================")
    print(f"Queries               : {summary.queries}")
    print(f"Qp chain recall@{top_k}    : {summary.qp_chain_recall:.3f}")
    print(f"Vector chain recall@{top_k}: {summary.vector_chain_recall:.3f}")
    print(f"Qp chain precision    : {summary.qp_chain_precision:.3f}")
    print(f"Vector chain precision: {summary.vector_chain_precision:.3f}")
    print(f"Qp full-chain rate    : {summary.qp_full_chain_rate:.3f}")
    print(f"Vector full-chain rate: {summary.vector_full_chain_rate:.3f}")
    print(f"p-value               : {summary.p_value}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for benchmark artifacts.",
    )
    parser.add_argument(
        "--chain-count",
        type=int,
        default=DEFAULT_CHAIN_COUNT,
        help="Number of multi-hop chains to generate.",
    )
    parser.add_argument(
        "--chain-length",
        type=int,
        default=DEFAULT_CHAIN_LENGTH,
        help="Number of memories per chain.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Top-k cutoff for chain-recall metrics.",
    )
    parser.add_argument(
        "--permutations",
        type=int,
        default=DEFAULT_PERMUTATIONS,
        help="Number of permutations for the paired significance test.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for corpus generation.",
    )
    parser.add_argument(
        "--print-artifact",
        action="store_true",
        help="Print the full benchmark artifact JSON to stdout.",
    )
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        output_root=args.output_root,
        chain_count=args.chain_count,
        chain_length=args.chain_length,
        top_k=args.top_k,
        permutations=args.permutations,
        seed=args.seed,
    )
    summary, per_query, output_path = run_benchmark(config)
    print_summary(summary, config.top_k)
    print(f"\nArtifact written to: {output_path}")

    if args.print_artifact:
        print()
        print(json.dumps(
            _build_artifact(summary, per_query, config=config, executed_at=datetime.now(timezone.utc), runtime_ms=0.0)
            .model_dump(mode="json"),
            indent=2,
        ))


if __name__ == "__main__":
    main()

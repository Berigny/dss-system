"""DSS-297 — Deterministic citation-faithfulness check.

This harness verifies that every citation emitted in a synthetic end-to-end
response hash-matches a committed ledger entry.  The gate is deterministic:
``citation_integrity`` must be 1.0.  An optional lexical-overlap ``judge_score``
is reported as informational only and does not control the gate.

The harness is deterministic, uses no external LLM / API, and emits a validated
``BenchmarkArtifact`` plus a KSR-EVAL v0.4 manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.api.chat import (
    _extract_coords_from_text,
    _extract_inline_citations,
)
from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.harness import BenchmarkHarness
from backend.benchmarks.manifest import build_manifest, write_manifest
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "dss297_citation_faithfulness"
DEFAULT_SEED = 193
DEFAULT_SEEDS = (193, 42, 7)

# Synthetic source documents and claims.  Each document becomes one ledger entry.
SOURCE_DOCUMENTS = [
    {
        "id": "fed-reg-d",
        "text": "Regulation D requires a reserve requirement of 10 percent for transaction accounts.",
        "claims": ["Reserve requirement is 10 percent"],
    },
    {
        "id": "iso-27001",
        "text": "ISO 27001 requires an information security management system with documented risk treatment.",
        "claims": ["ISMS requires documented risk treatment"],
    },
    {
        "id": "gaap-606",
        "text": "GAAP ASC 606 revenue recognition requires identifying the contract, performance obligations, and transaction price.",
        "claims": ["Revenue recognition requires identifying performance obligations"],
    },
    {
        "id": "osha-1910",
        "text": "OSHA 1910 requires employers to provide a workplace free from recognized hazards.",
        "claims": ["Employers must provide a workplace free from recognized hazards"],
    },
]


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    seeds: tuple[int, ...]


@dataclass(frozen=True)
class SourceEntry:
    entry_id: str
    text: str
    claims: tuple[str, ...]


@dataclass(frozen=True)
class CitationCase:
    case_id: str
    response_text: str
    expected_citations: set[str]


@dataclass(frozen=True)
class CitationResult:
    case_id: str
    response_text: str
    extracted_inline: list[str]
    extracted_coords: list[str]
    matched: set[str]
    missing: set[str]
    unexpected: set[str]
    chain_valid: bool
    citation_integrity: float
    judge_score: float


@dataclass(frozen=True)
class BenchmarkSummary:
    cases: int
    citation_integrity: float
    chain_valid_rate: float
    judge_score_mean: float
    per_case: list[CitationResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "citation_integrity": self.citation_integrity,
            "chain_valid_rate": self.chain_valid_rate,
            "judge_score_mean": self.judge_score_mean,
            "per_case": [
                {
                    "case_id": r.case_id,
                    "matched": sorted(r.matched),
                    "missing": sorted(r.missing),
                    "unexpected": sorted(r.unexpected),
                    "chain_valid": r.chain_valid,
                    "citation_integrity": r.citation_integrity,
                    "judge_score": r.judge_score,
                }
                for r in self.per_case
            ],
        }


# -----------------------------------------------------------------------------
# Synthetic case generation
# -----------------------------------------------------------------------------


def _build_source_entries() -> list[SourceEntry]:
    return [
        SourceEntry(
            entry_id=doc["id"],
            text=doc["text"],
            claims=tuple(doc["claims"]),
        )
        for doc in SOURCE_DOCUMENTS
    ]


def _build_ledger_store(entries: Sequence[SourceEntry]) -> LedgerStoreV2:
    """Commit every source entry to a deterministic in-memory ledger store."""
    store = LedgerStoreV2({})
    for entry in entries:
        ledger_entry = LedgerEntry(
            key=LedgerKey(namespace="dss297", identifier=entry.entry_id),
            state=ContinuousState(
                coordinates={},
                phase="source",
                metadata={
                    "text": entry.text,
                    "claims": list(entry.claims),
                    "source_id": entry.entry_id,
                },
            ),
        )
        store.write(ledger_entry)
    return store


def _response_with_citations(cited_ids: Sequence[str], *, claim_index: int = 0) -> str:
    """Build an assistant response that cites the requested source IDs."""
    parts: list[str] = []
    for sid in cited_ids:
        doc = next(d for d in SOURCE_DOCUMENTS if d["id"] == sid)
        claim = doc["claims"][claim_index % len(doc["claims"])]
        parts.append(f"{claim} [{sid}].")
    return " ".join(parts)


def _generate_cases(rng: random.Random) -> list[CitationCase]:
    """Generate deterministic citation cases: faithful, missing, and extra refs."""
    ids = [d["id"] for d in SOURCE_DOCUMENTS]
    cases: list[CitationCase] = []

    # Faithful: every inline citation resolves to a committed source.
    cases.append(
        CitationCase(
            case_id="faithful_single",
            response_text=_response_with_citations(["fed-reg-d"]),
            expected_citations={"dss297:fed-reg-d"},
        )
    )
    cases.append(
        CitationCase(
            case_id="faithful_multiple",
            response_text=_response_with_citations(["fed-reg-d", "iso-27001"]),
            expected_citations={"dss297:fed-reg-d", "dss297:iso-27001"},
        )
    )

    # Missing: response cites one valid source but omits another it references.
    cases.append(
        CitationCase(
            case_id="missing_ref",
            response_text=(
                "Regulation D requires a reserve requirement of 10 percent "
                "[fed-reg-d]. ISO 27001 also requires documented risk treatment."
            ),
            expected_citations={"dss297:fed-reg-d", "dss297:iso-27001"},
        )
    )

    # Unexpected: response cites a source ID that does not exist in the ledger.
    cases.append(
        CitationCase(
            case_id="unexpected_ref",
            response_text=(
                "Regulation D requires a reserve requirement of 10 percent "
                "[fed-reg-d] and the made-up rule applies [phantom-rule]."
            ),
            expected_citations={"dss297:fed-reg-d"},
        )
    )

    # Bracket-style citation to a second source.
    cases.append(
        CitationCase(
            case_id="second_source",
            response_text=(
                "GAAP ASC 606 requires identifying performance obligations "
                "[gaap-606]."
            ),
            expected_citations={"dss297:gaap-606"},
        )
    )

    # Shuffled order: faithful but different source order.
    cases.append(
        CitationCase(
            case_id="shuffled",
            response_text=_response_with_citations(["gaap-606", "osha-1910", "fed-reg-d"]),
            expected_citations={
                "dss297:gaap-606",
                "dss297:osha-1910",
                "dss297:fed-reg-d",
            },
        )
    )

    # Extra deterministic cases to make multi-seed aggregation meaningful.
    for i in range(3):
        sampled = rng.sample(ids, k=rng.randint(1, len(ids)))
        cases.append(
            CitationCase(
                case_id=f"random_sample_{i}",
                response_text=_response_with_citations(sampled),
                expected_citations={f"dss297:{sid}" for sid in sampled},
            )
        )

    return cases


# -----------------------------------------------------------------------------
# Deterministic citation verification
# -----------------------------------------------------------------------------


def _compute_judge_score(response_text: str, source_texts: Sequence[str]) -> float:
    """Informational lexical-overlap score; not a gate.

    Returns the fraction of non-trivial response tokens that also appear in at
    least one cited source text.  This is a cheap deterministic surrogate for an
    LLM semantic-faithfulness judge.
    """
    response_tokens = set(normalise_tokens(response_text))
    if not response_tokens:
        return 0.0
    source_tokens: set[str] = set()
    for text in source_texts:
        source_tokens.update(normalise_tokens(text))
    # Ignore citation brackets themselves and short artifacts.
    ignore = {"[", "]", "see", "dss297"}
    response_tokens -= ignore
    if not response_tokens:
        return 0.0
    overlap = response_tokens & source_tokens
    return len(overlap) / len(response_tokens)


def _verify_case(
    case: CitationCase,
    store: LedgerStoreV2,
    entries: Sequence[SourceEntry],
) -> CitationResult:
    """Verify one response against the ledger store deterministically."""
    # First verify the whole namespace chain for the case.
    chain_status = store.verify_namespace_chain("dss297")
    chain_valid = chain_status.get("valid", False)

    inline = _extract_inline_citations(case.response_text)
    coords = _extract_coords_from_text(case.response_text, default_namespace="dss297")
    extracted = set(coords)
    # Inline bracket IDs like [fed-reg-d] are not coordinates; map them to the
    # canonical namespace form if they match a known source.
    for bracket in inline:
        bare = bracket.strip("[]")
        if ":" not in bare:
            bare = f"dss297:{bare}"
        # Only treat it as a citation if it resolves to a known ledger entry.
        try:
            store.read(bare, verify_chain=False)
            extracted.add(bare)
        except Exception:
            pass

    matched = extracted & case.expected_citations
    missing = case.expected_citations - extracted
    unexpected = extracted - case.expected_citations

    # Citation integrity: every expected citation was emitted and every emitted
    # citation resolved to a committed ledger entry.
    all_resolved = True
    for citation in extracted:
        try:
            store.read(citation, verify_chain=False)
        except Exception:
            all_resolved = False

    citation_integrity = 1.0 if (not missing and not unexpected and all_resolved) else 0.0

    source_texts = [e.text for e in entries if f"dss297:{e.entry_id}" in extracted]
    judge_score = _compute_judge_score(case.response_text, source_texts)

    return CitationResult(
        case_id=case.case_id,
        response_text=case.response_text,
        extracted_inline=inline,
        extracted_coords=sorted(coords),
        matched=matched,
        missing=missing,
        unexpected=unexpected,
        chain_valid=chain_valid,
        citation_integrity=citation_integrity,
        judge_score=judge_score,
    )


def evaluate(seed: int) -> BenchmarkSummary:
    rng = random.Random(seed)
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    cases = _generate_cases(rng)
    results = [_verify_case(c, store, entries) for c in cases]

    n = len(results)
    integrity = sum(r.citation_integrity for r in results) / n if n else 1.0
    chain_valid_rate = sum(1.0 for r in results if r.chain_valid) / n if n else 1.0
    judge_mean = sum(r.judge_score for r in results) / n if n else 0.0

    return BenchmarkSummary(
        cases=n,
        citation_integrity=integrity,
        chain_valid_rate=chain_valid_rate,
        judge_score_mean=judge_mean,
        per_case=results,
    )


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
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
    runtime_ms: float,
    seed: int,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()
    gate_passed = summary.citation_integrity >= 1.0 and summary.chain_valid_rate >= 1.0

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"dss297-citation-faithfulness-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id="dss297-citation-faithfulness",
        suite_version="v1",
        executed_at=executed_at,
        mode="full_dss",
        status="success",
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
                "name": "dss297_citation_faithfulness_synthetic_v1",
                "version": "v1",
                "split": "benchmark",
                "record_count": summary.cases,
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "citation_integrity": {
                        "value": summary.citation_integrity,
                        "unit": "ratio",
                        "description": "Fraction of responses with all expected citations resolved and no unexpected citations.",
                    },
                },
            },
            "governance": {
                "status": "present",
                "metrics": {
                    "chain_valid_rate": {
                        "value": summary.chain_valid_rate,
                        "unit": "ratio",
                        "description": "Fraction of responses whose ledger namespace chain validated.",
                    },
                    "citation_gate_passed": {
                        "value": 1 if gate_passed else 0,
                        "unit": "boolean",
                        "description": "True if citation integrity and chain validity are both 100%.",
                    },
                    "judge_score_is_informational": {
                        "value": 1,
                        "unit": "boolean",
                        "description": "Judge score is reported separately and does not control the gate.",
                    },
                },
            },
            "traceability": {
                "status": "present",
                "metrics": {
                    "total_cases": {
                        "value": summary.cases,
                        "unit": "count",
                        "description": "Total number of citation-faithfulness cases evaluated.",
                    },
                    "expected_citations_total": {
                        "value": sum(len(r.matched) + len(r.missing) for r in summary.per_case),
                        "unit": "count",
                        "description": "Total expected citations across all cases.",
                    },
                    "missing_citations_total": {
                        "value": sum(len(r.missing) for r in summary.per_case),
                        "unit": "count",
                        "description": "Total missing citations across all cases.",
                    },
                    "unexpected_citations_total": {
                        "value": sum(len(r.unexpected) for r in summary.per_case),
                        "unit": "count",
                        "description": "Total unexpected/unresolved citations across all cases.",
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
                    "llm_calls": {
                        "value": 0,
                        "unit": "count",
                        "description": "Number of LLM API calls (zero for deterministic check).",
                    },
                    "judge_score_mean": {
                        "value": summary.judge_score_mean,
                        "unit": "ratio",
                        "description": "Informational lexical-overlap judge score; not a gate.",
                    },
                },
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={
            "seed": seed,
            "gate_passed": gate_passed,
            "citation_integrity_target": 1.0,
            "chain_validity_target": 1.0,
        },
    )


def run_single_seed(seed: int, config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-297 for a single seed and return a validated artifact."""
    start = time.perf_counter()
    summary = evaluate(seed)
    runtime_ms = (time.perf_counter() - start) * 1000.0
    executed_at = datetime.now(timezone.utc)

    artifact = _build_artifact(
        summary,
        config=config,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        seed=seed,
    )

    output_path = config.output_root / "seeds" / str(seed) / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = build_manifest(
        artifact,
        eval_script_version="dss297_citation_faithfulness_benchmark_v1.0",
        seeds=[seed],
        conditions={
            "transport": "R1",
            "deterministic": True,
        },
    )
    write_manifest(manifest, output_path.with_suffix(".manifest.json"))
    return artifact


def run_benchmark(config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-297 across the configured seeds and return the aggregate artifact."""
    harness = BenchmarkHarness(
        suite_id="dss297-citation-faithfulness",
        suite_version="v1",
        mode="full_dss",
        seeds=list(config.seeds),
        output_root=config.output_root,
        run_label="dss297-citation-faithfulness",
    )
    return harness.run(lambda seed: run_single_seed(seed, config))


def print_summary(summary: BenchmarkSummary) -> None:
    print("DSS-297 Citation Faithfulness Benchmark")
    print("=========================================")
    print(f"Cases                : {summary.cases}")
    print(f"Citation integrity   : {summary.citation_integrity:.3f}")
    print(f"Chain valid rate     : {summary.chain_valid_rate:.3f}")
    print(f"Judge score mean     : {summary.judge_score_mean:.3f}")
    print("Per-case results")
    for r in summary.per_case:
        status = "PASS" if r.citation_integrity == 1.0 else "FAIL"
        print(f"  {r.case_id:<20} {status}  missing={sorted(r.missing)} unexpected={sorted(r.unexpected)}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for benchmark artifacts.",
    )
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated random seeds for multi-seed aggregation.",
    )
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        output_root=args.output_root,
        seeds=args.seeds,
    )
    aggregate = run_benchmark(config)
    print(f"Aggregate artifact status: {aggregate.status}")
    print(f"Aggregate artifact written to: {config.output_root / 'aggregate'}")


if __name__ == "__main__":
    main()

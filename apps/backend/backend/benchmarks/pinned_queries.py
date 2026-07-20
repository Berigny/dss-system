"""Pinned query-set loader / generator for the DSS v0.5 benchmark suite.

DSS-296 requires the v0.5 suite to use pre-generated, SHA-pinned query sets
committed under ``eval/queries/`` so that runtime query generation can be
eliminated.  This module provides:

* Stable serialization / deserialization for query objects that contain
  ``QpCoordinate`` instances.
* Generation and saving of pinned query sets for the default configurations of
  DSS-292 through DSS-295.
* ``eval/queries/manifest.json`` generation with SHA256 sums.
* Runtime loading with graceful fallback to on-the-fly generation when a pinned
  set is missing or does not cover the requested configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.benchmarks.longbench_needle_benchmark import (
    DEFAULT_LENGTHS as NEEDLE_DEFAULT_LENGTHS,
    NeedleQuery,
    generate_corpus as generate_needle_corpus,
)
from backend.fieldx_kernel.qp_coordinate import QpCoordinate


SCHEMA_VERSION = "1.0"
QUERY_SET_VERSION = "v0.5"

# Common multi-seed set used across the v0.5 suite.
DEFAULT_SEEDS = (193, 42, 7)

# Default DSS-292 configuration (mirrors dss292_known_unknown_benchmark.py).
DSS292_DEFAULT_LENGTHS = (4, 8, 16, 32)
DSS292_DEFAULT_SEEDS = DEFAULT_SEEDS


REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()
QUERIES_ROOT = REPO_ROOT / "eval" / "queries"


# Suite identifier -> filename stem used under eval/queries/.
_QUERY_FILE_NAMES: dict[str, str] = {
    "dss292-known-unknown": "dss292_known_unknown_queries",
    "dss293-adversarial-poisoning": "dss293_adversarial_poisoning_queries",
    "dss294-bm25-ranking": "dss294_bm25_ranking_queries",
    "dss295-latency-storage": "dss295_latency_storage_queries",
}


# ---------------------------------------------------------------------------
# Stable JSON helpers
# ---------------------------------------------------------------------------


def _stable_json(obj: Any) -> str:
    """Return a deterministic, pretty-printed JSON representation."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Coordinate (de)serialization
# ---------------------------------------------------------------------------


def _serialize_coordinate(coord: QpCoordinate) -> dict[str, Any]:
    return coord.as_dict()


def _deserialize_coordinate(payload: dict[str, Any]) -> QpCoordinate:
    return QpCoordinate.from_dict(payload)


# ---------------------------------------------------------------------------
# Query object (de)serialization
# ---------------------------------------------------------------------------


def _serialize_needle_query(q: NeedleQuery) -> dict[str, Any]:
    return {
        "query_id": q.query_id,
        "text": q.text,
        "coordinate": _serialize_coordinate(q.coordinate),
        "needle_id": q.needle_id,
        "length": q.length,
    }


def _deserialize_needle_query(payload: dict[str, Any]) -> NeedleQuery:
    return NeedleQuery(
        query_id=payload["query_id"],
        text=payload["text"],
        coordinate=_deserialize_coordinate(payload["coordinate"]),
        needle_id=payload["needle_id"],
        length=payload["length"],
    )


def _serialize_benchmark_query(q: BenchmarkQuery) -> dict[str, Any]:
    return {
        "query_id": q.query_id,
        "text": q.text,
        "coordinate": _serialize_coordinate(q.coordinate),
        "query_class": q.query_class,
        "target_id": q.target_id,
        "length": q.length,
    }


def _deserialize_benchmark_query(payload: dict[str, Any]) -> "BenchmarkQuery":
    from backend.benchmarks.dss292_known_unknown_benchmark import BenchmarkQuery

    return BenchmarkQuery(
        query_id=payload["query_id"],
        text=payload["text"],
        coordinate=_deserialize_coordinate(payload["coordinate"]),
        query_class=payload["query_class"],
        target_id=payload.get("target_id"),
        length=payload["length"],
    )


def _serialize_poison_case(c: PoisonCase) -> dict[str, Any]:
    return {
        "case_id": c.case_id,
        "namespace": c.namespace,
        "base_id": c.base_id,
        "poison_id": c.poison_id,
        "base_text": c.base_text,
        "poison_text": c.poison_text,
        "base_coordinate": _serialize_coordinate(c.base_coordinate),
        "poison_coordinate": _serialize_coordinate(c.poison_coordinate),
        "poison_type": c.poison_type,
    }


def _deserialize_poison_case(payload: dict[str, Any]) -> "PoisonCase":
    from backend.benchmarks.dss293_adversarial_poisoning_benchmark import PoisonCase

    return PoisonCase(
        case_id=payload["case_id"],
        namespace=payload["namespace"],
        base_id=payload["base_id"],
        poison_id=payload.get("poison_id"),
        base_text=payload["base_text"],
        poison_text=payload["poison_text"],
        base_coordinate=_deserialize_coordinate(payload["base_coordinate"]),
        poison_coordinate=_deserialize_coordinate(payload["poison_coordinate"]),
        poison_type=payload["poison_type"],
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _generate_dss292_query_sets(
    lengths: tuple[int, ...] = DSS292_DEFAULT_LENGTHS,
    seeds: tuple[int, ...] = DSS292_DEFAULT_SEEDS,
) -> dict[str, Any]:
    """Generate pinned query records for DSS-292."""
    from backend.benchmarks.dss292_known_unknown_benchmark import generate_corpus

    seed_records: dict[str, Any] = {}
    for seed in seeds:
        _, queries = generate_corpus(lengths, seed=seed)
        seed_records[str(seed)] = {
            "lengths": list(lengths),
            "queries": [_serialize_benchmark_query(q) for q in queries],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": "dss292-known-unknown",
        "query_set_version": QUERY_SET_VERSION,
        "description": (
            "Pinned known-present / known-absent / borderline queries for DSS-292."
        ),
        "seed_records": seed_records,
    }


def _generate_dss293_query_sets(
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
) -> dict[str, Any]:
    """Generate pinned case records for DSS-293."""
    from backend.benchmarks.dss293_adversarial_poisoning_benchmark import build_cases

    seed_records: dict[str, Any] = {}
    for seed in seeds:
        cases = build_cases(seed=seed)
        seed_records[str(seed)] = {
            "cases": [_serialize_poison_case(c) for c in cases],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": "dss293-adversarial-poisoning",
        "query_set_version": QUERY_SET_VERSION,
        "description": "Pinned adversarial poisoning cases for DSS-293.",
        "seed_records": seed_records,
    }


def _generate_dss294_query_sets(
    lengths: tuple[int, ...] = NEEDLE_DEFAULT_LENGTHS,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
) -> dict[str, Any]:
    """Generate pinned query records for DSS-294."""
    seed_records: dict[str, Any] = {}
    for seed in seeds:
        _, queries = generate_needle_corpus(lengths, seed=seed)
        seed_records[str(seed)] = {
            "lengths": list(lengths),
            "queries": [_serialize_needle_query(q) for q in queries],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": "dss294-bm25-ranking",
        "query_set_version": QUERY_SET_VERSION,
        "description": "Pinned LongBench needle queries for DSS-294.",
        "seed_records": seed_records,
    }


def _generate_dss295_query_sets(
    corpus_sizes: tuple[int, ...] = (999, 9999, 99999),
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
) -> dict[str, Any]:
    """Generate pinned query records for DSS-295."""
    seed_records: dict[str, Any] = {}
    for seed in seeds:
        queries_by_size: dict[str, Any] = {}
        for size in corpus_sizes:
            _, queries = generate_needle_corpus([size], seed=seed)
            queries_by_size[str(size)] = [_serialize_needle_query(q) for q in queries]
        seed_records[str(seed)] = {
            "corpus_sizes": list(corpus_sizes),
            "queries_by_corpus_size": queries_by_size,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": "dss295-latency-storage",
        "query_set_version": QUERY_SET_VERSION,
        "description": "Pinned LongBench needle queries for DSS-295 latency/storage buckets.",
        "seed_records": seed_records,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _query_file_path(suite_id: str, *, root: Path = QUERIES_ROOT) -> Path:
    if suite_id not in _QUERY_FILE_NAMES:
        raise ValueError(f"unknown suite_id: {suite_id!r}")
    return root / f"{_QUERY_FILE_NAMES[suite_id]}.json"


def load_pinned_queries(
    suite_id: str,
    seed: int,
    *,
    root: Path = QUERIES_ROOT,
) -> list[Any] | dict[int, list[Any]]:
    """Load the pinned query set for ``suite_id`` and ``seed``.

    Returns a list of queries/cases for DSS-292, DSS-293 and DSS-294.  For
    DSS-295 returns a mapping ``corpus_size -> list[NeedleQuery]``.

    Raises ``FileNotFoundError`` when the pinned query set file does not exist
    or does not contain the requested seed.
    """
    path = _query_file_path(suite_id, root=root)
    if not path.exists():
        raise FileNotFoundError(f"pinned query set not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    seed_record = payload.get("seed_records", {}).get(str(seed))
    if seed_record is None:
        raise FileNotFoundError(f"seed {seed} not found in pinned query set: {path}")

    if suite_id == "dss292-known-unknown":
        return [_deserialize_benchmark_query(q) for q in seed_record["queries"]]
    if suite_id == "dss293-adversarial-poisoning":
        return [_deserialize_poison_case(c) for c in seed_record["cases"]]
    if suite_id == "dss294-bm25-ranking":
        return [_deserialize_needle_query(q) for q in seed_record["queries"]]
    if suite_id == "dss295-latency-storage":
        return {
            int(size): [_deserialize_needle_query(q) for q in queries]
            for size, queries in seed_record["queries_by_corpus_size"].items()
        }
    raise ValueError(f"unknown suite_id: {suite_id!r}")


def load_pinned_queries_for_config(
    suite_id: str,
    seed: int,
    *,
    root: Path = QUERIES_ROOT,
    lengths: tuple[int, ...] | None = None,
    corpus_sizes: tuple[int, ...] | None = None,
) -> list[Any] | dict[int, list[Any]]:
    """Load pinned queries and filter to the requested lengths or corpus sizes.

    Raises ``ValueError`` if the pinned set does not cover the requested
    configuration.
    """
    loaded = load_pinned_queries(suite_id, seed, root=root)

    if suite_id == "dss292-known-unknown":
        assert lengths is not None
        from backend.benchmarks.dss292_known_unknown_benchmark import BenchmarkQuery

        queries = [q for q in loaded if isinstance(q, BenchmarkQuery) and q.length in lengths]
        available = {q.length for q in queries}
        if not set(lengths).issubset(available):
            missing = set(lengths) - available
            raise ValueError(f"pinned DSS-292 query set missing lengths: {sorted(missing)}")
        return queries

    if suite_id == "dss294-bm25-ranking":
        assert lengths is not None
        queries = [q for q in loaded if isinstance(q, NeedleQuery) and q.length in lengths]
        available = {q.length for q in queries}
        if not set(lengths).issubset(available):
            missing = set(lengths) - available
            raise ValueError(f"pinned DSS-294 query set missing lengths: {sorted(missing)}")
        return queries

    if suite_id == "dss295-latency-storage":
        assert corpus_sizes is not None
        loaded_map = {k: v for k, v in loaded.items() if k in corpus_sizes}
        missing = set(corpus_sizes) - set(loaded_map.keys())
        if missing:
            raise ValueError(f"pinned DSS-295 query set missing corpus sizes: {sorted(missing)}")
        return loaded_map

    # DSS-293 has no per-config filtering.
    return loaded


def generate_and_save_query_sets(
    root: Path = QUERIES_ROOT,
    *,
    write_manifest: bool = True,
) -> dict[str, Path]:
    """Generate all pinned query sets, write JSON files, and update the manifest.

    Returns a mapping from suite_id to the written file path.
    """
    root.mkdir(parents=True, exist_ok=True)

    generated_at = _now_utc()
    files: dict[str, Path] = {}
    sha_entries: dict[str, dict[str, Any]] = {}

    for suite_id, generator in (
        ("dss292-known-unknown", _generate_dss292_query_sets()),
        ("dss293-adversarial-poisoning", _generate_dss293_query_sets()),
        ("dss294-bm25-ranking", _generate_dss294_query_sets()),
        ("dss295-latency-storage", _generate_dss295_query_sets()),
    ):
        payload = dict(generator)
        payload["generated_at"] = generated_at
        path = _query_file_path(suite_id, root=root)
        path.write_text(_stable_json(payload), encoding="utf-8")
        files[suite_id] = path
        sha_entries[path.name] = {
            "sha256": _sha256_file(path),
            "suite_id": suite_id,
        }

    if write_manifest:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "files": sha_entries,
        }
        (root / "manifest.json").write_text(_stable_json(manifest), encoding="utf-8")

    return files


def verify_query_manifest(*, root: Path = QUERIES_ROOT) -> dict[str, Any]:
    """Verify the pinned query manifest against files on disk.

    Mirrors the corpus manifest verification in ``eval/eval_entrypoint.py``.
    """
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return {"status": "missing", "manifest_path": str(manifest_path)}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified: dict[str, Any] = {"status": "ok", "manifest_path": str(manifest_path), "files": {}}
    for filename, info in manifest.get("files", {}).items():
        file_path = root / filename
        expected = info.get("sha256", "")
        actual = _sha256_file(file_path) if file_path.exists() else ""
        verified["files"][filename] = {
            "expected": expected,
            "actual": actual,
            "valid": expected and actual == expected,
        }
    return verified


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queries-root",
        type=Path,
        default=QUERIES_ROOT,
        help="Directory where pinned query sets are stored.",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Skip writing manifest.json.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the existing query manifest instead of regenerating.",
    )
    args = parser.parse_args(argv)

    if args.verify:
        result = verify_query_manifest(root=args.queries_root)
        print(_stable_json(result))
        return 0 if result.get("status") == "ok" else 1

    generate_and_save_query_sets(
        root=args.queries_root,
        write_manifest=not args.no_manifest,
    )
    print(f"Pinned query sets written to: {args.queries_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

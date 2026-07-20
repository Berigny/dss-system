"""Generate synthetic corpus files for the v0.5 benchmark suite.

This script materialises the in-memory corpora that DSS-292, DSS-294 and
DSS-295 actually evaluate against, computes SHA256 hashes, and writes
``eval/corpus/manifest.json`` so the eval entrypoint verifies the exact data
used by the benchmarks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
CORPUS_ROOT = REPO_ROOT / "eval" / "corpus"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _memory_to_record(memory: Any) -> dict[str, Any]:
    """Serialise a Memory or NeedleMemory to a stable JSONL record."""
    record: dict[str, Any] = {
        "memory_id": memory.memory_id,
        "text": memory.text,
        "length": memory.length,
    }
    coord = memory.coordinate
    record["coordinate"] = {
        "coordinate_id": coord.coordinate_id,
        "kernel_node": coord.kernel_node,
        "metric_prime": coord.metric_prime,
        "tetrahedron": coord.tetrahedron,
        "valuation_offset": coord.valuation_offset,
        "circulation_pass": coord.circulation_pass,
        "hysteresis_depth": coord.hysteresis_depth,
    }
    if hasattr(memory, "is_needle"):
        record["is_needle"] = memory.is_needle
    return record


def _write_corpus(name: str, memories: list[Any]) -> Path:
    path = CORPUS_ROOT / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for memory in memories:
            fh.write(json.dumps(_memory_to_record(memory), sort_keys=True) + "\n")
    return path


def generate_dss292_corpus() -> Path:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))
    sys.path.insert(0, str(REPO_ROOT / "packages" / "shared-types" / "src"))

    from backend.benchmarks.dss292_known_unknown_benchmark import generate_corpus

    memories, _ = generate_corpus((4, 8, 16, 32), seed=193)
    return _write_corpus("dss292_known_unknown_synthetic_v1", memories)


def generate_dss294_corpus() -> Path:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))
    sys.path.insert(0, str(REPO_ROOT / "packages" / "shared-types" / "src"))

    from backend.benchmarks.longbench_needle_benchmark import generate_corpus

    memories, _ = generate_corpus((4, 8, 16, 32), seed=193)
    return _write_corpus("dss294_longbench_needle_synthetic_v1", memories)


def generate_dss295_corpus() -> Path:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))
    sys.path.insert(0, str(REPO_ROOT / "packages" / "shared-types" / "src"))

    from backend.benchmarks.longbench_needle_benchmark import generate_corpus

    memories, _ = generate_corpus((999, 9999, 99999), seed=193)
    return _write_corpus("dss295_longbench_needle_synthetic_v1", memories)


def main() -> int:
    CORPUS_ROOT.mkdir(parents=True, exist_ok=True)

    files: dict[str, Path] = {
        "dss292_known_unknown_synthetic_v1.jsonl": generate_dss292_corpus(),
        "dss294_longbench_needle_synthetic_v1.jsonl": generate_dss294_corpus(),
        "dss295_longbench_needle_synthetic_v1.jsonl": generate_dss295_corpus(),
    }

    # Preserve existing files already in the directory.
    for existing in CORPUS_ROOT.glob("*.jsonl"):
        if existing.name not in files:
            files[existing.name] = existing

    manifest = {
        "schema_version": "1.0",
        "generated_at": "2026-07-20T00:00:00Z",
        "files": {
            name: {
                "sha256": _sha256_file(path),
                "records": sum(1 for _ in path.open("r", encoding="utf-8")),
            }
            for name, path in sorted(files.items())
        },
    }

    manifest_path = CORPUS_ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Updated corpus manifest: {manifest_path}")
    for name, path in sorted(files.items()):
        print(f"  {name}: {path.stat().st_size:,} bytes, {manifest['files'][name]['records']:,} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

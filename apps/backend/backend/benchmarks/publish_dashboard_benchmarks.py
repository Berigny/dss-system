"""Publish benchmark artefacts into the DSS-Dashboard benchmark feed shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from backend.benchmarks.canonical_publication_source import (
    build_canonical_publication_source_contract,
)
from backend.benchmarks.operator_publication import build_operator_publication_contract
from backend.benchmarks.phase1_activation import build_phase1_activation_contract
from backend.benchmarks.artifact_schema import validate_benchmark_artifact

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHBOARD_PUBLICATION_PATH = (
    APP_ROOT.parent / "DSS-Dashboard" / "data" / "benchmark_runs.json"
)


def _artifact_paths_from_root(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_exemplars(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not payload:
        return {}
    if isinstance(payload, dict):
        normalized: dict[str, list[dict[str, Any]]] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, list):
                normalized[key] = [item for item in value if isinstance(item, dict)]
        return normalized
    if isinstance(payload, list):
        normalized_list = [item for item in payload if isinstance(item, dict)]
        return {str(item.get("run_id") or "").strip(): [item] for item in normalized_list if str(item.get("run_id") or "").strip()}
    return {}


def _publication_run_from_artifact(path: Path, exemplars_by_run: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    payload = _load_json(path)
    artifact = validate_benchmark_artifact(payload)
    publication_run = artifact.model_dump(mode="json")
    exemplars = exemplars_by_run.get(artifact.run_id)
    if exemplars:
        publication_run["exemplars"] = exemplars
    return publication_run


def _run_dedupe_key(run: dict[str, Any]) -> tuple[str, str]:
    return (
        str(run.get("suite_id") or "").strip(),
        str(run.get("mode") or "").strip(),
    )


def _dedupe_runs_by_suite_mode(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest run per (suite_id, mode)."""
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        key = _run_dedupe_key(run)
        if not all(key):
            continue
        existing = newest.get(key)
        if existing is None or str(run.get("executed_at") or "") > str(existing.get("executed_at") or ""):
            newest[key] = run
    return sorted(newest.values(), key=lambda item: str(item.get("executed_at") or ""), reverse=True)


def build_publication_payload(
    artifact_paths: Iterable[Path],
    *,
    note: str,
    exemplars_by_run: dict[str, list[dict[str, Any]]] | None = None,
    reference_only_suites: list[str] | None = None,
    max_age_hours: int = 168,
    dedupe_suite_mode: bool = False,
) -> dict[str, Any]:
    runs = [
        _publication_run_from_artifact(path, exemplars_by_run or {})
        for path in artifact_paths
    ]
    if dedupe_suite_mode:
        runs = _dedupe_runs_by_suite_mode(runs)
    else:
        runs.sort(key=lambda item: str(item.get("executed_at") or ""), reverse=True)
    return {
        "note": note,
        "canonical_publication_source": build_canonical_publication_source_contract(),
        "operator_publication": build_operator_publication_contract(),
        "phase_1_activation": build_phase1_activation_contract(
            runs,
            reference_only_suites=reference_only_suites or [],
            max_age_hours=max_age_hours,
        ),
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts",
        nargs="*",
        type=Path,
        help="Specific benchmark artefact JSON files to publish.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="Optional directory to scan recursively for benchmark artefact JSON files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DASHBOARD_PUBLICATION_PATH,
        help="Path to the DSS-Dashboard publication JSON file.",
    )
    parser.add_argument(
        "--exemplars",
        type=Path,
        default=None,
        help="Optional JSON file keyed by run_id containing exemplar payloads to merge into published runs.",
    )
    parser.add_argument(
        "--note",
        default="Published benchmark artefacts are read-only trust material. The Control Plane renders them but does not execute benchmark runs.",
        help="Publication note stored at the top of the emitted dashboard file.",
    )
    parser.add_argument(
        "--reference-only-suite",
        dest="reference_only_suites",
        action="append",
        default=[],
        help="Optional Phase 1 suite name to mark as reference_only when no canonical run is yet published.",
    )
    parser.add_argument(
        "--phase1-max-age-hours",
        type=int,
        default=168,
        help="Maximum age in hours before a Phase 1 suite publication is marked stale.",
    )
    parser.add_argument(
        "--dedupe-suite-mode",
        action="store_true",
        default=False,
        help="Keep only the newest artefact for each (suite_id, mode) pair.",
    )
    args = parser.parse_args()

    artifact_paths = list(args.artifacts)
    if args.artifact_root is not None:
        artifact_paths.extend(_artifact_paths_from_root(args.artifact_root))
    unique_paths = sorted({path.resolve() for path in artifact_paths if path.exists()})
    if not unique_paths:
        raise SystemExit("No benchmark artefact JSON files were supplied.")

    exemplars_by_run: dict[str, list[dict[str, Any]]] = {}
    if args.exemplars is not None:
        exemplars_by_run = _normalize_exemplars(_load_json(args.exemplars))

    publication = build_publication_payload(
        unique_paths,
        note=args.note,
        exemplars_by_run=exemplars_by_run,
        reference_only_suites=args.reference_only_suites,
        max_age_hours=args.phase1_max_age_hours,
        dedupe_suite_mode=args.dedupe_suite_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(publication['runs'])} benchmark runs to {args.output}")


if __name__ == "__main__":
    main()

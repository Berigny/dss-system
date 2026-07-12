"""Generate a Phase 1 benchmark publication refresh for the Control Plane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.benchmarks.publish_dashboard_benchmarks import (
    DEFAULT_DASHBOARD_PUBLICATION_PATH,
    _artifact_paths_from_root,
    build_publication_payload,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="Directory containing canonical benchmark artefact JSON files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DASHBOARD_PUBLICATION_PATH,
        help="Destination publication JSON path.",
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
        help="Optional Phase 1 suite name to keep as reference_only when no canonical run is published.",
    )
    parser.add_argument(
        "--phase1-max-age-hours",
        type=int,
        default=168,
        help="Maximum suite age in hours before the refresh marks the suite stale.",
    )
    args = parser.parse_args()

    artifact_paths = _artifact_paths_from_root(args.artifact_root)
    if not artifact_paths:
        raise SystemExit(f"No benchmark artefact JSON files found under {args.artifact_root}")

    publication = build_publication_payload(
        artifact_paths,
        note=args.note,
        reference_only_suites=args.reference_only_suites,
        max_age_hours=args.phase1_max_age_hours,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    suite_summary = {
        item["suite_name"]: {
            "status": item["status"],
            "freshness_status": item["freshness_status"],
            "published_modes": item["published_modes"],
        }
        for item in publication["phase_1_activation"]["suite_activation"]
    }
    print(f"Published {len(publication['runs'])} benchmark runs to {args.output}")
    print(json.dumps(suite_summary, indent=2))


if __name__ == "__main__":
    main()

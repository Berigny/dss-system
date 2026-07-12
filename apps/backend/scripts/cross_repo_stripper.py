#!/usr/bin/env python3
"""Cross-repo KSR esoteric-language scan.

Scans ds-backend-local, ds-middleware-local, ds-frontend-local, and DSS-Dashboard
public surfaces using the backend KSR glossary. Intended for CI and pre-commit
use across the Dual-Substrate repositories.

Exit code 0 = clean; 1 = critical/high violations found.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow importing backend.kernel.* when running from the script directory.
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.kernel.esoteric_stripper import (  # noqa: E402
    EsotericLanguageStripper,
    StripReport,
    _load_ksr_from_env_or_yaml,
)


def _github_root() -> Path:
    return (REPO_ROOT).resolve().parent


DEFAULT_REPOS = (
    REPO_ROOT,
    _github_root() / "ds-middleware-local",
    _github_root() / "ds-frontend-local",
    _github_root() / "DSS-Dashboard",
)


def _merge_report(target: Path, report: StripReport, aggregate: dict) -> None:
    aggregate["files_processed"] += report.files_processed
    aggregate["files_modified"] += report.files_modified
    for term, count in report.replacements.items():
        aggregate["replacements"][term] = aggregate["replacements"].get(term, 0) + count
    for priority, terms in report.violations.items():
        agg_priority = aggregate["violations"].setdefault(priority, {})
        for term, paths in terms.items():
            agg_priority.setdefault(term, []).extend(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-repo KSR surface scan")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Path to ds-backend-local (holds the KSR)",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=[str(p) for p in DEFAULT_REPOS],
        help="Repo paths to scan",
    )
    parser.add_argument(
        "--report",
        default=str(REPO_ROOT / "backend" / "kernel" / ".ksr" / "cross_repo_strip_report.json"),
        help="Path to write aggregate JSON report",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    ksr_data = _load_ksr_from_env_or_yaml(repo_root)
    stripper = EsotericLanguageStripper(ksr_data)

    aggregate = {
        "repos": {},
        "files_processed": 0,
        "files_modified": 0,
        "replacements": {},
        "violations": {},
    }

    dirty = False
    for target_str in args.targets:
        target = Path(target_str).resolve()
        if not target.exists():
            print(f"WARNING: target not found, skipping: {target}", file=sys.stderr)
            continue
        report = stripper.scan_directory(target, check_only=True)
        aggregate["repos"][target.name] = report.to_dict()
        _merge_report(target, report, aggregate)
        if stripper.has_critical_or_high_violations(report):
            dirty = True

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    print(json.dumps(aggregate, indent=2))

    if dirty:
        print("ERROR: critical or high-priority violations remain.", file=sys.stderr)
        return 1
    print("OK: no critical or high-priority violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Artefact generation: JSON report and markdown summary."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


REPORT_DIR = Path(__file__).resolve().parent.parent / "eval" / "reports"


def write_json_report(report: Dict[str, Any], path: Path = None) -> Path:
    """Write the full report as JSON."""
    path = path or (REPORT_DIR / "benchmark_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    return path


def _badge(status: bool) -> str:
    return "✅ PASS" if status else "❌ FAIL"


def write_markdown_summary(report: Dict[str, Any], path: Path = None) -> Path:
    """Write a human-readable markdown summary."""
    path = path or (REPORT_DIR / "benchmark_summary.md")
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# DSS Benchmark Standalone Report",
        "",
        f"Generated: {report.get('timestamp', 'unknown')}",
        f"Overall: {_badge(report.get('overall_pass', False))}",
        "",
        "## Suites",
        "",
    ]

    for suite in report.get("suites", []):
        lines.append(f"### {suite.get('suite', 'unknown')}")
        for key, value in suite.items():
            if key in ("suite", "seeds", "per_seed"):
                continue
            lines.append(f"- **{key}**: {value}")
        lines.append("")

    lines.append("## Claims Registry")
    lines.append("")
    binding = report.get("claims_binding", {})
    for cid, detail in binding.get("bound", {}).items():
        status = detail.get("status", "pending")
        icon = "✅" if status == "passed" else "❌" if status == "failed" else "⚠️"
        lines.append(
            f"- {icon} `{cid}` = {detail.get('value')} "
            f"({detail.get('operator')} {detail.get('threshold')}) [{status}]"
        )

    if binding.get("unknown_claims"):
        lines.append("")
        lines.append("### Unknown claims detected")
        for cid in binding["unknown_claims"]:
            lines.append(f"- `{cid}`")

    if binding.get("missing_claims"):
        lines.append("")
        lines.append("### Missing claims")
        for cid in binding["missing_claims"]:
            lines.append(f"- `{cid}`")

    lines.append("")
    lines.append(
        "_Limitation: These benchmarks test structural integrity and abstention behavior, "
        "not general retrieval quality on unstructured corpora._"
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def generate_report(suites: List[Dict[str, Any]], claims_binding: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the full report payload."""
    suite_pass = all(s.get("pass", True) for s in suites)
    overall = suite_pass and claims_binding.get("overall", False)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_pass": overall,
        "suites": suites,
        "claims_binding": claims_binding,
    }

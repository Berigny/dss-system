"""CI-enforced claim registry binding and unknown-claim detection."""
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "eval" / "claims_registry.yaml"


def load_registry(path: Path = None) -> Dict[str, Any]:
    """Load the claims registry from YAML."""
    path = path or REGISTRY_PATH
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _claim_id(suite: str, metric: str) -> str:
    return f"{suite}.{metric}"


def bind_claims(registry: Dict[str, Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bind collected suite metrics to the claims registry.

    Returns a dictionary describing each claim's status, evidence, and whether
    any unregistered (unknown) claims were detected.
    """
    claims = {c["id"]: c for c in registry.get("claims", [])}
    unknown_policy = registry.get("unknown_claims_policy", "warn")
    bound: Dict[str, Dict] = {}
    unknown: List[str] = []

    for suite_result in results:
        suite = suite_result.get("suite")
        for metric, value in suite_result.items():
            if metric in ("suite", "seeds", "per_seed"):
                continue
            cid = _claim_id(suite, metric)
            if cid not in claims:
                unknown.append(cid)
                continue

            claim = claims[cid]
            threshold = claim.get("threshold")
            operator = claim.get("operator", ">=")
            status = "recorded"
            if threshold is not None:
                if isinstance(value, bool):
                    status = "passed" if value else "failed"
                elif isinstance(value, (int, float)):
                    if operator == "<=":
                        status = "passed" if value <= threshold else "failed"
                    elif operator == "==":
                        status = "passed" if value == threshold else "failed"
                    elif operator == ">=":
                        status = "passed" if value >= threshold else "failed"
                    else:
                        status = "failed"
                else:
                    status = "failed"
            bound[cid] = {
                "suite": suite,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "operator": operator,
                "status": status,
                "description": claim.get("description", ""),
            }

    # Mark any registered claims that were not produced.
    produced = set(bound.keys())
    missing = [cid for cid in claims if cid not in produced]

    fail_ci = unknown_policy == "fail_ci" and (unknown or missing)

    return {
        "bound": bound,
        "unknown_claims": unknown,
        "missing_claims": missing,
        "unknown_claims_policy": unknown_policy,
        "fail_ci": fail_ci,
    }


def check_registry(registry: Dict[str, Any], results: List[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    """Return (overall_pass, details) for the registry check."""
    binding = bind_claims(registry, results)
    all_passed = all(b["status"] in ("passed", "recorded") for b in binding["bound"].values())
    overall = all_passed and not binding["fail_ci"]
    binding["overall"] = overall
    return overall, binding

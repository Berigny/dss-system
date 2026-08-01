"""CI-enforced claim registry binding and unknown-claim detection."""
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
REGISTRY_PATH = EVAL_DIR / "claims_registry.active.yaml"
MERGED_REGISTRY_PATH = EVAL_DIR / "claims_registry.yaml"
BACKLOG_REGISTRY_PATH = EVAL_DIR / "claims_registry.backlog.yaml"


def load_registry(path: Path = None) -> Dict[str, Any]:
    """Load the claims registry from YAML."""
    path = path or REGISTRY_PATH
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _claim_id(suite: str, metric: str) -> str:
    return f"{suite}.{metric}"


def _evaluate(value: Any, threshold: Any, operator: str) -> str:
    """Evaluate a single value against a claim threshold."""
    if threshold is None:
        return "recorded"
    if isinstance(value, bool):
        return "passed" if value else "failed"
    if isinstance(value, (int, float)):
        if operator == "<=":
            return "passed" if value <= threshold else "failed"
        if operator == "==":
            return "passed" if value == threshold else "failed"
        if operator == ">=":
            return "passed" if value >= threshold else "failed"
        if operator == "<":
            return "passed" if value < threshold else "failed"
        if operator == ">":
            return "passed" if value > threshold else "failed"
        if operator == "!=":
            return "passed" if value != threshold else "failed"
        return "failed"
    return "failed"


def _validate_registry(registry: Dict[str, Any]) -> List[str]:
    """Return structural validation errors for the registry itself."""
    errors: List[str] = []
    claims = registry.get("claims", [])
    if not isinstance(claims, list):
        errors.append("registry 'claims' must be a list")
        return errors
    seen_ids: set[str] = set()
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claim {i} is not a mapping")
            continue
        cid = claim.get("id")
        if not cid:
            errors.append(f"claim {i} missing 'id'")
            continue
        if cid in seen_ids:
            errors.append(f"duplicate claim id: {cid}")
        seen_ids.add(cid)
        for field in ("suite", "metric", "description"):
            if field not in claim:
                errors.append(f"claim {cid} missing '{field}'")
        op = claim.get("operator", ">=")
        if op not in {">=", "<=", "==", "<", ">", "!=", "recorded"}:
            errors.append(f"claim {cid} has unknown operator: {op}")
    return errors


def bind_claims(
    registry: Dict[str, Any],
    results: List[Dict[str, Any]],
    run_suites: set = None,
) -> Dict[str, Any]:
    """Bind collected suite metrics to the claims registry.

    Supports both legacy ``suite.metric`` result keys and explicit ``claim_ids``
    mappings for claim IDs that do not follow the ``suite.metric`` convention.

    If ``run_suites`` is provided, only registered claims whose ``suite`` value
    is in ``run_suites`` are considered missing when not produced. This lets a
    runner that executes only a subset of suites (e.g. a fast smoke run) pass
    without being blocked by claims belonging to suites it did not run.

    Returns a dictionary describing each claim's status, evidence, and whether
    any unregistered (unknown) claims were detected.
    """
    claims = {c["id"]: c for c in registry.get("claims", [])}
    unknown_policy = registry.get("unknown_claims_policy", "warn")
    bound: Dict[str, Dict] = {}
    unknown: List[str] = []

    for suite_result in results:
        suite = suite_result.get("suite")
        explicit_cids = suite_result.get("claim_ids", {})

        # First, bind any explicitly named claim IDs supplied by the suite.
        for cid, value in explicit_cids.items():
            if cid not in claims:
                unknown.append(cid)
                continue
            claim = claims[cid]
            threshold = claim.get("threshold")
            operator = claim.get("operator", ">=")
            status = _evaluate(value, threshold, operator)
            bound[cid] = {
                "suite": claim.get("suite", suite),
                "metric": claim.get("metric", cid),
                "value": value,
                "threshold": threshold,
                "operator": operator,
                "status": status,
                "description": claim.get("description", ""),
            }

        # Then bind legacy suite.metric results.
        for metric, value in suite_result.items():
            if metric in ("suite", "seeds", "per_seed", "claim_ids"):
                continue
            cid = _claim_id(suite, metric)
            if cid not in claims:
                unknown.append(cid)
                continue
            if cid in bound:
                # Already bound explicitly; keep explicit value.
                continue

            claim = claims[cid]
            threshold = claim.get("threshold")
            operator = claim.get("operator", ">=")
            status = _evaluate(value, threshold, operator)
            bound[cid] = {
                "suite": suite,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "operator": operator,
                "status": status,
                "description": claim.get("description", ""),
            }

    # Mark any registered claims that were not produced. When the caller tells
    # us which suites were actually run, restrict the missing-claim check to
    # claims belonging to those suites. Claims for suites that were not run are
    # expected to be missing from this result set (they are validated elsewhere,
    # e.g. by dedicated pytest suites).
    produced = set(bound.keys())
    if run_suites is not None:
        missing = [
            cid for cid in claims
            if cid not in produced and claims[cid].get("suite") in run_suites
        ]
    else:
        missing = [cid for cid in claims if cid not in produced]

    fail_ci = unknown_policy == "fail_ci" and (unknown or missing)

    return {
        "bound": bound,
        "unknown_claims": unknown,
        "missing_claims": missing,
        "unknown_claims_policy": unknown_policy,
        "fail_ci": fail_ci,
    }


def check_registry(
    registry: Dict[str, Any],
    results: List[Dict[str, Any]],
    run_suites: set = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Return (overall_pass, details) for the registry check."""
    registry_errors = _validate_registry(registry)
    binding = bind_claims(registry, results, run_suites=run_suites)
    all_passed = all(b["status"] in ("passed", "recorded") for b in binding["bound"].values())
    overall = all_passed and not binding["fail_ci"] and not registry_errors
    binding["overall"] = overall
    binding["registry_errors"] = registry_errors
    return overall, binding

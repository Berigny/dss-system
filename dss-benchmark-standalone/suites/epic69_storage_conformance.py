"""Epic 69 SlateDB storage migration conformance harness (OD-KS4 re-pin).

This harness verifies the claims that back the Epic 69 storage migration by
inspecting the dss-codebase modules that are present in the local workspace.
Most checks are intentionally lightweight (class/function existence, source
constructs, constants) so the public dss-system benchmark repo can run them
without installing the full private dss-codebase runtime dependencies.

Where modules can be imported cheaply (e.g. the kernel flow-rules and constants
modules have no external deps), the harness imports them.  Where imports would
fail because of missing native packages (slatedb, rocksdict, etc.), it falls
back to AST/source inspection and records the result without crashing.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Mirror the dependency path used by suites/adversarial_peo.py and epic65.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence")
)
sys.path.insert(
    0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "apps" / "backend")
)


SUITE = "epic69_storage"


def _codebase_root() -> Path:
    """Return the local dss-codebase root, allowing an env override."""
    env = os.getenv("DSS_CODEBASE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "dss-codebase"


def _src(rel: str) -> Path | None:
    p = _codebase_root() / rel
    return p if p.exists() else None


def _read_text(rel: str) -> str | None:
    p = _src(rel)
    if p is None:
        return None
    return p.read_text(encoding="utf-8")


def _parse(rel: str) -> ast.AST | None:
    text = _read_text(rel)
    if text is None:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _has_class(tree: ast.AST | None, name: str) -> bool:
    if tree is None:
        return False
    return any(
        isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)
    )


def _has_function(tree: ast.AST | None, name: str) -> bool:
    if tree is None:
        return False
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in ast.walk(tree)
    )


def _contains(rel: str, *needles: str) -> bool:
    text = _read_text(rel)
    if text is None:
        return False
    return all(needle in text for needle in needles)


def _stub_backend_packages() -> None:
    """Insert empty package stubs so lightweight backend submodules import."""
    root = _codebase_root() / "apps" / "backend" / "backend"
    if not root.exists():
        return

    # backend
    if "backend" not in sys.modules:
        backend = types.ModuleType("backend")
        backend.__path__ = [str(root)]
        sys.modules["backend"] = backend

    subpackages = [
        "fieldx_kernel",
        "fieldx_kernel.substrate",
        "kernel",
        "services",
        "context_surface",
        "retrieval",
        "search",
    ]
    for sub in subpackages:
        full = f"backend.{sub}"
        if full not in sys.modules:
            pkg = types.ModuleType(full)
            pkg.__path__ = [str(root / sub.replace(".", "/"))]
            sys.modules[full] = pkg


def _import_flow_rules() -> Any | None:
    try:
        _stub_backend_packages()
        from backend.fieldx_kernel import flow_rules

        return flow_rules
    except Exception:
        return None


def _import_constants() -> Any | None:
    try:
        _stub_backend_packages()
        from backend.kernel import constants

        return constants
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Claim check implementations
# ---------------------------------------------------------------------------


def _check_sto_01() -> bool:
    """SlateDB as canonical ledger substrate (Firestore auth guard)."""
    main = _read_text("apps/backend/backend/main.py")
    manifest = _read_text("scripts/cloud_run_manifest.yaml")
    if main is None or manifest is None:
        return False
    return (
        "from backend.fieldx_kernel.substrate.slatedb_ledger_storage import SlateDBLedgerStorage"
        in main
        and "def _open_slatedb_gcs_storage()" in main
        and "SlateDBLedgerStorage(" in main
        and "LedgerStorageMapping(storage)" in main
        and 'if store_type == "slatedb_gcs":' in main
        and 'if store_type == "slatedb_gcs" and not str(auth_store_uri).startswith("firestore://"):'
        in main
        and "DSS_LEDGER_STORE_TYPE: slatedb_gcs" in manifest
    )


def _check_sto_02() -> bool:
    """Bucket-per-ledger factory."""
    src = _read_text("apps/backend/backend/services/ledger_storage_factory.py")
    tree = _parse("apps/backend/backend/services/ledger_storage_factory.py")
    if src is None:
        return False
    return (
        _has_class(tree, "LedgerStorageFactory")
        and "storage_bucket" in src
        and 'object_store_url=f"gs://{bucket}"' in src
        and "def for_ledger(" in src
    )


def _check_sto_03() -> bool:
    """Canonical Qp retrieval / qp_pure_distance rerank."""
    qp = _read_text("apps/backend/backend/fieldx_kernel/qp_retrieval.py")
    ret = _read_text("apps/backend/backend/context_surface/retrieval.py")
    padic_tree = _parse("apps/backend/backend/fieldx_kernel/substrate/padic_ledger_store.py")
    if qp is None or ret is None:
        return False
    return (
        "def qp_pure_distance(" in qp
        and "def qp_pure_rank_score(" in qp
        and "def derive_query_coordinate_from_primes(" in qp
        and "from backend.fieldx_kernel.qp_retrieval import" in ret
        and "def _rerank_by_qp_distance(" in ret
        and 'row["relevance_basis"] = "qp_distance"' in ret
        and _has_class(padic_tree, "PAdicLedgerStore")
    )


def _check_sto_04() -> bool:
    """Geological tiers on SlateDB via prefix iteration."""
    const = _read_text("apps/backend/backend/kernel/constants.py")
    storage = _read_text("apps/backend/backend/fieldx_kernel/substrate/slatedb_ledger_storage.py")
    ret = _read_text("apps/backend/backend/context_surface/retrieval.py")
    if const is None or storage is None or ret is None:
        return False
    return (
        "LAYER_SAND" in const
        and "LAYER_SILT" in const
        and "LAYER_LOAM" in const
        and "LAYER_CLAY" in const
        and "QUATERNARY_LAYER_ORDER" in const
        and "def keys(self, prefix:" in storage
        and "scan_raw(prefix_bytes)" in storage
        and "overlay:{namespace}:" in ret
    )


def _check_sto_05() -> bool:
    """RocksDB / SQLite / Postgres retired from production."""
    main = _read_text("apps/backend/backend/main.py")
    manifest = _read_text("scripts/cloud_run_manifest.yaml")
    if main is None or manifest is None:
        return False
    # Production target is SlateDB; Cloud SQL annotations and Postgres env gone.
    manifest_clean = (
        "cloudsql_instances" not in manifest
        and "DSS_DATABASE_URL" not in manifest
        and "DSS_LEDGER_STORE_TYPE: slatedb_gcs" in manifest
    )
    # SQLite-on-GCS is no longer a runtime branch.
    no_sqlite_branch = 'if store_type == "sqlite_gcs"' not in main
    return manifest_clean and no_sqlite_branch


def _check_sto_06() -> bool:
    """Cloud Run deployment target."""
    manifest = _read_text("scripts/cloud_run_manifest.yaml")
    backend_deploy = _src("scripts/deploy_dss_backend_cloud_run.sh")
    middleware_deploy = _src("scripts/deploy_dss_middleware_cloud_run.sh")
    if manifest is None:
        return False
    return (
        "project: dss-pilot-prod" in manifest
        and "region: australia-southeast1" in manifest
        and "dss-backend:" in manifest
        and "dss-middleware:" in manifest
        and "max_instances: 2" in manifest
        and backend_deploy is not None
        and middleware_deploy is not None
    )


def _check_sto_09() -> bool:
    """Flow-trace persistence on append (and S2 retrieval trace)."""
    flow_trace = _read_text("apps/backend/backend/fieldx_kernel/substrate/flow_trace.py")
    ledger_v2 = _read_text("apps/backend/backend/fieldx_kernel/substrate/ledger_store_v2.py")
    ret = _read_text("apps/backend/backend/context_surface/retrieval.py")
    if flow_trace is None or ledger_v2 is None or ret is None:
        return False
    return (
        "def s1_write_trace(" in flow_trace
        and "def s2_retrieval_trace(" in flow_trace
        and "from backend.fieldx_kernel.substrate.flow_trace import s1_write_trace" in ledger_v2
        and 'metadata["flow_trace"] = s1_write_trace(' in ledger_v2
        and "s2_retrieval_trace(" in ret
    )


def _check_krn_03() -> bool:
    """Metatron-centroid flow law locked."""
    flow_rules = _import_flow_rules()
    if flow_rules is not None:
        return (
            flow_rules.C_NODE == 99
            and flow_rules.EVEN_SINKS == {0, 2, 4, 6}
            and flow_rules.ODD_BRANCHES == {1, 3, 5, 7}
            and flow_rules.ADJACENCY_RULES[flow_rules.C_NODE] == flow_rules.ODD_BRANCHES
            and all(flow_rules.ADJACENCY_RULES[e] == {flow_rules.C_NODE} for e in flow_rules.EVEN_SINKS)
            and flow_rules.ALLOWED_BRIDGES == {(3, 4), (7, 0)}
            and flow_rules.ALLOWED_TERMINAL_WRAPS == {(3, 0), (7, 4)}
        )
    # Fallback to source inspection if import failed.
    src = _read_text("apps/backend/backend/fieldx_kernel/flow_rules.py")
    if src is None:
        return False
    return (
        "C_NODE = 99" in src
        and "EVEN_SINKS = {0, 2, 4, 6}" in src
        and "ODD_BRANCHES = {1, 3, 5, 7}" in src
        and "ALLOWED_BRIDGES = {(3, 4), (7, 0)}" in src
        and "ALLOWED_TERMINAL_WRAPS = {(3, 0), (7, 4)}" in src
        and "_assert_topology_locked()" in src
    )


def _check_krn_09() -> bool:
    """Unknown body primes degrade lawfulness rather than hard-failing."""
    flow_rules = _import_flow_rules()
    constants = _import_constants()
    schema = _read_text("apps/backend/backend/fieldx_kernel/schema.py")
    if schema is None:
        return False

    # Body-prime reservation is the precondition.
    reservation_ok = (
        constants is not None
        and getattr(constants, "BODY_PRIMES_START", None) == 23
        and "MIN_BODY_PRIME: Final[int] = 23" in schema
    )

    # run_full_check lowers lawfulness for unknown (body) primes.
    if flow_rules is not None:
        lawful, _, _, level = flow_rules.run_full_check([2, 23, 29, 3], 1.0)
        return reservation_ok and lawful and level < 3

    src = _read_text("apps/backend/backend/fieldx_kernel/flow_rules.py")
    if src is None:
        return False
    degrades = (
        "# CASE B: Known -> Body (Creation)" in src
        and "lawfulness_level = min(lawfulness_level, LAW_CONDITIONAL)" in src
        and "# CASE C: Body -> Known (Re-entry)" in src
        and "lawfulness_level = min(lawfulness_level, LAW_MARGINAL)" in src
    )
    return reservation_ok and degrades


# ---------------------------------------------------------------------------
# Public harness API
# ---------------------------------------------------------------------------

_CLAIM_CHECKS: List[Tuple[str, Any]] = [
    ("STO-01", _check_sto_01),
    ("STO-02", _check_sto_02),
    ("STO-03", _check_sto_03),
    ("STO-04", _check_sto_04),
    ("STO-05", _check_sto_05),
    ("STO-06", _check_sto_06),
    ("STO-09", _check_sto_09),
    ("KRN-03", _check_krn_03),
    ("KRN-09", _check_krn_09),
]


class Epic69StorageConformanceHarness:
    """Run Epic 69 storage conformance checks and map results to claim IDs."""

    @classmethod
    def run(cls) -> Dict[str, Any]:
        results: Dict[str, bool] = {}
        for cid, checker in _CLAIM_CHECKS:
            try:
                results[cid] = bool(checker())
            except Exception:
                # A crash is treated as a failed claim; the harness must not raise.
                results[cid] = False
        return {
            "suite": SUITE,
            "claim_ids": results,
            "all_passed": all(results.values()),
        }


def run_epic69_storage_conformance() -> Dict[str, Any]:
    """Entry point used by tests and the benchmark runner."""
    return Epic69StorageConformanceHarness.run()


if __name__ == "__main__":
    print(json.dumps(run_epic69_storage_conformance(), indent=2, sort_keys=True))

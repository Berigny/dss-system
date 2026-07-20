"""Tests for DSS-296 one-click reproduction pipeline."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.pinned_queries import verify_query_manifest


REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent


def _load_entrypoint_module():
    """Load eval/eval_entrypoint.py without requiring it to be on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "dss296_eval_entrypoint",
        REPO_ROOT / "eval" / "eval_entrypoint.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # Register the synthetic module so string annotations can be resolved.
    sys.modules[spec.name] = module
    # The entrypoint imports backend.*, so ensure apps/backend is on the path.
    backend_root = REPO_ROOT / "apps" / "backend"
    shared_types = REPO_ROOT / "packages" / "shared-types" / "src"
    extra_paths = [str(backend_root), str(shared_types)]
    for p in extra_paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_dockerfile_exists() -> None:
    dockerfile = REPO_ROOT / "eval" / "Dockerfile"
    assert dockerfile.exists()
    contents = dockerfile.read_text(encoding="utf-8")
    assert "python:3.11" in contents
    assert "sentence-transformers" in contents


def test_make_eval_target_defined() -> None:
    makefile = REPO_ROOT / "Makefile"
    assert makefile.exists()
    contents = makefile.read_text(encoding="utf-8")
    assert "eval:" in contents
    assert "docker build" in contents or "docker run" in contents


def test_entrypoint_can_be_imported() -> None:
    module = _load_entrypoint_module()
    assert hasattr(module, "run_eval")
    assert hasattr(module, "EvalConfig")


def test_entrypoint_dry_run_produces_valid_artifacts(tmp_path: Path) -> None:
    module = _load_entrypoint_module()
    config = module.EvalConfig(
        output_root=tmp_path,
        dry_run=True,
        skip_real_embedding=True,
        max_events=2000,
    )
    rc = module.run_eval(config)
    assert rc == 0

    # Verify summary manifest and at least one aggregate artifact exist.
    run_dirs = sorted(d for d in tmp_path.iterdir() if d.is_dir())
    assert run_dirs
    run_dir = run_dirs[-1]
    summary_path = run_dir / "summary_manifest.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["benchmarks"]
    assert all(b["status"] == "success" for b in summary["benchmarks"])
    assert summary.get("query_verification", {}).get("status") == "ok"

    # Validate each copied aggregate artifact.
    for artifact_file in run_dir.glob("*_aggregate.json"):
        payload = json.loads(artifact_file.read_text(encoding="utf-8"))
        validated = validate_benchmark_artifact(payload)
        assert validated.status == "success"


def test_corpus_manifest_is_valid() -> None:
    module = _load_entrypoint_module()
    verification = module._verify_corpus_manifest()
    assert verification["status"] == "ok"
    for filename, info in verification["files"].items():
        assert info["valid"] is True, f"{filename} SHA256 mismatch"


def test_query_manifest_is_valid() -> None:
    verification = verify_query_manifest()
    assert verification["status"] == "ok"
    for filename, info in verification["files"].items():
        assert info["valid"] is True, f"{filename} SHA256 mismatch"


def test_pinned_queries_used_in_dry_run(tmp_path: Path) -> None:
    """Dry-run should call the pinned-query loader for each wired harness."""
    from backend.benchmarks import (
        dss292_known_unknown_benchmark as dss292_mod,
        dss293_adversarial_poisoning_benchmark as dss293_mod,
        dss294_bm25_baseline as dss294_mod,
        dss295_latency_storage_benchmark as dss295_mod,
    )
    from backend.benchmarks.pinned_queries import load_pinned_queries_for_config

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _counting_loader(*args, **kwargs):
        calls.append((args, kwargs))
        return load_pinned_queries_for_config(*args, **kwargs)

    module = _load_entrypoint_module()
    config = module.EvalConfig(
        output_root=tmp_path,
        dry_run=True,
        skip_real_embedding=True,
        max_events=2000,
    )

    with patch.object(dss292_mod, "load_pinned_queries_for_config", _counting_loader), \
         patch.object(dss293_mod, "load_pinned_queries_for_config", _counting_loader), \
         patch.object(dss294_mod, "load_pinned_queries_for_config", _counting_loader), \
         patch.object(dss295_mod, "load_pinned_queries_for_config", _counting_loader):
        rc = module.run_eval(config)

    assert rc == 0
    assert len(calls) >= 4, "pinned query loader was not invoked for all wired harnesses"
    suite_ids = {args[0] for args, _ in calls}
    assert "dss292-known-unknown" in suite_ids
    assert "dss293-adversarial-poisoning" in suite_ids
    assert "dss294-bm25-ranking" in suite_ids
    assert "dss295-latency-storage" in suite_ids


def test_dockerfile_build_syntax(tmp_path: Path) -> None:
    """Verify Dockerfile can at least be parsed by docker build --dry-run."""
    if os.environ.get("SKIP_DOCKER_TEST"):
        pytest.skip("SKIP_DOCKER_TEST set")
    result = subprocess.run(
        ["docker", "build", "-f", str(REPO_ROOT / "eval" / "Dockerfile"), "--dry-run", str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    # docker build --dry-run returns 0 on recent Docker; if the option is not
    # supported, we accept the command but do not fail the test.
    if result.returncode != 0 and "--dry-run" in result.stderr:
        pytest.skip("docker build --dry-run not supported by local Docker")
    assert result.returncode == 0 or "dry-run" in result.stderr

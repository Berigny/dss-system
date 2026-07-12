"""Persisted benchmark publication jobs for Control Plane operator triggers."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.benchmarks.operator_publication import build_operator_publication_contract
from backend.benchmarks.canonical_publication_source import (
    build_canonical_publication_source_contract,
)
from backend.benchmarks.publish_dashboard_benchmarks import _artifact_paths_from_root, build_publication_payload
from backend.benchmarks.artifact_schema import BenchmarkArtifact, validate_benchmark_artifact
from backend.benchmarks.phase1_activation import PHASE1_REQUIRED_MODES
from backend.benchmarks.run_dual_retrieval_benchmark import run_phase1_suite_benchmark, write_benchmark_artifact


BENCHMARK_PUBLICATION_JOBS_V1_KEY = b"__benchmark_publication_jobs_v1__"

PUBLICATION_FAILURE_MESSAGES: dict[str, str] = {
    "benchmark_artifact_root_not_configured": "Benchmark artefact storage is not configured.",
    "benchmark_publication_output_not_configured": "Canonical publication output is not configured.",
    "benchmark_execution_failed": "Benchmark execution failed before publication could complete.",
    "benchmark_artifact_write_failed": "Benchmark artefact persistence failed before publication could complete.",
    "benchmark_publication_refresh_failed": "Canonical benchmark publication refresh failed.",
    "no_valid_benchmark_artifacts_found": "No valid benchmark artefacts were found for publication.",
    "canonical_publication_unavailable": "Canonical publication is not currently available.",
    "canonical_publication_unpublished": "No benchmark publication has been published yet.",
}

DEFAULT_BENCHMARK_STORAGE_ROOT = Path("/app/data/benchmarks")
DEFAULT_BENCHMARK_ARTIFACT_ROOT = DEFAULT_BENCHMARK_STORAGE_ROOT / "artifacts"
DEFAULT_BENCHMARK_PUBLICATION_OUTPUT = DEFAULT_BENCHMARK_STORAGE_ROOT / "benchmark_runs.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _failure_code_and_message(raw_reason: Any) -> tuple[str | None, str | None]:
    text = str(raw_reason or "").strip()
    if not text:
        return None, None
    code = text.split(":", 1)[0].strip()
    message = PUBLICATION_FAILURE_MESSAGES.get(code)
    if not message:
        return code or None, text
    return code or None, message


def _load_jobs(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(BENCHMARK_PUBLICATION_JOBS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    jobs: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            jobs[str(key)] = dict(record)
    return jobs


def _persist_jobs(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[BENCHMARK_PUBLICATION_JOBS_V1_KEY] = json.dumps(
        {"version": 1, "jobs": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _allowed_domains() -> set[str]:
    contract = build_operator_publication_contract()
    groups = contract.get("domain_groups") if isinstance(contract, dict) else []
    return {
        str(item.get("domain_key"))
        for item in groups
        if isinstance(item, dict) and str(item.get("domain_key") or "").strip()
    }


def _mounted_benchmark_storage_available() -> bool:
    return DEFAULT_BENCHMARK_STORAGE_ROOT.parent.exists()


def _artifact_root_value() -> tuple[str, bool]:
    configured = str(os.getenv("BENCHMARK_ARTIFACT_ROOT") or "").strip()
    if configured:
        return configured, False
    if _mounted_benchmark_storage_available():
        return str(DEFAULT_BENCHMARK_ARTIFACT_ROOT), True
    return "", False


def _publication_output_value() -> tuple[str, bool]:
    configured = str(os.getenv("BENCHMARK_PUBLICATION_OUTPUT") or "").strip()
    if configured:
        return configured, False
    if _mounted_benchmark_storage_available():
        return str(DEFAULT_BENCHMARK_PUBLICATION_OUTPUT), True
    return "", False


def _configured_artifact_root() -> Path:
    configured, _ = _artifact_root_value()
    if not configured:
        raise RuntimeError("benchmark_artifact_root_not_configured")
    return Path(configured)


def _configured_publication_output() -> Path:
    configured, _ = _publication_output_value()
    if not configured:
        raise RuntimeError("benchmark_publication_output_not_configured")
    return Path(configured)


def benchmark_publication_runtime_config() -> dict[str, Any]:
    artifact_root, artifact_root_defaulted = _artifact_root_value()
    publication_output, publication_output_defaulted = _publication_output_value()
    return {
        "artifact_root": {
            "configured": bool(artifact_root),
            "path": artifact_root or None,
            "source": "default" if artifact_root and artifact_root_defaulted else ("env" if artifact_root else None),
        },
        "publication_output": {
            "configured": bool(publication_output),
            "path": publication_output or None,
            "source": "default" if publication_output and publication_output_defaulted else ("env" if publication_output else None),
        },
    }


def _reference_only_suites_from_env() -> list[str]:
    raw = str(os.getenv("BENCHMARK_REFERENCE_ONLY_SUITES") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _phase1_max_age_hours_from_env() -> int:
    raw = str(os.getenv("BENCHMARK_PHASE1_MAX_AGE_HOURS") or "").strip()
    if not raw:
        return 168
    try:
        return max(1, int(raw))
    except ValueError:
        return 168


def _valid_artifact_paths(root: Path) -> list[Path]:
    valid: list[Path] = []
    for path in _artifact_paths_from_root(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_benchmark_artifact(payload)
        except Exception:
            continue
        valid.append(path)
    return valid


def _domain_group(domain_key: str) -> dict[str, Any] | None:
    contract = build_operator_publication_contract()
    groups = contract.get("domain_groups") if isinstance(contract, dict) else []
    return next(
        (item for item in groups if isinstance(item, dict) and str(item.get("domain_key") or "").strip() == str(domain_key or "").strip()),
        None,
    )


def _execute_phase1_domain_benchmarks(
    *,
    domain_key: str,
    artifact_root: Path,
) -> list[tuple[BenchmarkArtifact, Path]]:
    group = _domain_group(domain_key)
    if not isinstance(group, dict):
        return []
    trigger_mode = str(group.get("trigger_mode") or "").strip()
    suite_names = [str(item).strip() for item in (group.get("suite_names") or []) if str(item).strip()]
    if trigger_mode != "execute_and_publish" or not suite_names:
        return []

    repo_sha = str(os.getenv("GIT_SHA") or "unknown").strip() or "unknown"
    artefact_schema_version = str(os.getenv("BENCHMARK_ARTEFACT_SCHEMA_VERSION") or "1.0.0").strip() or "1.0.0"
    top_k_raw = str(os.getenv("BENCHMARK_TOP_K") or "10").strip()
    try:
        top_k = max(1, int(top_k_raw))
    except ValueError:
        top_k = 10

    outputs: list[tuple[BenchmarkArtifact, Path]] = []
    for suite_name in suite_names:
        for mode in PHASE1_REQUIRED_MODES:
            artifact, path = run_phase1_suite_benchmark(
                suite_name=suite_name,
                mode=mode,
                output_root=artifact_root,
                repo_sha=repo_sha,
                artefact_schema_version=artefact_schema_version,
                top_k=top_k,
                write_output=False,
            )
            outputs.append((artifact, path))
    return outputs


def _persist_phase1_domain_outputs(outputs: list[tuple[BenchmarkArtifact, Path]]) -> list[Path]:
    written: list[Path] = []
    for artifact, output_path in outputs:
        write_benchmark_artifact(artifact, output_path)
        written.append(output_path)
    return written


def _failed_execution_outputs(outputs: list[tuple[BenchmarkArtifact, Path]]) -> list[str]:
    failures: list[str] = []
    for artifact, _output_path in outputs:
        if str(getattr(artifact, "status", "") or "").strip() != "failed":
            continue
        failure_reason = str(getattr(artifact, "failure_reason", "") or "").strip() or "unknown_failure"
        failures.append(f"{artifact.run_id}:{failure_reason}")
    return failures


def _refresh_publication_from_artifacts(*, domain_key: str) -> dict[str, Any]:
    artifact_root = _configured_artifact_root()
    output_path = _configured_publication_output()
    artifact_paths = _valid_artifact_paths(artifact_root)
    if not artifact_paths:
        raise RuntimeError(f"no_valid_benchmark_artifacts_found:{artifact_root}")
    publication = build_publication_payload(
        artifact_paths,
        note="Published benchmark artefacts are read-only trust material. The Control Plane renders them but does not execute benchmark runs.",
        reference_only_suites=_reference_only_suites_from_env(),
        max_age_hours=_phase1_max_age_hours_from_env(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    return {
        "kind": "canonical_dashboard_feed",
        "target": str(output_path),
        "domain_key": domain_key,
        "published_at": _now_iso(),
        "artifact_root": str(artifact_root),
        "run_count": len(publication.get("runs") or []),
        "phase_1_activation_checked_at": str(
            ((publication.get("phase_1_activation") or {}).get("publication_checked_at") or "")
        ).strip(),
    }


def get_canonical_publication_snapshot() -> dict[str, Any]:
    config = benchmark_publication_runtime_config()
    source_contract = build_canonical_publication_source_contract()
    output_path_text = str((config.get("publication_output") or {}).get("path") or "").strip()
    if not output_path_text:
        reason = "benchmark_publication_output_not_configured"
        _, message = _failure_code_and_message(reason)
        return {
            "status": "unavailable",
            "reason": reason,
            "message": message,
            "config": config,
            "source_contract": source_contract,
            "publication": None,
        }
    output_path = Path(output_path_text)
    if not output_path.exists():
        reason = "canonical_publication_unpublished"
        _, message = _failure_code_and_message(reason)
        return {
            "status": "unpublished",
            "reason": reason,
            "message": message,
            "config": config,
            "source_contract": source_contract,
            "publication": {
                "runs": [],
                "note": message,
                "canonical_publication_source": source_contract,
            },
        }
    try:
        publication = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        reason = "canonical_publication_unavailable"
        _, message = _failure_code_and_message(reason)
        return {
            "status": "unavailable",
            "reason": reason,
            "message": message,
            "config": config,
            "source_contract": source_contract,
            "publication": None,
        }
    return {
        "status": "ok",
        "reason": None,
        "config": config,
        "source_contract": source_contract,
        "publication": publication,
    }


def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    return {
        "job_id": job.get("job_id"),
        "domain_key": job.get("domain_key"),
        "domain_label": job.get("domain_label"),
        "status": job.get("status"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "updated_at": job.get("updated_at"),
        "failure_code": job.get("failure_code"),
        "failure_message": job.get("failure_message"),
        "failure_detail": job.get("failure_detail"),
        "publication_reference": job.get("publication_reference"),
        "operator_identity": dict(job.get("operator_identity") or {}),
    }


def enqueue_benchmark_publication_job(
    db: Any,
    *,
    domain_key: str,
    operator_identity: dict[str, Any],
) -> dict[str, Any]:
    normalized_domain = str(domain_key or "").strip()
    if normalized_domain not in _allowed_domains():
        raise ValueError(f"unsupported_domain:{normalized_domain or 'unknown'}")
    contract = build_operator_publication_contract()
    groups = contract.get("domain_groups") if isinstance(contract, dict) else []
    group = next(
        (item for item in groups if isinstance(item, dict) and str(item.get("domain_key") or "").strip() == normalized_domain),
        None,
    )
    now = _now_iso()
    job_id = f"benchpub:{uuid4().hex[:16]}"
    job = {
        "job_id": job_id,
        "domain_key": normalized_domain,
        "domain_label": str(group.get("label") or normalized_domain) if isinstance(group, dict) else normalized_domain,
        "status": "queued",
        "queued_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
        "failure_code": None,
        "failure_message": None,
        "failure_detail": None,
        "publication_reference": None,
        "operator_identity": {
            "principal_id": str(operator_identity.get("principal_id") or "").strip() or "anonymous",
            "principal_type": str(operator_identity.get("principal_type") or "").strip() or "unknown",
            "principal_did": str(operator_identity.get("principal_did") or "").strip() or None,
        },
    }
    jobs = _load_jobs(db)
    jobs[job_id] = job
    persisted = _persist_jobs(db, jobs)
    return _job_summary(persisted.get(job_id, job)) or dict(job)


def get_benchmark_publication_job(db: Any, job_id: str) -> dict[str, Any] | None:
    jobs = _load_jobs(db)
    return _job_summary(jobs.get(str(job_id).strip()))


def _persist_job_status(
    db: Any,
    *,
    job_id: str,
    status: str,
    started_at: str | None = None,
) -> dict[str, Any] | None:
    jobs = _load_jobs(db)
    current = jobs.get(str(job_id).strip())
    if not isinstance(current, dict):
        return None
    updated_at = _now_iso()
    job = dict(current)
    job["status"] = status
    job["started_at"] = job.get("started_at") or started_at
    job["updated_at"] = updated_at
    jobs[str(job_id).strip()] = job
    persisted = _persist_jobs(db, jobs)
    return persisted.get(str(job_id).strip(), job)


def run_benchmark_publication_job(db: Any, *, job_id: str) -> dict[str, Any] | None:
    jobs = _load_jobs(db)
    current = jobs.get(str(job_id).strip())
    if not isinstance(current, dict):
        return None
    started_at = _now_iso()
    running = _persist_job_status(
        db,
        job_id=str(job_id).strip(),
        status="running_benchmarks",
        started_at=started_at,
    )
    if not isinstance(running, dict):
        return None

    try:
        artifact_root = _configured_artifact_root()
        outputs = _execute_phase1_domain_benchmarks(
            domain_key=str(running.get("domain_key") or "").strip(),
            artifact_root=artifact_root,
        )
        execution_failures = _failed_execution_outputs(outputs)
        if execution_failures:
            raise RuntimeError("benchmark_execution_failed:" + "; ".join(execution_failures))
    except Exception as exc:
        failed_at = _now_iso()
        failed = dict(running)
        failure_code, failure_message = _failure_code_and_message(exc)
        failed["status"] = "failed"
        failed["completed_at"] = failed_at
        failed["updated_at"] = failed_at
        failed["failure_code"] = failure_code
        failed["failure_message"] = failure_message
        failed["failure_detail"] = str(exc)
        jobs[str(job_id).strip()] = failed
        persisted = _persist_jobs(db, jobs)
        return _job_summary(persisted.get(str(job_id).strip(), failed))

    writing = _persist_job_status(
        db,
        job_id=str(job_id).strip(),
        status="writing_artefacts",
    )
    if not isinstance(writing, dict):
        return None
    try:
        _persist_phase1_domain_outputs(outputs)
    except Exception as exc:
        failed_at = _now_iso()
        failed = dict(writing)
        failure_code, failure_message = _failure_code_and_message(f"benchmark_artifact_write_failed:{exc}")
        failed["status"] = "failed"
        failed["completed_at"] = failed_at
        failed["updated_at"] = failed_at
        failed["failure_code"] = failure_code
        failed["failure_message"] = failure_message
        failed["failure_detail"] = str(exc)
        jobs = _load_jobs(db)
        jobs[str(job_id).strip()] = failed
        persisted = _persist_jobs(db, jobs)
        return _job_summary(persisted.get(str(job_id).strip(), failed))

    publishing = _persist_job_status(
        db,
        job_id=str(job_id).strip(),
        status="publishing",
    )
    if not isinstance(publishing, dict):
        return None
    try:
        publication_reference = _refresh_publication_from_artifacts(
            domain_key=str(publishing.get("domain_key") or "").strip()
        )
    except Exception as exc:
        failed_at = _now_iso()
        failed = dict(publishing)
        failure_code, failure_message = _failure_code_and_message(
            f"benchmark_publication_refresh_failed:{exc}"
        )
        failed["status"] = "failed"
        failed["completed_at"] = failed_at
        failed["updated_at"] = failed_at
        failed["failure_code"] = failure_code
        failed["failure_message"] = failure_message
        failed["failure_detail"] = str(exc)
        jobs = _load_jobs(db)
        jobs[str(job_id).strip()] = failed
        persisted = _persist_jobs(db, jobs)
        return _job_summary(persisted.get(str(job_id).strip(), failed))

    completed_at = _now_iso()
    published = dict(publishing)
    published["status"] = "published"
    published["completed_at"] = completed_at
    published["updated_at"] = completed_at
    published["failure_code"] = None
    published["failure_message"] = None
    published["publication_reference"] = publication_reference
    jobs[str(job_id).strip()] = published
    persisted = _persist_jobs(db, jobs)
    return _job_summary(persisted.get(str(job_id).strip(), published))

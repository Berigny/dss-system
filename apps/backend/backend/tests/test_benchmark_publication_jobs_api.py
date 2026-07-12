from __future__ import annotations

import json
from pathlib import Path
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import control_plane_router
from backend.services.benchmark_publication_jobs import BENCHMARK_PUBLICATION_JOBS_V1_KEY
from backend.services.session_tokens import mint_session_token


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(control_plane_router)
    return TestClient(app)


def _session_headers(principal_did: str = "did:key:z6MkReader") -> dict[str, str]:
    token = mint_session_token(principal_did=principal_did)["token"]
    return {"x-session-token": token}


def test_control_plane_benchmark_publication_jobs_acknowledge_and_persist(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))
        monkeypatch.setenv("GIT_SHA", "testsha123")

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
            "x-principal-did": "did:key:z6MkOpsAdmin",
        }

        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["job"]["domain_key"] == "retrieval"
        assert body["job"]["status"] == "queued"
        assert body["job"]["publication_reference"] is None
        assert body["job"]["operator_identity"]["principal_id"] == "ops-admin"

        job_id = body["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        job_body = job_status.json()["job"]
        assert job_body["job_id"] == job_id
        assert job_body["status"] == "published"
        assert job_body["publication_reference"]["kind"] == "canonical_dashboard_feed"
        assert job_body["publication_reference"]["domain_key"] == "retrieval"
        assert job_body["publication_reference"]["target"] == str(output_path)
        assert output_path.exists()
        publication = json.loads(output_path.read_text(encoding="utf-8"))
        assert "phase_1_activation" in publication
        assert "operator_publication" in publication

        persisted = json.loads(client.app.state.db[BENCHMARK_PUBLICATION_JOBS_V1_KEY].decode())
        assert job_id in persisted["jobs"]


def test_control_plane_benchmark_publication_jobs_execute_retrieval_domain_when_artifact_root_starts_empty(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))
        monkeypatch.setenv("GIT_SHA", "testsha123")

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
            "x-principal-did": "did:key:z6MkOpsAdmin",
        }
        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        body = job_status.json()["job"]
        assert body["status"] == "published"
        assert output_path.exists()
        artefacts = sorted(artifact_root.rglob("*.json"))
        assert len(artefacts) == 9


def test_control_plane_benchmark_publication_jobs_execute_memory_domain_when_artifact_root_starts_empty(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))
        monkeypatch.setenv("GIT_SHA", "testsha123")

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
            "x-principal-did": "did:key:z6MkOpsAdmin",
        }
        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "memory_traceability"},
            headers=headers,
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        body = job_status.json()["job"]
        assert body["status"] == "published"
        assert output_path.exists()
        artefacts = sorted(artifact_root.rglob("*.json"))
        assert len(artefacts) == 6


def test_control_plane_benchmark_publication_jobs_reject_unknown_domain(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    response = client.post(
        "/api/control-plane/benchmarks/publication-jobs",
        json={"domain_key": "unknown_domain"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unsupported_domain"


def test_control_plane_benchmark_publication_jobs_require_operator(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    client = _make_client()
    response = client.post(
        "/api/control-plane/benchmarks/publication-jobs",
        json={"domain_key": "retrieval"},
        headers={"x-principal-id": "anonymous", "x-principal-type": "human"},
    )
    assert response.status_code == 403


def test_control_plane_benchmark_publication_jobs_fail_when_refresh_is_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.delenv("BENCHMARK_ARTIFACT_ROOT", raising=False)
    monkeypatch.delenv("BENCHMARK_PUBLICATION_OUTPUT", raising=False)

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    response = client.post(
        "/api/control-plane/benchmarks/publication-jobs",
        json={"domain_key": "retrieval"},
        headers=headers,
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["job_id"]
    job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
    assert job_status.status_code == 200
    body = job_status.json()["job"]
    assert body["status"] == "failed"
    assert body["failure_code"] == "benchmark_artifact_root_not_configured"
    assert body["failure_message"] == "Benchmark artefact storage is not configured."
    assert "benchmark_artifact_root_not_configured" in str(body["failure_detail"])


def test_control_plane_benchmark_publication_jobs_surface_execution_failure(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))

        def _boom(**_kwargs):
            raise RuntimeError("benchmark_execution_failed:synthetic_execution_failure")

        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._execute_phase1_domain_benchmarks",
            _boom,
        )

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
        }
        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        body = job_status.json()["job"]
        assert body["status"] == "failed"
        assert body["failure_code"] == "benchmark_execution_failed"
        assert body["failure_message"] == "Benchmark execution failed before publication could complete."


def test_control_plane_benchmark_publication_jobs_surface_artifact_write_failure(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))

        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._execute_phase1_domain_benchmarks",
            lambda **_kwargs: [],
        )

        def _write_boom(_outputs):
            raise OSError("synthetic_artifact_write_failure")

        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._persist_phase1_domain_outputs",
            _write_boom,
        )

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
        }
        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        body = job_status.json()["job"]
        assert body["status"] == "failed"
        assert body["failure_code"] == "benchmark_artifact_write_failed"
        assert body["failure_message"] == "Benchmark artefact persistence failed before publication could complete."


def test_control_plane_benchmark_publication_jobs_surface_publication_refresh_failure(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))

        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._execute_phase1_domain_benchmarks",
            lambda **_kwargs: [],
        )
        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._persist_phase1_domain_outputs",
            lambda _outputs: [],
        )

        def _publish_boom(**_kwargs):
            raise RuntimeError("no_valid_benchmark_artifacts_found:/tmp/synthetic")

        monkeypatch.setattr(
            "backend.services.benchmark_publication_jobs._refresh_publication_from_artifacts",
            _publish_boom,
        )

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
        }
        response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert response.status_code == 200
        job_id = response.json()["job"]["job_id"]
        job_status = client.get(f"/api/control-plane/benchmarks/publication-jobs/{job_id}", headers=headers)
        assert job_status.status_code == 200
        body = job_status.json()["job"]
        assert body["status"] == "failed"
        assert body["failure_code"] == "benchmark_publication_refresh_failed"
        assert body["failure_message"] == "Canonical benchmark publication refresh failed."
        assert "no_valid_benchmark_artifacts_found" in str(body["failure_detail"])


def test_control_plane_benchmark_publication_jobs_allow_bootstrap_operator_did(monkeypatch) -> None:
    artifact_root = Path("backend/benchmarks").resolve()
    output_path = Path("/tmp/test-benchmark-publication-bootstrap.json")
    if output_path.exists():
        output_path.unlink()
    monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    client = _make_client()
    headers = {
        "x-principal-id": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
        "x-principal-type": "human",
        "x-principal-did": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
    }

    response = client.post(
        "/api/control-plane/benchmarks/publication-jobs",
        json={"domain_key": "retrieval"},
        headers=headers,
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["job_id"]
    status_response = client.get(
        f"/api/control-plane/benchmarks/publication-jobs/{job_id}",
        headers=headers,
    )
    assert status_response.status_code == 200


def test_control_plane_canonical_benchmark_publication_reads_published_output(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_root = Path(tmpdir) / "artifacts"
        output_path = Path(tmpdir) / "benchmark_runs.json"
        monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(artifact_root))
        monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))
        monkeypatch.setenv("GIT_SHA", "testsha123")

        client = _make_client()
        headers = {
            "x-principal-id": "ops-admin",
            "x-principal-type": "admin",
            "x-principal-did": "did:key:z6MkOpsAdmin",
        }

        create_response = client.post(
            "/api/control-plane/benchmarks/publication-jobs",
            json={"domain_key": "retrieval"},
            headers=headers,
        )
        assert create_response.status_code == 200
        job_id = create_response.json()["job"]["job_id"]
        status_response = client.get(
            f"/api/control-plane/benchmarks/publication-jobs/{job_id}",
            headers=headers,
        )
        assert status_response.status_code == 200
        assert status_response.json()["job"]["status"] == "published"

        publication_response = client.get("/api/control-plane/benchmarks/publication", headers=_session_headers())
        assert publication_response.status_code == 200
        body = publication_response.json()
        assert body["status"] == "ok"
        assert body["config"]["artifact_root"]["configured"] is True
        assert body["config"]["publication_output"]["configured"] is True
        assert body["source_contract"]["canonical_publication_owner"] == "ds_backend_local"
        assert "runs" in body["publication"]


def test_control_plane_canonical_benchmark_publication_reports_missing_output_config(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(Path("backend/benchmarks").resolve()))
    monkeypatch.delenv("BENCHMARK_PUBLICATION_OUTPUT", raising=False)

    client = _make_client()
    response = client.get("/api/control-plane/benchmarks/publication", headers=_session_headers())
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "benchmark_publication_output_not_configured"
    assert body["message"] == "Canonical publication output is not configured."
    assert body["config"]["artifact_root"]["configured"] is True
    assert body["config"]["publication_output"]["configured"] is False


def test_control_plane_canonical_benchmark_publication_reports_unpublished_when_feed_missing(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_ARTIFACT_ROOT", str(Path("backend/benchmarks").resolve()))
    output_path = Path("/tmp/test-canonical-benchmark-publication-unpublished.json")
    if output_path.exists():
        output_path.unlink()
    monkeypatch.setenv("BENCHMARK_PUBLICATION_OUTPUT", str(output_path))

    client = _make_client()
    response = client.get("/api/control-plane/benchmarks/publication", headers=_session_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unpublished"
    assert body["reason"] == "canonical_publication_unpublished"
    assert body["message"] == "No benchmark publication has been published yet."
    assert body["publication"]["runs"] == []

"""Replay sampled production requests asynchronously across benchmark modes."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field, ValidationError

from backend.benchmarks.artifact_schema import BenchmarkArtifact, BenchmarkMode
from backend.benchmarks.publish_dashboard_benchmarks import build_publication_payload
from backend.benchmarks.run_dual_retrieval_benchmark import (
    MODE_CONFIG,
    BenchmarkMemoryService,
    QuerySpec,
    RunnerConfig,
    build_failed_artifact,
    evaluate,
)

DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "shadow_replay_benchmarks"


class ShadowReplaySample(BaseModel):
    sample_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    principal_id: str | None = None
    principal_hash: str | None = None
    source_mode: str = Field(min_length=1)
    source_build_sha: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    query: str = Field(min_length=1)
    memories: list[str | dict[str, Any]] = Field(min_length=1)
    relevant: list[str] = Field(default_factory=list)
    captured_at: datetime


@dataclass(frozen=True)
class ReplaySummary:
    mode: BenchmarkMode
    artifact: BenchmarkArtifact


def load_shadow_samples(path: Path) -> list[ShadowReplaySample]:
    if not path.exists():
        raise FileNotFoundError(f"Shadow replay sample file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("samples", [])
    if not isinstance(rows, list):
        raise ValueError("shadow replay payload must be a list or an object with a 'samples' list")
    return [ShadowReplaySample.model_validate(item) for item in rows if isinstance(item, dict)]


def _memory_texts(memories: Sequence[str | dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in memories:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = ""
        if text:
            values.append(text)
    return values


def build_shadow_dataset(samples: Iterable[ShadowReplaySample]) -> tuple[BenchmarkMemoryService, list[QuerySpec]]:
    service = BenchmarkMemoryService()
    seen_entities: set[str] = set()
    specs: list[QuerySpec] = []
    for sample in samples:
        memory_texts = _memory_texts(sample.memories)
        if not memory_texts:
            raise ValueError(f"shadow replay sample {sample.sample_id} has no replayable memories")
        if sample.entity not in seen_entities:
            service.clear_entity(sample.entity)
            seen_entities.add(sample.entity)
        for text in memory_texts:
            service.anchor_memory(entity=sample.entity, text=text)
        specs.append(
            QuerySpec(
                entity=sample.entity,
                query=sample.query,
                relevant_texts={item for item in sample.relevant if item},
            )
        )
    return service, specs


def build_shadow_artifact(
    *,
    mode: BenchmarkMode,
    samples: Sequence[ShadowReplaySample],
    repo_sha: str,
    executed_at: datetime,
    top_k: int,
    suite_version: str,
    result: Any | None = None,
    failure_reason: str | None = None,
) -> BenchmarkArtifact:
    config = RunnerConfig(
        mode=mode,
        suite_id="shadow_replay_benchmark",
        suite_version=suite_version,
        dataset_version="prod-sampled-v1",
        top_k=top_k,
    )
    synthetic_dataset_path = Path("shadow_replay_samples.json")
    if failure_reason is not None:
        artifact = build_failed_artifact(
            config=config,
            dataset_path=synthetic_dataset_path,
            executed_at=executed_at,
            repo_sha=repo_sha,
            artefact_schema_version="1.0.0",
            failure_reason=failure_reason,
        )
    else:
        from backend.benchmarks.run_dual_retrieval_benchmark import build_artifact

        artifact = build_artifact(
            result,
            config=config,
            dataset_path=synthetic_dataset_path,
            executed_at=executed_at,
            repo_sha=repo_sha,
            artefact_schema_version="1.0.0",
        )
    sample_ids = ",".join(sample.sample_id for sample in samples[:20])
    source_modes = ",".join(sorted({sample.source_mode for sample in samples}))
    source_builds = ",".join(sorted({sample.source_build_sha for sample in samples}))
    surfaces = ",".join(sorted({sample.surface for sample in samples}))
    artifact.run_config.update(
        {
            "evidence_source": "shadow_replay",
            "sample_count": len(samples),
            "sample_ids": sample_ids or "none",
            "source_modes": source_modes or "unknown",
            "source_build_shas": source_builds or "unknown",
            "source_surfaces": surfaces or "unknown",
            "replay_mode_label": str(MODE_CONFIG[mode]["label"]),
        }
    )
    return artifact


def replay_mode(
    samples: Sequence[ShadowReplaySample],
    *,
    mode: BenchmarkMode,
    repo_sha: str,
    top_k: int,
    suite_version: str,
    executed_at: datetime,
) -> ReplaySummary:
    try:
        service, specs = build_shadow_dataset(samples)
        if not specs:
            raise ValueError("shadow replay received zero replayable samples")
        result = evaluate(service, specs, mode=mode, top_k=top_k)
        artifact = build_shadow_artifact(
            mode=mode,
            samples=samples,
            repo_sha=repo_sha,
            executed_at=executed_at,
            top_k=top_k,
            suite_version=suite_version,
            result=result,
        )
    except Exception as exc:
        artifact = build_shadow_artifact(
            mode=mode,
            samples=samples,
            repo_sha=repo_sha,
            executed_at=executed_at,
            top_k=top_k,
            suite_version=suite_version,
            failure_reason=str(exc),
        )
    return ReplaySummary(mode=mode, artifact=artifact)


def run_shadow_replay(
    samples: Sequence[ShadowReplaySample],
    *,
    repo_sha: str,
    modes: Sequence[BenchmarkMode] = ("semantic_only", "coordinate_guided", "full_dss"),
    top_k: int = 10,
    suite_version: str = "v1",
    executed_at: datetime | None = None,
    max_workers: int = 3,
) -> list[BenchmarkArtifact]:
    timestamp = executed_at or datetime.now(timezone.utc)
    artifacts: dict[str, BenchmarkArtifact] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shadow-replay") as pool:
        futures = {
            pool.submit(
                replay_mode,
                samples,
                mode=mode,
                repo_sha=repo_sha,
                top_k=top_k,
                suite_version=suite_version,
                executed_at=timestamp,
            ): mode
            for mode in modes
        }
        for future in as_completed(futures):
            summary = future.result()
            artifacts[summary.mode] = summary.artifact
    return [artifacts[mode] for mode in modes if mode in artifacts]


def write_artifacts(artifacts: Sequence[BenchmarkArtifact], output_root: Path) -> list[Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for artifact in artifacts:
        path = output_root / f"{artifact.run_id}.json"
        path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True, help="JSON file containing sampled shadow replay requests.")
    parser.add_argument("--repo-sha", default=os.getenv("GIT_SHA", "").strip() or "unknown")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dashboard-output", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args()

    try:
        samples = load_shadow_samples(args.samples)
    except ValidationError as exc:
        raise SystemExit(str(exc)) from exc
    if not samples:
        raise SystemExit("No shadow replay samples supplied.")

    artifacts = run_shadow_replay(
        samples,
        repo_sha=args.repo_sha,
        top_k=args.top_k,
        max_workers=max(1, int(args.max_workers)),
    )
    paths = write_artifacts(artifacts, args.output_root)
    if args.dashboard_output is not None:
        publication = build_publication_payload(
            paths,
            note=(
                "Published shadow replay benchmark artefacts are derived from sampled live traffic replayed "
                "asynchronously across benchmark modes. They are detached from the user request path."
            ),
        )
        args.dashboard_output.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard_output.write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(paths)} shadow replay benchmark artefact(s) to {args.output_root}")


if __name__ == "__main__":
    main()

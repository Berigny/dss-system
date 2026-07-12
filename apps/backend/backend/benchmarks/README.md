# DSS v0.3 Benchmark Reproduction

This directory contains the reproducible benchmark suite for DSS v0.3.

## One-command reproduction

```bash
docker build -t dss-benchmarks:v0.3 -f backend/benchmarks/Dockerfile .
docker run --rm -it dss-benchmarks:v0.3 reproduce
```

The container will:

1. Generate a unique run ID (`ds-benchmark-{YYYYMMDD}-{HHMMSS}-{seed}`).
2. Log the run ID and every seed.
3. Run the ablation suite across five seeds with deterministic mode enabled.
4. Archive per-seed and aggregate artefacts under `runs/<run_id>/`.

## Local reproduction (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r backend/benchmarks/requirements.txt
pip install -e .
DSS_DETERMINISTIC=true python backend/benchmarks/reproduce.py
```

## Artefact layout

```
runs/
└── ds-benchmark-20260712-120000-193/
    ├── manifest.json
    ├── run.log
    └── semantic_only/
        ├── seeds/
        │   ├── 193/<timestamp>.json
        │   ├── 194/<timestamp>.json
        │   └── 195/<timestamp>.json
        └── aggregate/<timestamp>.json
    └── coordinate_guided/
        └── ...
```

## Dependency pinning

- Base image: `python:3.11.9-slim-bookworm`
- Python packages: `backend/benchmarks/requirements.txt` (generated with hashes via `uv pip compile --generate-hashes`)
- Source input: `backend/benchmarks/requirements.in`

## Determinism

Set `DSS_DETERMINISTIC=true` before importing benchmark code. The reproduction
script does this automatically.

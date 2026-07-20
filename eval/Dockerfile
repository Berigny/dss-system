# DSS v0.5 benchmark evaluation image.
#
# Builds a reproducible container that runs the full v0.5 benchmark suite:
#   DSS-292 known-unknown, DSS-293 adversarial poisoning,
#   DSS-294 BM25 ranking, DSS-295 latency/storage.
#
# Usage:
#   docker build -t dss-eval -f eval/Dockerfile .
#   docker run --rm -v $(PWD)/eval/reports/benchmarks:/app/eval/reports/benchmarks dss-eval
#
# Optional environment variables:
#   DRY_RUN=1            Run a fast, deterministic smoke run (default for CI).
#   DSS_REFRESH_TOKEN    Optional delegated Kimi mode token (not required for v0.5).
#   OPENROUTER_API_KEY   Optional fallback LLM key (not required for v0.5).

FROM python:3.11-slim

# Install build tooling required by native wheels (rocksdict, sentence-transformers, etc).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the entire monorepo so relative installs and imports work.
WORKDIR /app
COPY . /app

# Install shared types first, then backend requirements, then benchmark extras.
# requirements.txt contains a relative editable install for local dev/CI; strip
# it here because shared-types is already installed at a known absolute path.
RUN pip install --no-cache-dir -e /app/packages/shared-types
RUN sed -i '/^-e /d' /app/apps/backend/requirements.txt && \
    pip install --no-cache-dir -r /app/apps/backend/requirements.txt
RUN pip install --no-cache-dir rank-bm25 sentence-transformers hnswlib datasets

# The entrypoint is intentionally at repo-root level so it can see both
# apps/backend and eval/ paths.
WORKDIR /app/apps/backend

ENV PYTHONPATH=/app/apps/backend:/app/packages/shared-types/src:${PYTHONPATH}
ENV DRY_RUN=0

CMD ["python", "/app/eval/eval_entrypoint.py"]

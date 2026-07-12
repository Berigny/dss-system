# Pinned, reproducible benchmark runtime for DSS v0.3.
# Build: docker build -t dss-benchmarks:v0.3 -f backend/benchmarks/Dockerfile .
# Run:   docker run --rm -it dss-benchmarks:v0.3 reproduce

FROM python:3.11.9-slim-bookworm

WORKDIR /app

# Git is required so benchmark artefacts can record the repository SHA.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install pinned dependencies with hashes before copying the full source,
# so dependency layers are cached when only application code changes.
COPY backend/benchmarks/requirements.txt /app/backend/benchmarks/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/backend/benchmarks/requirements.txt

# Copy the rest of the repository and install it in editable mode.
COPY . /app
RUN pip install --no-cache-dir -e /app

ENV PYTHONPATH=/app
ENV DSS_DETERMINISTIC=true

WORKDIR /app
ENTRYPOINT ["/app/backend/benchmarks/entrypoint.sh"]

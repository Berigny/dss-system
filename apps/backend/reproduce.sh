#!/bin/sh
# One-command reproduction wrapper for the DSS v0.3 benchmark suite.
#
# Usage: ./reproduce.sh

set -e

IMAGE_TAG="dss-benchmarks:v0.3"

echo "Building ${IMAGE_TAG}..."
docker build -t "${IMAGE_TAG}" -f backend/benchmarks/Dockerfile .

echo "Running benchmark reproduction..."
docker run --rm -it \
  -v "$(pwd)/runs:/app/runs" \
  "${IMAGE_TAG}" \
  reproduce

#!/bin/sh
set -e

if [ "$1" = "reproduce" ]; then
    shift
    exec python /app/backend/benchmarks/reproduce.py "$@"
fi

exec "$@"

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DB_PATH=/data

# rocksdict needs RocksDB build headers and a C++ toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    librocksdb-dev \
    python3-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# The shared-types package lives outside the app directory, so copy it to the
# well-known path referenced by requirements.txt before installing deps.
COPY packages/shared-types /packages/shared-types
COPY apps/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/backend/ .

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

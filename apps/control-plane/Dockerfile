FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000

COPY packages/shared-types /packages/shared-types
COPY apps/control-plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/control-plane/ .

EXPOSE 3000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "3000"]

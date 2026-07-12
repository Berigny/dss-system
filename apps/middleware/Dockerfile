FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001

COPY packages/shared-types /packages/shared-types
COPY apps/middleware/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/middleware/ .

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]

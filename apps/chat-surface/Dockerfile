FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3001

COPY packages/shared-types /packages/shared-types
COPY apps/chat-surface/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/chat-surface/ .

EXPOSE 3001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "3001"]

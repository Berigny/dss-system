FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3002

COPY apps/coord-demo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/coord-demo/ .

EXPOSE 3002

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "3002"]

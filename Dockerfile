FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the SDKs.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY praetor/ ./praetor/
COPY service/ ./service/

# Cloud Run supplies $PORT and may change it; do not hardcode 8080.
ENV PORT=8080
CMD exec uvicorn service.main:app --host 0.0.0.0 --port ${PORT} --workers 1

# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# trafilatura + lxml need libxml2/libxslt at runtime; build-essential for any wheels that fall back to source
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2 \
        libxslt1.1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent SQLite cache lives on the mounted disk in production.
ENV PORT=8000 \
    EVIDENCE_DB_PATH=/data/evidence.db
EXPOSE 8000

# Render/Fly/Railway inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "mkdir -p /data && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

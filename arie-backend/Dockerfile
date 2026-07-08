# ══════════════════════════════════════════════════════════
# Onboarda / RegMind — Production Dockerfile
# Sprint 3: Containerized deployment with WeasyPrint support
# ══════════════════════════════════════════════════════════
#
# Build:   docker build -t arie-backend .
# Run:     docker run -p 10000:10000 --env-file .env arie-backend
# Compose: docker compose up -d
#
# BSA-019: base image pinned by digest — a mutable tag can silently change
# between builds. This digest is the python:3.11-slim multi-arch manifest list
# (verified 2026-07-08). To update: pick the new tag's digest via
#   docker buildx imagetools inspect python:3.11-slim
# and replace BOTH the comment date and the digest below in one reviewed change.
FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3 AS base

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG IMAGE_TAG=${GIT_SHA}

# WeasyPrint system dependencies (Cairo, Pango, GDK-PixBuf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ── Application Setup ──
WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN groupadd -r arie && useradd -r -g arie -d /app -s /sbin/nologin arie \
    && mkdir -p /app/uploads /app/data /app/logs \
    && chown -R arie:arie /app

USER arie

# ── Runtime Configuration ──
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000 \
    DB_PATH=/app/data/arie_finance.db \
    GIT_SHA=${GIT_SHA} \
    BUILD_TIME=${BUILD_TIME} \
    IMAGE_TAG=${IMAGE_TAG}

EXPOSE 10000

# Health check — hit /api/liveness every 30s (audit H10 / PR-15).
# /api/liveness is the public, hardened probe designed for ALB/ECS.
# /api/readiness must NOT be used here: it requires admin/sco authentication,
# so an unauthenticated container probe gets 401 and the container is
# permanently reported unhealthy regardless of actual state.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; r=requests.get('http://localhost:10000/api/liveness', timeout=3); exit(0 if r.status_code==200 else 1)"

# ── Entrypoint ──
CMD ["python", "server.py"]

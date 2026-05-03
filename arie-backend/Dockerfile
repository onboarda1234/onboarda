# ══════════════════════════════════════════════════════════
# Onboarda / RegMind — Production Dockerfile
# Sprint 3: Containerized deployment with WeasyPrint support
# ══════════════════════════════════════════════════════════
#
# Build:   docker build -t arie-backend .
# Run:     docker run -p 10000:10000 --env-file .env arie-backend
# Compose: docker compose up -d
#
FROM python:3.11-slim AS base

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

# Health check — hit /api/readiness every 30s (deep readiness probe)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; r=requests.get('http://localhost:10000/api/readiness', timeout=3); exit(0 if r.status_code==200 else 1)"

# ── Entrypoint ──
CMD ["python", "server.py"]

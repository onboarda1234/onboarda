# ══════════════════════════════════════════════════════════
# Onboarda / RegMind — Production Dockerfile
# Sprint 3: Containerized deployment with WeasyPrint support
# ══════════════════════════════════════════════════════════
#
# Build:   docker build -t arie-backend:<full-git-sha> .
# Run:     docker run -p 10000:10000 --env-file .env arie-backend:<full-git-sha>
# Compose: docker compose up -d
#
# BSA-019 / PR-RELEASE-CONTROLS-1: base image pinned by digest — a mutable tag
# can silently change between builds. Debian trixie carried release-blocking,
# unfixed glibc/perl findings in the prior runtime. The Alpine image removes
# those unused Debian packages while retaining the same Python 3.11 runtime.
# This digest is the python:3.11-alpine multi-arch manifest list (verified
# 2026-07-30). To update: pick the new tag's digest via
#   docker buildx imagetools inspect python:3.11-alpine
# and replace BOTH the comment date and the digest below in one reviewed change.
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4 AS base

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG IMAGE_TAG=${GIT_SHA}

# Apply the current Alpine security repository before installing the minimal
# WeasyPrint runtime set. Build-only compilers and Debian's Perl toolchain are
# intentionally absent from the final image.
RUN apk upgrade --no-cache \
    && apk add --no-cache \
    cairo \
    pango \
    gdk-pixbuf \
    libffi \
    shared-mime-info \
    font-liberation \
    font-dejavu

# ── Application Setup ──
WORKDIR /app

# Install Python dependencies first (layer caching).
# R2-BSA-019: install from the hash-pinned lockfile with --require-hashes, so
# every dependency (including the full transitive tree) is verified against a
# reviewed SHA-256 and a compromised/typosquatted upload cannot substitute
# different bytes. requirements.lock is generated from requirements.txt via
# pip-compile --generate-hashes (see requirements.txt header).
COPY requirements.txt requirements.lock .
# The runtime never installs packages. Removing the packaging toolchain also
# removes setuptools' vulnerable vendored jaraco.context/wheel copies.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip uninstall --yes setuptools pip

# Copy application code
COPY . .

# Create a locked non-root user with Alpine's native account tools.
RUN addgroup -S arie \
    && adduser -S -D -H -h /app -s /sbin/nologin -G arie arie \
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

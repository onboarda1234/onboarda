#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Onboarda / RegMind — Startup Script
# ═══════════════════════════════════════════════════════════
# Usage:  ./start.sh
# Config: Set environment variables below or in .env file
# ═══════════════════════════════════════════════════════════

set -e

# ── Configuration ───────────────────────────────────────
export PORT="${PORT:-10000}"
export DB_PATH="${DB_PATH:-$(dirname "$0")/arie.db}"
export DEBUG="${DEBUG:-0}"
export ENVIRONMENT="${ENVIRONMENT:-development}"

# ── SECRET_KEY Handling ──────────────────────────────────
# PRODUCTION/STAGING: SECRET_KEY must be explicitly set — fails if missing.
# DEVELOPMENT: Auto-generates a random key if not provided.
if [ "$ENVIRONMENT" = "production" ] || [ "$ENVIRONMENT" = "staging" ]; then
    if [ -z "$SECRET_KEY" ]; then
        echo ""
        echo "╔══════════════════════════════════════════════════╗"
        echo "║  FATAL: SECRET_KEY not set in $ENVIRONMENT mode  ║"
        echo "║  Set SECRET_KEY env var before starting.         ║"
        echo "║  Example: export SECRET_KEY=\$(openssl rand -hex 64)║"
        echo "╚══════════════════════════════════════════════════╝"
        echo ""
        exit 1
    fi
    if [ -z "$PII_ENCRYPTION_KEY" ]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════╗"
        echo "║  FATAL: PII_ENCRYPTION_KEY not set in $ENVIRONMENT mode ║"
        echo "║  Generate one with:                                      ║"
        echo "║  python3 -c 'from cryptography.fernet import Fernet;     ║"
        echo "║              print(Fernet.generate_key().decode())'      ║"
        echo "╚══════════════════════════════════════════════════════════╝"
        echo ""
        exit 1
    fi
    export SECRET_KEY
else
    if [ -z "$SECRET_KEY" ]; then
        export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(64))')"
        echo "  ⚠️  No SECRET_KEY set — auto-generated random key for development"
    else
        export SECRET_KEY
    fi
fi

# ── DATABASE_URL (PostgreSQL for production) ─────────────
# If DATABASE_URL is set, the server will use PostgreSQL.
# Otherwise, falls back to SQLite via DB_PATH.
# export DATABASE_URL="postgresql://user:pass@host:5432/arie"

# ── API Keys (set these for live screening) ─────────────
# export OPENSANCTIONS_API_KEY="your-key-here"
# export OPENCORPORATES_API_KEY="your-key-here"
# export IP_GEOLOCATION_API_KEY=""  # Optional: ipapi.co free tier works without key

# ── Sumsub KYC (set these for live identity verification) ─
# export SUMSUB_APP_TOKEN="your-app-token"
# export SUMSUB_SECRET_KEY="your-secret-key"
# export SUMSUB_LEVEL_NAME="basic-kyc-level"
# export SUMSUB_WEBHOOK_SECRET="your-webhook-secret"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  Onboarda / RegMind — Starting Platform         ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Check Python dependencies ───────────────────────────
# ── Environment Validation ────────────────────────────────
echo "→ Validating environment..."
if [ "$ENVIRONMENT" = "production" ] || [ "$ENVIRONMENT" = "staging" ]; then
    MISSING=""
    [ -z "$SECRET_KEY" ] && MISSING="$MISSING SECRET_KEY"
    [ -z "$PII_ENCRYPTION_KEY" ] && MISSING="$MISSING PII_ENCRYPTION_KEY"
    [ -z "$ALLOWED_ORIGIN" ] && echo "  ⚠️  ALLOWED_ORIGIN not set — CORS will default to same-origin only"
    if [ -n "$MISSING" ]; then
        echo "  ❌ Missing required $ENVIRONMENT variables:$MISSING"
        exit 1
    fi
    echo "  ✅ $ENVIRONMENT environment validated"
else
    echo "  ℹ️  Running in development mode"
fi

# ── Check Python dependencies ───────────────────────────
echo "→ Checking dependencies..."
python3 -c "import bcrypt, jwt, tornado, cryptography, typing_extensions, requests, pydantic" 2>/dev/null || {
    echo "❌ Missing dependencies. Installing..."
    pip3 install -r "$(dirname "$0")/requirements.txt" --break-system-packages 2>/dev/null || \
    pip3 install -r "$(dirname "$0")/requirements.txt"
}
echo "  ✅ All dependencies available"

# ── Start the server ────────────────────────────────────
echo ""
echo "→ Starting Onboarda / RegMind API on port $PORT..."
echo "  📋 Client Portal:   http://localhost:$PORT/portal"
echo "  🏢 Back Office:     http://localhost:$PORT/backoffice"
echo "  📡 API:             http://localhost:$PORT/api/health"
echo "  🔍 Screening:       http://localhost:$PORT/api/screening/status"
echo ""
echo "  Default admin login:"
echo "    Email:    asudally@onboarda.com"
echo "    Password: (generated on first run — check server output above)"
echo ""
echo "  API Keys:"
echo "    Sumsub AML:     ${SUMSUB_APP_TOKEN:+configured ✅}${SUMSUB_APP_TOKEN:-not set (simulated mode)}"
echo "    OpenCorporates: ${OPENCORPORATES_API_KEY:+configured ✅}${OPENCORPORATES_API_KEY:-not set (simulated mode)}"
echo "    IP Geolocation: live (free tier)
    Sumsub KYC:     ${SUMSUB_APP_TOKEN:+configured ✅}${SUMSUB_APP_TOKEN:-not set (simulated mode)}"
echo ""
echo "  Press Ctrl+C to stop the server."
echo ""

cd "$SCRIPT_DIR"
python3 server.py

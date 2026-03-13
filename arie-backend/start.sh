#!/bin/bash
# ═══════════════════════════════════════════════════════════
# ARIE Finance — Startup Script
# ═══════════════════════════════════════════════════════════
# Usage:  ./start.sh
# Config: Set environment variables below or in .env file
# ═══════════════════════════════════════════════════════════

set -e

# ── Configuration ───────────────────────────────────────
export PORT="${PORT:-8080}"
export SECRET_KEY="${SECRET_KEY:-arie-dev-secret-change-in-production}"
export DB_PATH="${DB_PATH:-$(dirname "$0")/arie.db}"
export DEBUG="${DEBUG:-0}"
export ENVIRONMENT="${ENVIRONMENT:-development}"

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
echo "║  ARIE Finance — Starting Platform               ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Check Python dependencies ───────────────────────────
echo "→ Checking dependencies..."
python3 -c "import bcrypt, jwt, tornado, cryptography, typing_extensions, requests" 2>/dev/null || {
    echo "❌ Missing dependencies. Installing..."
    pip3 install bcrypt PyJWT tornado cryptography typing_extensions requests --break-system-packages 2>/dev/null || \
    pip3 install bcrypt PyJWT tornado cryptography typing_extensions requests
}
echo "  ✅ All dependencies available"

# ── Start the server ────────────────────────────────────
echo ""
echo "→ Starting ARIE Finance API on port $PORT..."
echo "  📋 Client Portal:   http://localhost:$PORT/portal"
echo "  🏢 Back Office:     http://localhost:$PORT/backoffice"
echo "  📡 API:             http://localhost:$PORT/api/health"
echo "  🔍 Screening:       http://localhost:$PORT/api/screening/status"
echo ""
echo "  Default admin login:"
echo "    Email:    asudally@ariefinance.mu"
echo "    Password: Admin@123"
echo ""
echo "  API Keys:"
echo "    OpenSanctions:  ${OPENSANCTIONS_API_KEY:+configured ✅}${OPENSANCTIONS_API_KEY:-not set (simulated mode)}"
echo "    OpenCorporates: ${OPENCORPORATES_API_KEY:+configured ✅}${OPENCORPORATES_API_KEY:-not set (simulated mode)}"
echo "    IP Geolocation: live (free tier)
    Sumsub KYC:     ${SUMSUB_APP_TOKEN:+configured ✅}${SUMSUB_APP_TOKEN:-not set (simulated mode)}"
echo ""
echo "  Press Ctrl+C to stop the server."
echo ""

cd "$SCRIPT_DIR"
python3 server.py

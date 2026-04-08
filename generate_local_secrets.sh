#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# generate_local_secrets.sh
#
# Generates cryptographically strong values for locally-generated secrets
# required by the Onboarda / RegMind backend.
#
# Usage:
#   ./generate_local_secrets.sh                  # writes generated_secrets.env
#   ./generate_local_secrets.sh staging.env      # writes to custom file
#
# This script does NOT generate provider-issued secrets (Sumsub, Anthropic,
# AWS, database).  Those must be obtained from each provider.
# ---------------------------------------------------------------------------

OUT_FILE="${1:-generated_secrets.env}"

# ── dependency check ──────────────────────────────────────────────────────
need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: Missing required command: $1" >&2
    exit 1
  }
}

need openssl
need python3

# Verify the cryptography package is available (needed for Fernet key generation)
python3 -c "from cryptography.fernet import Fernet" 2>/dev/null || {
  echo "ERROR: Python 'cryptography' package is required but not installed." >&2
  echo "  Install with:  pip install cryptography" >&2
  exit 1
}

# ── generators ────────────────────────────────────────────────────────────

# 64-byte hex string (512-bit) for JWT / HMAC signing
gen_hex() {
  openssl rand -hex 64
}

# Fernet key (base64-encoded, 32-byte) for PII encryption
gen_fernet() {
  python3 -c "
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
"
}

# 24-char password with mixed case, digit, and special character
gen_password() {
  python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits + '!@#%^&*()-_=+'
while True:
    pw = ''.join(secrets.choice(alphabet) for _ in range(24))
    if (any(c.islower() for c in pw)
        and any(c.isupper() for c in pw)
        and any(c.isdigit() for c in pw)
        and any(c in '!@#%^&*()-_=+' for c in pw)):
        print(pw)
        break
"
}

# ── generate values ───────────────────────────────────────────────────────
JWT_SECRET="$(gen_hex)"
SECRET_KEY="$(gen_hex)"
PII_ENCRYPTION_KEY="$(gen_fernet)"

ADMIN_INITIAL_PASSWORD="$(gen_password)"
DEMO_PORTAL_PASSWORD="$(gen_password)"
DEMO_BACKOFFICE_PASSWORD="$(gen_password)"
DEMO_CLIENT_PASSWORD="$(gen_password)"

# ── write output ──────────────────────────────────────────────────────────
cat > "$OUT_FILE" <<EOF
# ============================================================
# Onboarda / RegMind — locally-generated secrets
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# SECURITY: Do NOT commit this file to version control.
# ============================================================

# ── Authentication & encryption (required for staging / production) ──
JWT_SECRET=${JWT_SECRET}
SECRET_KEY=${SECRET_KEY}
PII_ENCRYPTION_KEY=${PII_ENCRYPTION_KEY}

# ── Optional demo / admin passwords ──
ADMIN_INITIAL_PASSWORD=${ADMIN_INITIAL_PASSWORD}
DEMO_PORTAL_PASSWORD=${DEMO_PORTAL_PASSWORD}
DEMO_BACKOFFICE_PASSWORD=${DEMO_BACKOFFICE_PASSWORD}
DEMO_CLIENT_PASSWORD=${DEMO_CLIENT_PASSWORD}

# ============================================================
# The following secrets must be obtained from external providers.
# Do NOT generate placeholder values for these.
# ============================================================
# DATABASE_URL=              # PostgreSQL connection string
# ANTHROPIC_API_KEY=         # https://console.anthropic.com/
# SUMSUB_APP_TOKEN=          # https://cockpit.sumsub.com/
# SUMSUB_SECRET_KEY=         # https://cockpit.sumsub.com/
# SUMSUB_WEBHOOK_SECRET=     # https://cockpit.sumsub.com/
# AWS_ACCESS_KEY_ID=         # AWS IAM console (or use IAM roles on ECS)
# AWS_SECRET_ACCESS_KEY=     # AWS IAM console (or use IAM roles on ECS)
EOF

chmod 600 "$OUT_FILE"

echo "✅  Secrets written to ${OUT_FILE}  (mode 600)"
echo ""
echo "Still required from external providers:"
echo "  • DATABASE_URL           — PostgreSQL connection string"
echo "  • ANTHROPIC_API_KEY      — Anthropic console"
echo "  • SUMSUB_APP_TOKEN       — Sumsub cockpit"
echo "  • SUMSUB_SECRET_KEY      — Sumsub cockpit"
echo "  • SUMSUB_WEBHOOK_SECRET  — Sumsub cockpit"
echo "  • AWS_ACCESS_KEY_ID      — AWS IAM (or use IAM roles on ECS/Fargate)"
echo "  • AWS_SECRET_ACCESS_KEY  — AWS IAM (or use IAM roles on ECS/Fargate)"

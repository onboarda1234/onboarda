"""
Sumsub webhook validation & constants — PR 14 (webhook hardening).

This module centralises:
  * applicant_id format validation (defence against injection into substring scans
    and log poisoning)
  * applicant_id masking for logs (tenant-safe diagnostic output)
  * Sumsub signature-algorithm allowlist (X-Payload-Digest-Alg)
  * Explicit event-type allowlists for the webhook handler

IMPORTANT (audit_log behavior, tested in T14 and T14b):
Unknown event types and known-but-non-mutating event types (applicantPending,
applicantCreated, etc.) intentionally do NOT create audit_log rows. The
event-type gate in SumsubWebhookHandler.post short-circuits BEFORE the mutating
branch opens the database connection. The audit trail for those deliveries is
the WARNING / INFO log line with masked applicant_id. This is the deliberate
Rev 2 design — audit_log rows exist only for deliveries that could have caused
state changes on `applications` rows. Rationale: audit_log is a state-change
record, not a webhook-arrival record. A webhook-arrival log would create a
high-volume, low-signal table and would obscure the actual state transitions
under review during a compliance audit.
"""
import hashlib
import re

# ── Applicant ID format ──────────────────────────────────────────────────
# Sumsub applicant IDs are hex strings, typically 24 chars, observed range
# 16–64. We accept [0-9a-fA-F]{16,64} as a conservative superset. Anything
# outside this shape is rejected with HTTP 400 at the handler layer.
_APPLICANT_ID_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")


def validate_applicant_id(applicant_id: str) -> bool:
    """Return True iff applicant_id matches the Sumsub hex-id format."""
    if not isinstance(applicant_id, str):
        return False
    return bool(_APPLICANT_ID_RE.match(applicant_id))


def mask_applicant_id(applicant_id: str) -> str:
    """
    Return a log-safe masked form of applicant_id.

    Even though Sumsub applicant IDs are not themselves PII, they are strong
    tenant correlation keys. We log only the first 8 chars + an ellipsis
    so that logs can still be correlated across systems without exposing
    the full identifier to anyone with log-read access.
    """
    if not isinstance(applicant_id, str) or not applicant_id:
        return "<none>"
    if len(applicant_id) <= 8:
        return applicant_id  # nothing to mask
    return f"{applicant_id[:8]}\u2026"


# ── Signature algorithm allowlist ────────────────────────────────────────
# Sumsub sends X-Payload-Digest-Alg to indicate which HMAC algorithm was
# used to compute X-Payload-Digest. We hard-gate to a known set.
# If Sumsub ever introduces a new algorithm, adding it here is an explicit,
# reviewable change — never an implicit one.
ALLOWED_DIGEST_ALGS = {
    "HMAC_SHA256_HEX": hashlib.sha256,
    "HMAC_SHA512_HEX": hashlib.sha512,
}


# ── Event type allowlists ────────────────────────────────────────────────
# MUTATING events are allowed to open the database and update `applications`.
# ACKNOWLEDGED events are known, valid Sumsub event types that we choose not
# to act on. They return 200 with an INFO log line and no DB write.
# Anything NOT in either set is treated as "unknown" — 200 with a WARN log
# line, no DB write, no audit_log row.
#
# This list is intentionally explicit. Adding a new event type to either set
# is a reviewable, auditable change.
SUMSUB_MUTATING_EVENT_TYPES = frozenset({
    "applicantReviewed",
})

SUMSUB_ACKNOWLEDGED_EVENT_TYPES = frozenset({
    "applicantCreated",
    "applicantPending",
    "applicantOnHold",
    "applicantPrechecked",
    "applicantDeleted",
    "applicantReset",
    "applicantActionPending",
    "applicantActionReviewed",
    "applicantActionOnHold",
    "videoIdentStatusChanged",
})

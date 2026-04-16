"""Neutral shared module for application-party queries and PII field handling.

Extracted from server.py to break the circular import:
  rule_engine.py → server.py → Prometheus re-registration → ValueError.

This module has NO dependency on server.py and is safe to import from
rule_engine.py, supervisor/, or any other module.
"""

import base64
import logging
import os
import secrets
import sys

from config import ENVIRONMENT
from security_hardening import PIIEncryptor
from prescreening.normalize import safe_json_loads

logger = logging.getLogger("arie")

# ── PII Encryption Initialization ──────────────────────────────
# Mirrors the logic in server.py but lives in a neutral module that
# can be imported without triggering Prometheus metric registration.
_pii_encryptor = None
_pii_encryption_ok = False
try:
    _pii_encryptor = PIIEncryptor()
    _pii_encryption_ok = True
    logger.info("party_utils: PIIEncryptor initialized — field-level encryption active")
except (RuntimeError, ValueError) as e:
    if ENVIRONMENT in ("production", "prod", "staging"):
        logger.critical("FATAL: PIIEncryptor failed in %s: %s", ENVIRONMENT, e)
        sys.exit(1)
    elif ENVIRONMENT in ("development", "testing", "demo"):
        from cryptography.fernet import Fernet as _Fernet
        _auto_key = _Fernet.generate_key().decode()
        os.environ["PII_ENCRYPTION_KEY"] = _auto_key
        try:
            _pii_encryptor = PIIEncryptor(_auto_key)
            _pii_encryption_ok = True
            logger.warning(
                "party_utils: auto-generated transient PII key for %s (NOT for production/staging)",
                ENVIRONMENT,
            )
        except Exception as e2:
            logger.error("party_utils: PIIEncryptor initialization failed even with auto key: %s", e2)
    else:
        logger.critical("FATAL: PIIEncryptor failed in unrecognised environment '%s': %s", ENVIRONMENT, e)
        sys.exit(1)

# Boot-time encryption self-test: encrypt → decrypt → compare
if _pii_encryptor is not None:
    try:
        _canary = "pii-selftest-canary-" + secrets.token_hex(8)
        _encrypted_canary = _pii_encryptor.encrypt(_canary)
        _decrypted_canary = _pii_encryptor.decrypt(_encrypted_canary)
        if _decrypted_canary != _canary:
            logger.critical("FATAL: PII encryption self-test FAILED — decrypt mismatch")
            if ENVIRONMENT in ("production", "prod", "staging"):
                sys.exit(1)
            _pii_encryption_ok = False
        else:
            logger.info("party_utils: PII encryption self-test passed (encrypt/decrypt canary OK)")
    except Exception as _st_err:
        logger.critical("FATAL: PII encryption self-test exception: %s", _st_err)
        if ENVIRONMENT in ("production", "prod", "staging"):
            sys.exit(1)
        _pii_encryption_ok = False


# ── PII Field Constants ────────────────────────────────────────
PII_FIELDS_DIRECTORS = ["passport_number", "nationality", "id_number"]
PII_FIELDS_UBOS = ["passport_number", "nationality"]
PII_FIELDS_APPLICATIONS = ["pep_flags"]


# ── PII Utility Functions ──────────────────────────────────────

def extract_fernet_token(value) -> str:
    """Return ciphertext normalized to the format expected by PIIEncryptor.decrypt()."""
    if value in (None, ""):
        return ""
    raw = value.decode("utf-8", "ignore") if isinstance(value, (bytes, bytearray)) else str(value)
    for _ in range(4):
        if raw.startswith("gAAAAA"):
            return base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        padded = raw + ("=" * (-len(raw) % 4))
        decoded_next = None
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(padded.encode("utf-8"))
            except Exception:
                continue
            try:
                decoded_str = decoded.decode("utf-8")
            except Exception:
                continue
            decoded_next = decoded_str
            if decoded_str.startswith("gAAAAA"):
                return base64.b64encode(decoded_str.encode("utf-8")).decode("utf-8")
            break
        if not decoded_next or decoded_next == raw:
            break
        raw = decoded_next
    return ""


def encrypt_pii_fields(record: dict, field_names: list) -> dict:
    """Encrypt specified PII fields in a record before database write."""
    if not _pii_encryptor:
        return record
    encrypted = dict(record)
    for field in field_names:
        if field in encrypted and encrypted[field]:
            val = str(encrypted[field])
            if val and not extract_fernet_token(val):  # Don't double-encrypt Fernet tokens
                encrypted[field] = _pii_encryptor.encrypt(val)
    return encrypted


def decrypt_pii_fields(record: dict, field_names: list) -> dict:
    """Decrypt specified PII fields in a record after database read."""
    if not _pii_encryptor:
        return record
    decrypted = dict(record)
    for field in field_names:
        if field in decrypted and decrypted[field]:
            val = str(decrypted[field])
            token = extract_fernet_token(val)
            if token:
                try:
                    decrypted[field] = _pii_encryptor.decrypt(token)
                except Exception as e:
                    logger.warning("PII decryption failed for field '%s': %s", field, e)
                    decrypted[field] = None  # Clear encrypted blob — show as missing, not gibberish
    return decrypted


# ── Party Query / Hydration Functions ──────────────────────────

def parse_json_field(value, fallback):
    """Parse a JSON field, returning fallback if parsing fails or type mismatches."""
    parsed = safe_json_loads(value)
    return parsed if isinstance(parsed, type(fallback)) else fallback


def hydrate_party_record(record: dict, pii_fields=None, name_key="full_name") -> dict:
    """Hydrate a raw DB party record: decrypt PII, parse JSON fields, ensure full_name."""
    result = dict(record)
    if pii_fields:
        result = decrypt_pii_fields(result, pii_fields)
    result["pep_declaration"] = parse_json_field(result.get("pep_declaration"), {})
    result["full_name"] = result.get(name_key) or result.get("full_name") or ""
    return result


def get_application_parties(db, application_id):
    """Fetch directors, UBOs, and intermediaries for an application.

    Returns a 3-tuple: (directors, ubos, intermediaries).
    Each element is a list of dicts with PII fields decrypted and
    JSON fields parsed.
    """
    directors = [
        hydrate_party_record(d, PII_FIELDS_DIRECTORS)
        for d in db.execute("SELECT * FROM directors WHERE application_id = ?", (application_id,)).fetchall()
    ]
    ubos = [
        hydrate_party_record(u, PII_FIELDS_UBOS)
        for u in db.execute("SELECT * FROM ubos WHERE application_id = ?", (application_id,)).fetchall()
    ]
    intermediaries = []
    for row in db.execute("SELECT * FROM intermediaries WHERE application_id = ?", (application_id,)).fetchall():
        item = dict(row)
        item["full_name"] = item.get("entity_name", "")
        intermediaries.append(item)
    return directors, ubos, intermediaries


def get_application_parties_batch(db, application_ids):
    """Batch-fetch directors, UBOs, and intermediaries for multiple applications.

    Uses WHERE application_id IN (...) to avoid N+1 queries.
    Returns a dict keyed by application_id, each value being a 3-tuple:
    (directors, ubos, intermediaries).

    EX-13: Reduces party queries from 3N to 3 regardless of application count.
    """
    if not application_ids:
        return {}

    placeholders = ",".join("?" for _ in application_ids)
    id_list = list(application_ids)

    # Batch query directors
    directors_by_app = {}
    for d in db.execute(
        "SELECT * FROM directors WHERE application_id IN (%s)" % placeholders,
        id_list,
    ).fetchall():
        app_id = d["application_id"]
        directors_by_app.setdefault(app_id, []).append(
            hydrate_party_record(d, PII_FIELDS_DIRECTORS)
        )

    # Batch query UBOs
    ubos_by_app = {}
    for u in db.execute(
        "SELECT * FROM ubos WHERE application_id IN (%s)" % placeholders,
        id_list,
    ).fetchall():
        app_id = u["application_id"]
        ubos_by_app.setdefault(app_id, []).append(
            hydrate_party_record(u, PII_FIELDS_UBOS)
        )

    # Batch query intermediaries
    intermediaries_by_app = {}
    for row in db.execute(
        "SELECT * FROM intermediaries WHERE application_id IN (%s)" % placeholders,
        id_list,
    ).fetchall():
        app_id = row["application_id"]
        item = dict(row)
        item["full_name"] = item.get("entity_name", "")
        intermediaries_by_app.setdefault(app_id, []).append(item)

    # Build result dict for all requested IDs (include empty lists for apps with no parties)
    result = {}
    for app_id in application_ids:
        result[app_id] = (
            directors_by_app.get(app_id, []),
            ubos_by_app.get(app_id, []),
            intermediaries_by_app.get(app_id, []),
        )
    return result

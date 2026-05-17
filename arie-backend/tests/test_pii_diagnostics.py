import json
import sqlite3


def _make_diag_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, ref TEXT)")
    conn.execute(
        """
        CREATE TABLE directors (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            nationality TEXT,
            passport_number TEXT,
            id_number TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE ubos (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            nationality TEXT,
            passport_number TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT,
            ip_address TEXT
        )
        """
    )
    return conn


def test_diagnostic_scan_identifies_bad_rows_without_exposing_pii():
    from cryptography.fernet import Fernet
    from security_hardening import PIIEncryptor
    from party_utils import encrypt_pii_fields
    from scripts.diagnose_pii_tokens import build_summary, scan_pii_tokens

    db = _make_diag_db()
    db.execute("INSERT INTO applications (id, ref) VALUES (?, ?)", ("app1", "ARF-DIAG-1"))
    valid = encrypt_pii_fields({"nationality": "Mauritius"}, ["nationality"])["nationality"]
    invalid = PIIEncryptor(Fernet.generate_key().decode()).encrypt("NeverExpose")
    db.execute(
        "INSERT INTO directors (id, application_id, nationality) VALUES (?, ?, ?)",
        ("valid-row", "app1", valid),
    )
    db.execute(
        "INSERT INTO directors (id, application_id, nationality) VALUES (?, ?, ?)",
        ("plain-row", "app1", "LegacyPlaintext"),
    )
    db.execute(
        "INSERT INTO ubos (id, application_id, nationality) VALUES (?, ?, ?)",
        ("bad-row", "app1", invalid),
    )
    db.commit()

    findings = scan_pii_tokens(db)
    summary = build_summary(findings)
    serialized = json.dumps(summary)

    assert {"plaintext_legacy", "invalid_encrypted_token"} == {f["status"] for f in findings}
    assert "valid-row" not in serialized
    assert "bad-row" in serialized
    assert "plain-row" in serialized
    assert "NeverExpose" not in serialized
    assert invalid not in serialized
    assert "LegacyPlaintext" not in serialized
    assert "value_sha256_16" in serialized


def test_diagnostic_apply_nulls_invalid_tokens_and_writes_audit():
    from cryptography.fernet import Fernet
    from security_hardening import PIIEncryptor
    from scripts.diagnose_pii_tokens import apply_null_invalid_tokens, scan_pii_tokens

    db = _make_diag_db()
    db.execute("INSERT INTO applications (id, ref) VALUES (?, ?)", ("app1", "ARF-DIAG-2"))
    invalid = PIIEncryptor(Fernet.generate_key().decode()).encrypt("NeverExpose")
    db.execute(
        "INSERT INTO directors (id, application_id, nationality) VALUES (?, ?, ?)",
        ("bad-row", "app1", invalid),
    )
    db.commit()

    findings = scan_pii_tokens(db)
    repaired = apply_null_invalid_tokens(db, findings, "unit-test repair")

    row = db.execute("SELECT nationality FROM directors WHERE id=?", ("bad-row",)).fetchone()
    audit = db.execute("SELECT action, target, detail FROM audit_log").fetchone()

    assert len(repaired) == 1
    assert row["nationality"] is None
    assert audit["action"] == "PII Repair"
    assert audit["target"] == "ARF-DIAG-2"
    assert "NeverExpose" not in audit["detail"]
    assert invalid not in audit["detail"]
    assert "value_sha256_16" in audit["detail"]

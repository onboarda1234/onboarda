"""Evidence pack must carry the tamper-evident supervisor chain + attestation (H4).

Regression coverage for H4: the regulator-facing evidence pack previously built
its audit trail only from `audit_log` filtered by two `ref` target shapes with a
hard LIMIT 5000, and never included the hash-chained `supervisor_audit_log` or a
verification attestation — so a regulator could not independently verify the
decision chain from the pack.
"""


def _seed_app_with_chain(db, app_id="app-h4", ref="ARF-H4"):
    db.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (app_id, ref, "client-h4", "H4 Co", "Mauritius", "Technology", "SME", "approved", "LOW", 20),
    )
    # An audit row keyed by the application id — the ref-only filter used to drop this.
    db.execute(
        "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("u1", "Officer", "admin", "Decision", app_id, "{}", "127.0.0.1", "2026-01-01T00:00:00"),
    )
    from supervisor.audit import append_verdict_chain_entry
    append_verdict_chain_entry(
        db=db, application_id=app_id, verdict="CONSISTENT",
        contradiction_count=0, supervisor_confidence=1.0, memo_id="memo-h4",
    )
    db.commit()


def test_load_case_includes_supervisor_chain_and_attestation(temp_db):
    from db import get_db
    import evidence_pack_export as ep

    db = get_db()
    try:
        _seed_app_with_chain(db)
        app = dict(db.execute("SELECT * FROM applications WHERE id = 'app-h4'").fetchone())
        case = ep._load_case(db, app)

        # Supervisor chain rows for this application are present.
        assert len(case["supervisor_audit"]) == 1
        assert case["supervisor_audit"][0]["entry_hash"]

        # Full-chain verification attestation is present and intact.
        assert case["supervisor_chain_verification"]["verified"] is True

        # Widened audit query now captures the application-id-keyed row.
        assert any(r.get("target") == "app-h4" for r in case["audit"])
    finally:
        db.close()


def test_supervisor_chain_csv_carries_hashes_and_attestation(temp_db):
    from db import get_db
    import evidence_pack_export as ep

    db = get_db()
    try:
        _seed_app_with_chain(db, app_id="app-h4b", ref="ARF-H4B")
        app = dict(db.execute("SELECT * FROM applications WHERE id = 'app-h4b'").fetchone())
        case = ep._load_case(db, app)

        csv_text = ep.render_supervisor_audit_chain_csv(case, "external_redacted").decode("utf-8")
        # Hash columns must survive even external redaction (no PII, needed for verification).
        assert "entry_hash" in csv_text
        assert "previous_hash" in csv_text
        # Verification attestation header present.
        assert "chain_verified" in csv_text
        assert case["supervisor_audit"][0]["entry_hash"] in csv_text
        # Full payload COLUMN is withheld under external redaction (the string may
        # still appear in the algorithm note header, so check the column row).
        header_line = next(l for l in csv_text.splitlines() if l.startswith("timestamp,"))
        assert "canonical_hash_payload" not in header_line
    finally:
        db.close()


def test_supervisor_chain_csv_is_independently_recomputable(temp_db):
    """H4: at full_internal a third party can recompute sha256(payload)==entry_hash."""
    import csv
    import io
    import hashlib
    from db import get_db
    from supervisor.audit import append_verdict_chain_entry
    import evidence_pack_export as ep

    db = get_db()
    try:
        _seed_app_with_chain(db, app_id="app-h4c", ref="ARF-H4C")
        # A second entry so linkage (previous_hash -> entry_hash) is exercised too.
        append_verdict_chain_entry(
            db=db, application_id="app-h4c", verdict="CONSISTENT_WITH_WARNINGS",
            contradiction_count=1, supervisor_confidence=0.9, memo_id="memo-h4c-2",
        )
        db.commit()
        app = dict(db.execute("SELECT * FROM applications WHERE id = 'app-h4c'").fetchone())
        case = ep._load_case(db, app)

        text = ep.render_supervisor_audit_chain_csv(case, "full_internal").decode("utf-8")
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
        hi = next(i for i, r in enumerate(rows) if r and r[0] == "timestamp")
        header = rows[hi]
        assert "canonical_hash_payload" in header
        p_idx = header.index("canonical_hash_payload")
        h_idx = header.index("entry_hash")

        data_rows = rows[hi + 1:]
        assert len(data_rows) == 2
        for r in data_rows:
            recomputed = hashlib.sha256(r[p_idx].encode()).hexdigest()
            assert recomputed == r[h_idx], "independent recompute of entry_hash failed"
    finally:
        db.close()

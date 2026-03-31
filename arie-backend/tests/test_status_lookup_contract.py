"""
Focused tests for the public status-lookup contract.
"""

import pytest


def _insert_client_and_application(db):
    import bcrypt
    import uuid

    suffix = uuid.uuid4().hex[:8]
    password_hash = bcrypt.hashpw("LookupPass123!".encode(), bcrypt.gensalt()).decode()
    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (f"lookup_client_{suffix}", f"lookup_{suffix}@example.com", password_hash, "Lookup Corp Ltd")
    )
    db.execute("""
        INSERT INTO applications (
            id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        f"lookup_app_{suffix}",
        f"ARF-2026-{suffix.upper()}",
        f"lookup_client_{suffix}",
        "Lookup Corp Ltd",
        "Mauritius",
        "Technology",
        "SME",
        "compliance_review",
        "HIGH",
        72
    ))
    db.commit()
    return {
        "client_id": f"lookup_client_{suffix}",
        "email": f"lookup_{suffix}@example.com",
        "ref": f"ARF-2026-{suffix.upper()}",
    }


class TestStatusLookupContract:
    def test_public_lookup_requires_reference_and_email(self, db):
        from server import lookup_application_status_record

        created = _insert_client_and_application(db)

        with pytest.raises(ValueError, match="Reference number and email are required for public status lookup."):
            lookup_application_status_record(db, ref=created["ref"], email="")

        with pytest.raises(ValueError, match="Reference number and email are required for public status lookup."):
            lookup_application_status_record(db, ref="", email=created["email"])

    def test_public_lookup_only_returns_minimal_fields(self, db):
        from server import build_status_lookup_payload, lookup_application_status_record

        created = _insert_client_and_application(db)

        app = lookup_application_status_record(
            db,
            ref=created["ref"],
            email=created["email"],
            current_user=None
        )
        payload = build_status_lookup_payload(app)

        assert payload["ref"] == created["ref"]
        assert payload["status"] == "compliance_review"
        assert "updated_at" in payload
        assert "company_name" not in payload
        assert "risk_level" not in payload
        assert "risk_score" not in payload
        assert "client_email" not in payload

    def test_authenticated_client_can_lookup_own_status_without_email(self, db):
        from server import build_status_lookup_payload, lookup_application_status_record

        created = _insert_client_and_application(db)

        app = lookup_application_status_record(
            db,
            ref=created["ref"],
            email="",
            current_user={"sub": created["client_id"], "type": "client"}
        )
        payload = build_status_lookup_payload(app)

        assert payload["ref"] == created["ref"]
        assert payload["status"] == "compliance_review"
        assert "updated_at" in payload
        assert set(payload.keys()) == {"ref", "status", "updated_at"}

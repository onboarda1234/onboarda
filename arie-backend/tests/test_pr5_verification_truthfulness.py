import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_only_verified_state_has_success_metadata():
    from verification_state import VERIFICATION_STATES, verification_state_payload

    for state in VERIFICATION_STATES:
        payload = verification_state_payload(state)
        assert payload["verification_success"] is (state == "verified")
        if state != "verified":
            assert payload["verification_status_label"] != "Verified"


def test_document_schema_allows_explicit_in_progress_state():
    import db

    sqlite_schema = db._get_sqlite_schema()
    postgres_schema = db._get_postgres_schema()

    assert "'in_progress'" in sqlite_schema
    assert "'in_progress'" in postgres_schema
    assert "verification_status TEXT DEFAULT 'pending' CHECK" in postgres_schema


def test_portal_upload_copy_does_not_claim_stored_and_verified():
    portal_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-portal.html",
    )
    with open(portal_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert "Stored and verified" not in src
    assert "stored and verified" not in src
    assert "verification_success !== true" in src
    assert "data-verification-success" in src
    assert "function kycDocumentVerificationState" in src
    assert "ID + PoA stored — Not verified" in src
    assert "Submission Blocked — Verification Required" in src

"""PR7E async verification UI state-propagation guards."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "arie-portal.html"
BACKOFFICE = ROOT / "arie-backoffice.html"
PORTAL_BROWSER_SMOKE = ROOT / "arie-backend" / "scripts" / "qa" / "portal_async_verification_smoke.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_verification_status_endpoint_payload_is_terminal_render_ready(tmp_path, monkeypatch):
    from verification_jobs import verification_status_for_document

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        db.execute(
            """
            INSERT INTO applications (id, ref, company_name, country, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "app_pr7e_status",
                "ARF-2026-PR7E",
                "PR7E Status Ltd",
                "Mauritius",
                "draft",
                json.dumps({"registered_entity_name": "PR7E Status Ltd"}),
            ),
        )
        db.execute(
            """
            INSERT INTO documents (
                id, application_id, doc_type, doc_name, file_path, file_size,
                mime_type, slot_key, verification_status, verification_results,
                is_current, version, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "doc_pr7e_flagged",
                "app_pr7e_status",
                "cert_inc",
                "certificate.pdf",
                "/tmp/certificate.pdf",
                256,
                "application/pdf",
                "entity:cert_inc",
                "flagged",
                json.dumps(
                    {
                        "overall": "flagged",
                        "checks": [
                            {
                                "label": "Synthetic check",
                                "result": "warn",
                                "message": "manual review required",
                            }
                        ],
                    }
                ),
                1,
                3,
                "2026-05-22 10:00:00",
            ),
        )
        db.commit()

        payload = verification_status_for_document(db, "doc_pr7e_flagged")

        assert payload["doc_id"] == "doc_pr7e_flagged"
        assert payload["id"] == "doc_pr7e_flagged"
        assert payload["application_id"] == "app_pr7e_status"
        assert payload["doc_name"] == "certificate.pdf"
        assert payload["doc_type"] == "cert_inc"
        assert payload["slot_key"] == "entity:cert_inc"
        assert payload["is_current"] == 1
        assert payload["version"] == 3
        assert payload["verification_status"] == "flagged"
        assert payload["verification_state"] == "flagged"
        assert payload["verification_terminal"] is True
        assert payload["verification_success"] is False
        assert payload["verification_status_label"] == "Review required"
        assert payload["verification_results"]["checks"][0]["result"] == "warn"


def test_portal_polls_authoritative_status_after_async_upload_and_restore():
    src = _read(PORTAL)

    assert "var ASYNC_VERIFICATION_POLL_INTERVAL_MS = 2000;" in src
    assert "var ASYNC_VERIFICATION_POLL_MAX_MS = 120000;" in src
    assert "function startVerificationStatusPolling(options)" in src
    assert "apiCall('GET', '/documents/' + encodeURIComponent(docId) + '/verification-status')" in src
    assert "verificationRecordIsTerminal(latestRecord)" in src
    assert "stopAllVerificationStatusPolling()" in src

    assert "docId: latest.doc_id" in src
    assert "renderCompanyVerification(docId, updatedRecord)" in src
    assert "docId: verification.doc_id" in src
    assert "renderPersonVerification(personId, docType, updatedRecord)" in src

    sync_section = src.split("function syncPersistedApplicationDocuments(app)", 1)[1].split(
        "async function resumeApplication", 1
    )[0]
    assert "startVerificationStatusPolling({" in sync_section
    assert "renderCompanyVerification(slotId, updatedRecord)" in sync_section
    assert "renderPersonVerification(personId, doc.doc_type, updatedRecord)" in sync_section


def test_portal_terminal_rendering_remains_truthful_for_non_verified_states():
    src = _read(PORTAL)

    assert "card.setAttribute('data-verification-success', verificationSucceeded ? 'true' : 'false');" in src
    assert "card.setAttribute('data-verification-state', documentRecord.verification_state || documentRecord.verification_status || 'pending');" in src
    assert "state === 'verified' || state === 'flagged' || state === 'failed'" in src
    assert "Stored and verified" not in src
    assert "var icon = stateMeta.verification_success === true ? '✅ '" in src


def test_backoffice_detail_polls_pending_verification_documents_until_terminal():
    src = _read(BACKOFFICE)

    assert "var _VERIFICATION_DETAIL_POLL_INTERVAL_MS = 5000;" in src
    assert "function startBackofficeVerificationDetailPolling(app)" in src
    assert "appHasActiveVerificationDocuments(app)" in src
    assert "await refreshCurrentAppDetail({ preserveDetailTab: true });" in src
    assert "startBackofficeVerificationDetailPolling(app);" in src
    assert "if (name !== 'app-detail') stopBackofficeVerificationDetailPolling();" in src
    assert "stopBackofficeVerificationDetailPolling();" in src
    assert "verification_terminal: doc.verification_terminal === true" in src
    assert "verification_terminal: d.verification_terminal === true" in src


def test_portal_async_browser_smoke_uses_real_login_and_checks_pending_to_terminal():
    src = _read(PORTAL_BROWSER_SMOKE)

    assert "STAGING_PORTAL_EMAIL" in src
    assert "STAGING_PORTAL_PASSWORD" in src
    assert "STAGING_UPLOAD_FILE" in src
    assert "authenticatedLogin: \"ui-form\"" in src
    assert "tokenInjectionUsed: false" in src
    assert "authBypassUsed: false" in src
    assert "localStorage.setItem" not in src
    assert "sessionStorage.setItem" not in src
    assert "Authorization: Bearer" not in src
    assert "#l-email" in src
    assert "#l-password" in src
    assert "#login-form" in src
    assert "data-verification-state=\"pending\"" in src
    assert "data-verification-state=\"in_progress\"" in src
    assert "data-verification-state=\"verified\"" in src
    assert "data-verification-state=\"flagged\"" in src
    assert "data-verification-state=\"failed\"" in src
    assert "Non-verified terminal state rendered success-style language." in src

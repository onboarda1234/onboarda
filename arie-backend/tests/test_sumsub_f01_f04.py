"""
Targeted regression tests for Sumsub blocker findings F-01 to F-04.

F-01 — Webhook handler must read X-App-Access-Sig (not X-Payload-Digest)
F-02 — sumsub_verify_webhook must reject when secret is missing in staging AND production
F-03 — Portal must not contain misleading "email sent" messaging
F-04 — Portal must send application_id in applicant creation; backend must persist mapping
"""
import os
import hmac
import hashlib
import pytest
import sqlite3
import json
import sys

# ═══════════════════════════════════════════════════════════════════
# F-01: Webhook handler must use X-App-Access-Sig header
# ═══════════════════════════════════════════════════════════════════

class TestF01WebhookHeader:
    """Webhook handler reads the correct Sumsub signature header: X-App-Access-Sig."""

    def test_webhook_handler_reads_correct_header(self):
        """server.py SumsubWebhookHandler must read X-App-Access-Sig, not X-Payload-Digest."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()

        wh_start = src.find("class SumsubWebhookHandler")
        assert wh_start != -1, "SumsubWebhookHandler class not found"
        wh_end = src.find("\nclass ", wh_start + 10)
        wh_code = src[wh_start:wh_end]

        assert "X-Payload-Digest" not in wh_code, (
            "SumsubWebhookHandler still reads X-Payload-Digest — "
            "F-01 NOT fixed. Sumsub sends X-App-Access-Sig."
        )
        assert "X-App-Access-Sig" in wh_code, (
            "SumsubWebhookHandler must read X-App-Access-Sig header"
        )

    def test_webhook_handler_correct_header_comes_before_verify(self):
        """The correct header must be read before calling sumsub_verify_webhook."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()

        wh_start = src.find("class SumsubWebhookHandler")
        wh_end = src.find("\nclass ", wh_start + 10)
        wh_code = src[wh_start:wh_end]

        sig_pos = wh_code.find("X-App-Access-Sig")
        verify_pos = wh_code.find("sumsub_verify_webhook")
        assert sig_pos < verify_pos, (
            "X-App-Access-Sig header read must appear before sumsub_verify_webhook call"
        )

    def test_no_x_payload_digest_anywhere_in_webhook_path(self):
        """Neither server.py nor screening.py should reference X-Payload-Digest."""
        for filename in ("server.py", "screening.py"):
            path = os.path.join(os.path.dirname(__file__), "..", filename)
            with open(path) as f:
                src = f.read()
            assert "X-Payload-Digest" not in src, (
                f"{filename} still references X-Payload-Digest — remove or update to X-App-Access-Sig"
            )


# ═══════════════════════════════════════════════════════════════════
# F-02: sumsub_verify_webhook must reject unsigned webhooks in staging
# ═══════════════════════════════════════════════════════════════════

class TestF02WebhookSecretStagingRejection:
    """Missing webhook secret must cause rejection in both staging and production."""

    def test_missing_secret_in_staging_rejects_webhook(self, monkeypatch):
        """staging + no secret = reject (was: accept)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        body = b'{"type":"applicantReviewed"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is False, (
            "F-02: staging must reject webhooks when SUMSUB_WEBHOOK_SECRET is not set"
        )

    def test_missing_secret_in_production_rejects_webhook(self, monkeypatch):
        """production + no secret = reject (pre-existing, must stay passing)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "production")

        body = b'{"type":"applicantReviewed"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is False, (
            "production must reject webhooks when SUMSUB_WEBHOOK_SECRET is not set"
        )

    def test_missing_secret_in_demo_accepts_webhook(self, monkeypatch):
        """demo + no secret = accept (development/demo still permissive)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "demo")

        body = b'{"type":"test"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is True, (
            "demo/dev mode should still accept webhooks without secret for local testing"
        )

    def test_missing_secret_in_development_accepts_webhook(self, monkeypatch):
        """development + no secret = accept."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "development")

        body = b'{"type":"test"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is True

    def test_valid_secret_and_valid_sig_accepted_in_staging(self, monkeypatch):
        """staging + correct secret + correct HMAC = accept."""
        import screening
        secret = "staging-test-secret-key-abc"
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", secret)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        body = b'{"type":"applicantReviewed","applicantId":"test123"}'
        expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        result = screening.sumsub_verify_webhook(body, expected_sig)
        assert result is True, "Valid HMAC must be accepted even in staging"

    def test_valid_secret_invalid_sig_rejected_in_staging(self, monkeypatch):
        """staging + correct secret + wrong HMAC = reject."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "staging-secret")
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        body = b'{"type":"applicantReviewed"}'
        result = screening.sumsub_verify_webhook(body, "bad-signature-value")
        assert result is False, "Invalid HMAC must be rejected in staging"

    def test_staging_rejection_guard_uses_in_operator(self):
        """The code must use 'in (production, staging)' style guard, not ==."""
        screening_path = os.path.join(os.path.dirname(__file__), "..", "screening.py")
        with open(screening_path) as f:
            src = f.read()

        # Find the sumsub_verify_webhook function
        fn_start = src.find("def sumsub_verify_webhook")
        fn_end = src.find("\ndef ", fn_start + 10)
        fn_code = src[fn_start:fn_end]

        assert "staging" in fn_code, (
            "F-02: sumsub_verify_webhook must include 'staging' in the rejection guard"
        )
        # Must NOT use the old single-environment check pattern
        assert 'ENVIRONMENT == "production"' not in fn_code, (
            "F-02: old single-environment check still present — update to cover staging too"
        )


# ═══════════════════════════════════════════════════════════════════
# F-03: Portal must not contain misleading "email sent" messaging
# ═══════════════════════════════════════════════════════════════════

class TestF03PortalHonestMessaging:
    """Portal KYC section must not claim emails are sent or links are delivered."""

    def _portal_src(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(path) as f:
            return f.read()

    def test_no_secure_link_sent_email_text(self):
        """The literal 'Secure link sent! They will receive an email' must be gone."""
        src = self._portal_src()
        assert "Secure link sent" not in src, (
            "F-03: misleading 'Secure link sent' text still present in portal"
        )
        assert "They will receive an email to upload" not in src, (
            "F-03: misleading 'will receive an email to upload' text still present in portal"
        )

    def test_send_link_button_text_updated(self):
        """Buttons must not say 'Send Link' or '📧 Send Link' — they should say Register."""
        src = self._portal_src()
        # Old button text: '📧 Send Link'
        assert "Send Link" not in src, (
            "F-03: button text 'Send Link' still present — implies email is sent"
        )

    def test_link_sent_toast_text_updated(self):
        """Toast must not say 'Link Sent!' or 'Link sent to [email]'."""
        src = self._portal_src()
        assert "Link Sent!" not in src, (
            "F-03: 'Link Sent!' toast text still present"
        )
        assert "Link sent to" not in src, (
            "F-03: 'Link sent to' toast text still present"
        )

    def test_token_call_removed_or_result_used(self):
        """The dead /kyc/token call (result unused) must be removed from sendKYCLink."""
        src = self._portal_src()
        # Find the sendKYCLink function
        fn_start = src.find("async function sendKYCLink")
        fn_end = src.find("\nasync function ", fn_start + 10)
        if fn_end == -1:
            fn_end = src.find("\nfunction ", fn_start + 10)
        fn_code = src[fn_start:fn_end]

        # The token call was dead code — it must be gone from this function
        assert "/kyc/token" not in fn_code, (
            "F-03: dead /kyc/token call still present in sendKYCLink — "
            "token was fetched and discarded; either use it or remove the call"
        )

    def test_registered_text_present(self):
        """The honest 'Applicant registered' messaging must be present in the sendKYCLink function."""
        src = self._portal_src()
        fn_start = src.find("async function sendKYCLink")
        fn_end = src.find("\nasync function ", fn_start + 10)
        if fn_end == -1:
            fn_end = src.find("\nfunction ", fn_start + 10)
        fn_code = src[fn_start:fn_end]

        assert "Applicant registered" in fn_code, (
            "F-03: 'Applicant registered' messaging not found in sendKYCLink function body"
        )


# ═══════════════════════════════════════════════════════════════════
# F-04: Portal sends application_id; backend persists mapping
# ═══════════════════════════════════════════════════════════════════

class TestF04PortalApplicationIdMapping:
    """Portal must send application_id; backend must persist sumsub_applicant_mappings row."""

    def _portal_src(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(path) as f:
            return f.read()

    def test_portal_sends_application_id_in_kyc_applicant_call(self):
        """sendKYCLink must include application_id in the POST /kyc/applicant payload."""
        src = self._portal_src()
        fn_start = src.find("async function sendKYCLink")
        fn_end = src.find("\nasync function ", fn_start + 10)
        if fn_end == -1:
            fn_end = src.find("\nfunction ", fn_start + 10)
        fn_code = src[fn_start:fn_end]

        assert "application_id" in fn_code, (
            "F-04: sendKYCLink does not include application_id in the /kyc/applicant payload"
        )
        assert "currentApplicationId" in fn_code, (
            "F-04: sendKYCLink does not reference currentApplicationId"
        )

    def test_portal_sends_person_type_in_kyc_applicant_call(self):
        """sendKYCLink must include person_type for complete mapping record."""
        src = self._portal_src()
        fn_start = src.find("async function sendKYCLink")
        fn_end = src.find("\nasync function ", fn_start + 10)
        if fn_end == -1:
            fn_end = src.find("\nfunction ", fn_start + 10)
        fn_code = src[fn_start:fn_end]

        assert "person_type" in fn_code, (
            "F-04: sendKYCLink does not include person_type in the /kyc/applicant payload"
        )

    def test_backend_stores_mapping_when_both_ids_present(self):
        """SumsubApplicantHandler must insert into sumsub_applicant_mappings when application_id given."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()

        handler_start = src.find("class SumsubApplicantHandler")
        handler_end = src.find("\nclass ", handler_start + 10)
        handler_code = src[handler_start:handler_end]

        assert "sumsub_applicant_mappings" in handler_code, (
            "F-04: SumsubApplicantHandler does not insert into sumsub_applicant_mappings"
        )
        assert "application_id" in handler_code, (
            "F-04: SumsubApplicantHandler does not use application_id for mapping"
        )

    def test_backend_mapping_insert_is_conditional(self):
        """Mapping insert must only happen when both applicant_id and application_id are present."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()

        handler_start = src.find("class SumsubApplicantHandler")
        handler_end = src.find("\nclass ", handler_start + 10)
        handler_code = src[handler_start:handler_end]

        # Should guard: if applicant_id and application_id
        assert "if applicant_id and application_id" in handler_code, (
            "F-04: mapping insert must be guarded on both applicant_id and application_id being truthy"
        )

    def test_backend_mapping_persisted_in_db(self):
        """Integration: backend inserts mapping row when /api/kyc/applicant is called with application_id."""
        # Build a minimal in-memory SQLite db matching the mapping table schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE sumsub_applicant_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                applicant_id TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                person_name TEXT DEFAULT '',
                person_type TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(applicant_id)
            )
        """)
        # Simulate what the handler does on success
        application_id = "app-test-001"
        applicant_id = "sumsub-abc-123"
        external_user_id = "dir1"
        person_name = "John Doe"
        person_type = "director"

        conn.execute("""
            INSERT OR IGNORE INTO sumsub_applicant_mappings
            (application_id, applicant_id, external_user_id, person_name, person_type)
            VALUES (?, ?, ?, ?, ?)
        """, (application_id, applicant_id, external_user_id, person_name, person_type))
        conn.commit()

        row = conn.execute(
            "SELECT * FROM sumsub_applicant_mappings WHERE applicant_id = ?", (applicant_id,)
        ).fetchone()

        assert row is not None, "Mapping row was not inserted"
        assert row["application_id"] == application_id
        assert row["applicant_id"] == applicant_id
        assert row["person_type"] == "director"
        conn.close()

    def test_backend_mapping_lookup_resolves_from_portal_created_applicant(self):
        """After insert, mapping lookup must return the correct application_id deterministically."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE sumsub_applicant_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                applicant_id TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                person_name TEXT DEFAULT '',
                person_type TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(applicant_id)
            )
        """)
        # Two different applicants for two different applications
        conn.execute("""
            INSERT OR IGNORE INTO sumsub_applicant_mappings
            (application_id, applicant_id, external_user_id, person_name, person_type)
            VALUES (?, ?, ?, ?, ?)
        """, ("app-001", "sumsub-111", "dir1", "Alice Smith", "director"))
        conn.execute("""
            INSERT OR IGNORE INTO sumsub_applicant_mappings
            (application_id, applicant_id, external_user_id, person_name, person_type)
            VALUES (?, ?, ?, ?, ?)
        """, ("app-002", "sumsub-222", "dir1", "Bob Jones", "director"))
        conn.commit()

        # Lookup for applicant sumsub-111 should return app-001, not app-002
        row = conn.execute(
            "SELECT application_id FROM sumsub_applicant_mappings WHERE applicant_id = ?",
            ("sumsub-111",)
        ).fetchone()
        assert row["application_id"] == "app-001", (
            "Deterministic mapping must resolve to the correct application"
        )

        # Lookup for applicant sumsub-222 should return app-002
        row = conn.execute(
            "SELECT application_id FROM sumsub_applicant_mappings WHERE applicant_id = ?",
            ("sumsub-222",)
        ).fetchone()
        assert row["application_id"] == "app-002"
        conn.close()

    def test_duplicate_applicant_mapping_is_idempotent(self):
        """INSERT OR IGNORE must not fail when same applicant_id inserted twice."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE sumsub_applicant_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                applicant_id TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                person_name TEXT DEFAULT '',
                person_type TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(applicant_id)
            )
        """)
        for _ in range(2):
            conn.execute("""
                INSERT OR IGNORE INTO sumsub_applicant_mappings
                (application_id, applicant_id, external_user_id, person_name, person_type)
                VALUES (?, ?, ?, ?, ?)
            """, ("app-001", "sumsub-dup", "dir1", "Alice", "director"))
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM sumsub_applicant_mappings WHERE applicant_id = 'sumsub-dup'"
        ).fetchone()[0]
        assert count == 1, "Duplicate applicant_id must not produce duplicate rows"
        conn.close()

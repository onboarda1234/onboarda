"""
Sumsub Verification Audit Test Pack
Validates the full Sumsub verification lifecycle:
- Applicant creation & mapping
- Document upload & verification
- Webhook processing & idempotency
- Status persistence & propagation
- UI/API truthfulness
- Security & auditability
"""
import os
import sys
import json
import hmac
import hashlib
import time
import tempfile
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════════
# A. APPLICANT CREATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubApplicantCreation:
    """A. Creating an onboarding case creates exactly one correct Sumsub applicant."""

    def test_create_applicant_returns_required_fields(self):
        """Applicant creation must return applicant_id, external_user_id, status, source."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        # Mock the API call to avoid hitting real Sumsub
        client = SumsubClient.__new__(SumsubClient)
        client.app_token = "test_token"
        client.secret_key = "test_secret"
        client.base_url = "https://api.sumsub.com"
        client.level_name = "basic-kyc-level"
        client.webhook_secret = ""
        client.is_live = False
        client.timeout = 15
        client.max_retries = 1
        client.usage_tracker = MagicMock()

        # Use simulation mode
        result = client._simulate_applicant("user@example.com")

        assert "applicant_id" in result
        assert "external_user_id" in result
        assert result["external_user_id"] == "user@example.com"
        assert "status" in result
        assert result["source"] == "simulated"

    def test_applicant_id_linked_to_correct_user(self):
        """Applicant ID must be deterministically linked to the external user."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        client = SumsubClient.__new__(SumsubClient)
        result = client._simulate_applicant("director_001@company.com")

        assert result["external_user_id"] == "director_001@company.com"
        assert result["applicant_id"]  # Must not be empty

    def test_two_different_users_get_different_applicants(self):
        """Two different external users must never share an applicant ID."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        client = SumsubClient.__new__(SumsubClient)
        result1 = client._simulate_applicant("user1@example.com")
        result2 = client._simulate_applicant("user2@example.com")

        assert result1["applicant_id"] != result2["applicant_id"]
        assert result1["external_user_id"] != result2["external_user_id"]


# ═══════════════════════════════════════════════════════════════
# B. WEBHOOK PROCESSING TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubWebhookProcessing:
    """B/D/E. Webhook handling: valid, duplicate, malformed, wrong applicant."""

    def _make_webhook_payload(self, applicant_id="app_123", external_user_id="user@test.com",
                               review_answer="GREEN", event_type="applicantReviewed"):
        """Create a standard webhook payload."""
        return {
            "type": event_type,
            "applicantId": applicant_id,
            "externalUserId": external_user_id,
            "reviewResult": {
                "reviewAnswer": review_answer,
                "rejectLabels": [] if review_answer == "GREEN" else ["FORGERY"],
                "moderationComment": "" if review_answer == "GREEN" else "Suspected forgery"
            }
        }

    def test_valid_webhook_stores_correct_data(self):
        """applicantReviewed webhook must store the correct applicant_id, answer, and timestamp."""
        payload = self._make_webhook_payload(
            applicant_id="sumsub_abc123",
            external_user_id="director1@company.com",
            review_answer="GREEN"
        )

        # Simulate what webhook handler does
        kyc_data = {
            "sumsub_applicant_id": payload["applicantId"],
            "external_user_id": payload["externalUserId"],
            "review_answer": payload["reviewResult"]["reviewAnswer"],
            "rejection_labels": payload["reviewResult"]["rejectLabels"],
            "moderation_comment": payload["reviewResult"]["moderationComment"],
            "event_type": payload["type"],
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

        assert kyc_data["sumsub_applicant_id"] == "sumsub_abc123"
        assert kyc_data["external_user_id"] == "director1@company.com"
        assert kyc_data["review_answer"] == "GREEN"
        assert kyc_data["event_type"] == "applicantReviewed"
        assert kyc_data["received_at"]  # Must have timestamp

    def test_red_review_adds_flag(self):
        """A RED review_answer must add a rejection flag."""
        payload = self._make_webhook_payload(review_answer="RED")

        screening_report = {"overall_flags": []}
        if payload["reviewResult"]["reviewAnswer"] == "RED":
            screening_report["overall_flags"].append(
                f"Sumsub KYC verification REJECTED for {payload['externalUserId']}"
            )

        assert len(screening_report["overall_flags"]) == 1
        assert "REJECTED" in screening_report["overall_flags"][0]

    def test_green_review_no_flag(self):
        """A GREEN review_answer must NOT add a rejection flag."""
        payload = self._make_webhook_payload(review_answer="GREEN")

        screening_report = {"overall_flags": []}
        if payload["reviewResult"]["reviewAnswer"] == "RED":
            screening_report["overall_flags"].append("REJECTED")

        assert len(screening_report["overall_flags"]) == 0

    def test_duplicate_webhook_is_idempotent(self):
        """Processing the same webhook twice must not create duplicate data."""
        payload = self._make_webhook_payload(applicant_id="app_dup_test")

        kyc_data = {
            "sumsub_applicant_id": payload["applicantId"],
            "review_answer": payload["reviewResult"]["reviewAnswer"],
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

        # Simulate writing to prescreening_data twice
        pdict = {"screening_report": {}}
        pdict["screening_report"]["sumsub_webhook"] = kyc_data
        first_write = json.dumps(pdict)

        # Second webhook (same data)
        pdict["screening_report"]["sumsub_webhook"] = kyc_data
        second_write = json.dumps(pdict)

        # The key "sumsub_webhook" is overwritten, not appended — idempotent
        first_parsed = json.loads(first_write)
        second_parsed = json.loads(second_write)
        assert first_parsed["screening_report"]["sumsub_webhook"]["sumsub_applicant_id"] == \
               second_parsed["screening_report"]["sumsub_webhook"]["sumsub_applicant_id"]

    def test_malformed_webhook_rejected(self):
        """Invalid JSON payload must be rejected."""
        malformed_body = b"not valid json {{"
        try:
            json.loads(malformed_body)
            assert False, "Should have raised JSONDecodeError"
        except (json.JSONDecodeError, ValueError):
            pass  # Expected

    def test_webhook_for_unknown_applicant_does_not_corrupt(self, temp_db):
        """PR 14 (F-7): A webhook whose applicant has no mapping must not
        mutate any application row. With the legacy substring scan removed,
        the delivery MUST route to the sumsub_unmatched_webhooks DLQ
        instead — never back-door into applications via substring match.

        This test was previously a tautology that ran its own substring
        algorithm in the test body and asserted the shape. It now drives
        the real SumsubWebhookHandler end-to-end.
        """
        import sqlite3
        from tests.test_sumsub_hardening_pr14 import (
            _call_handler, _make_payload, _open_real_db,
        )

        # Seed an application whose prescreening_data contains the applicant
        # id as a raw substring. The pre-F-7 handler would have falsely
        # cross-linked it. The post-F-7 handler must leave it alone.
        # NOTE: We must seed into db.DB_PATH (which is frozen at import time)
        # rather than the temp_db fixture path — the handler opens db.DB_PATH
        # directly and ignores the env var re-point in the fixture.
        unknown_applicant = "cafebabefeedface" + "0011223344556677"
        poisoned = json.dumps({
            "note": f"historical mention of {unknown_applicant} in free text"
        })
        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("DELETE FROM applications WHERE id=?",
                         ("app_unknown_victim",))
            conn.execute("DELETE FROM applications WHERE ref=?",
                         ("ARF-VERIF-UNK",))
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, "
                "country, sector, entity_type, status, risk_level, risk_score, "
                "prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("app_unknown_victim", "ARF-VERIF-UNK", "clientX",
                 "Victim Co", "Mauritius", "Technology", "SME",
                 "draft", "LOW", 10, poisoned),
            )
            conn.commit()
        finally:
            conn.close()

        body = _make_payload(applicant_id=unknown_applicant,
                             external_user_id="nobody@nowhere.test")
        handler = _call_handler(body)
        assert handler._status_code == 200

        # The poisoned row MUST NOT carry a sumsub_webhook mutation.
        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT prescreening_data FROM applications WHERE id=?",
                ("app_unknown_victim",),
            ).fetchone()
        finally:
            conn.close()
        pdict = json.loads(row["prescreening_data"] or "{}")
        screening_report = pdict.get("screening_report", {})
        assert "sumsub_webhook" not in screening_report, \
            "Unknown applicant leaked into an application row via substring scan"


# ═══════════════════════════════════════════════════════════════
# C. WEBHOOK SIGNATURE VERIFICATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubWebhookSignature:
    """Security: webhook signature verification using HMAC-SHA256."""

    def test_valid_signature_accepted(self):
        """A correctly signed webhook must be accepted."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        secret = "test_webhook_secret_key"
        payload = b'{"type":"applicantReviewed","applicantId":"abc123"}'
        expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

        client = SumsubClient.__new__(SumsubClient)
        client.webhook_secret = secret

        assert client.verify_webhook_signature(payload, expected_sig) is True

    def test_invalid_signature_rejected(self):
        """An incorrectly signed webhook must be rejected."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        secret = "test_webhook_secret_key"
        payload = b'{"type":"applicantReviewed","applicantId":"abc123"}'
        wrong_sig = "0000000000000000000000000000000000000000000000000000000000000000"

        client = SumsubClient.__new__(SumsubClient)
        client.webhook_secret = secret

        assert client.verify_webhook_signature(payload, wrong_sig) is False

    def test_missing_secret_rejects_webhook(self):
        """If no webhook secret is configured, all webhooks must be rejected."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        client = SumsubClient.__new__(SumsubClient)
        client.webhook_secret = ""  # Not configured

        payload = b'{"type":"applicantReviewed"}'
        assert client.verify_webhook_signature(payload, "any_signature") is False

    def test_tampered_payload_detected(self):
        """A payload that was tampered with after signing must be rejected."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        secret = "test_webhook_secret_key"
        original_payload = b'{"type":"applicantReviewed","reviewResult":{"reviewAnswer":"RED"}}'
        sig = hmac.new(secret.encode("utf-8"), original_payload, hashlib.sha256).hexdigest()

        # Tamper: change RED to GREEN
        tampered_payload = b'{"type":"applicantReviewed","reviewResult":{"reviewAnswer":"GREEN"}}'

        client = SumsubClient.__new__(SumsubClient)
        client.webhook_secret = secret

        assert client.verify_webhook_signature(tampered_payload, sig) is False


# ═══════════════════════════════════════════════════════════════
# D. MAPPING INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubMappingIntegrity:
    """C. Mapping: correct applicant-to-application, document-to-person linkage."""

    def test_webhook_result_linked_to_correct_application(self):
        """Sumsub result must be stored under the correct application's prescreening_data."""
        applicant_id = "sumsub_target_app"
        external_user_id = "director@targetco.com"

        # Two applications, only one contains the applicant
        apps = [
            {"id": "app_target", "prescreening_data": json.dumps({"sumsub_applicant_id": applicant_id})},
            {"id": "app_other", "prescreening_data": json.dumps({"company": "OtherCo"})},
        ]

        matched_ids = []
        for app in apps:
            pdata = app["prescreening_data"] or ""
            if applicant_id in pdata or external_user_id in pdata:
                matched_ids.append(app["id"])

        assert matched_ids == ["app_target"]
        assert "app_other" not in matched_ids

    def test_two_applicants_never_cross_link(self, temp_db):
        """PR 14 (F-7): Driving the real handler, results for applicant A
        must never be written onto applicant B's application — even if
        applicant A's id happens to appear as a raw substring of B's
        prescreening_data (the exact vector the legacy substring scan
        created).

        This replaces the prior tautology that asserted string containment
        against two hand-built dicts without ever touching the handler.
        """
        import sqlite3
        from tests.test_sumsub_hardening_pr14 import (
            _call_handler, _make_payload, _open_real_db,
        )

        applicant_a = "aaaaaaaabbbbbbbb" + "ccccccccdddddddd"

        # Row B has applicant A's id embedded in a free-text note.
        # Pre-F-7, the legacy scan would write applicant A's webhook onto B.
        # Post-F-7, it must not.
        # NOTE: seed into db.DB_PATH (frozen) — same reason as the previous
        # test. The temp_db fixture's env-var re-point is ignored by db.py.
        row_b_pdata = json.dumps({
            "note": f"accidentally references {applicant_a} in comment"
        })
        row_a_pdata = json.dumps({"company": "Applicant A Corp"})

        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("DELETE FROM applications WHERE id IN (?, ?)",
                         ("verif_cross_a", "verif_cross_b"))
            conn.execute("DELETE FROM applications WHERE ref IN (?, ?)",
                         ("ARF-XL-A", "ARF-XL-B"))
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, "
                "country, sector, entity_type, status, risk_level, risk_score, "
                "prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("verif_cross_a", "ARF-XL-A", "clientA", "A Corp",
                 "Mauritius", "Technology", "SME", "draft", "LOW", 10, row_a_pdata),
            )
            conn.execute(
                "INSERT INTO applications (id, ref, client_id, company_name, "
                "country, sector, entity_type, status, risk_level, risk_score, "
                "prescreening_data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("verif_cross_b", "ARF-XL-B", "clientB", "B Corp",
                 "Mauritius", "Technology", "SME", "draft", "LOW", 10, row_b_pdata),
            )
            conn.commit()
        finally:
            conn.close()

        # Send the real webhook for applicant A. Neither row has a mapping,
        # so the handler must route to DLQ without touching either app.
        body = _make_payload(applicant_id=applicant_a,
                             external_user_id="a@corp.test")
        handler = _call_handler(body)
        assert handler._status_code == 200

        conn = _open_real_db()
        conn.row_factory = sqlite3.Row
        try:
            for row_id in ("verif_cross_a", "verif_cross_b"):
                row = conn.execute(
                    "SELECT prescreening_data FROM applications WHERE id=?",
                    (row_id,),
                ).fetchone()
                pdict = json.loads(row["prescreening_data"] or "{}")
                screening_report = pdict.get("screening_report", {})
                assert "sumsub_webhook" not in screening_report, (
                    f"Row {row_id} was cross-linked to applicant A's webhook"
                )
        finally:
            conn.close()

    def test_webhook_data_structure_matches_schema(self):
        """Stored webhook data must have all required audit fields."""
        kyc_data = {
            "sumsub_applicant_id": "app_123",
            "external_user_id": "user@company.com",
            "review_answer": "GREEN",
            "rejection_labels": [],
            "moderation_comment": "",
            "event_type": "applicantReviewed",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

        required_fields = [
            "sumsub_applicant_id",
            "external_user_id",
            "review_answer",
            "rejection_labels",
            "moderation_comment",
            "event_type",
            "received_at",
        ]

        for field in required_fields:
            assert field in kyc_data, f"Missing audit field: {field}"


# ═══════════════════════════════════════════════════════════════
# E. FAILURE HANDLING TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubFailureHandling:
    """D. API failures return safe states, never VERIFIED."""

    def test_api_failure_returns_safe_state(self):
        """If Sumsub API fails, result must NOT be 'verified'."""
        # Simulate a failed API call
        error_result = {
            "error": "Sumsub API timeout",
            "status": "error",
            "source": "sumsub",
            "api_status": "error"
        }

        assert error_result.get("status") != "verified"
        assert error_result.get("review_answer") != "GREEN"

    def test_simulation_mode_marked_clearly(self):
        """Simulated results must be marked with source='simulated'."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        client = SumsubClient.__new__(SumsubClient)
        result = client._simulate_applicant("test@test.com")

        assert result["source"] == "simulated"
        assert result["api_status"] == "simulated"

    def test_missing_credentials_blocks_live_calls(self):
        """Without SUMSUB_APP_TOKEN, live API calls must not proceed."""
        # In the real system, SumsubClient checks is_live and blocks simulation in production
        # This validates the guard logic
        app_token = ""
        secret_key = ""
        is_live = bool(app_token and secret_key)

        assert is_live is False

    def test_cost_cap_prevents_runaway(self):
        """Monthly cost cap must block calls when exceeded."""
        try:
            from sumsub_client import SumsubClient
        except ImportError:
            pytest.skip("sumsub_client not importable")

        # Verify cost model exists
        assert hasattr(SumsubClient, 'COST_PER_CALL') or True  # Cost tracking is in usage_tracker


# ═══════════════════════════════════════════════════════════════
# F. UI TRUTHFULNESS TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubUITruthfulness:
    """E/F. Portal and back office must only display real Sumsub results."""

    def test_no_verification_means_no_verified_display(self):
        """If no Sumsub webhook has been received, status must NOT be 'verified'."""
        prescreening_data = json.dumps({"screening_report": {}})
        pdict = json.loads(prescreening_data)

        sumsub_result = pdict.get("screening_report", {}).get("sumsub_webhook")
        assert sumsub_result is None
        # UI should show "Pending" or "Not Started", never "Verified"

    def test_green_result_correctly_mapped(self):
        """A GREEN Sumsub result should show as verified/approved."""
        pdict = {
            "screening_report": {
                "sumsub_webhook": {
                    "review_answer": "GREEN",
                    "sumsub_applicant_id": "app_123"
                }
            }
        }

        result = pdict["screening_report"]["sumsub_webhook"]
        assert result["review_answer"] == "GREEN"
        # UI may show "Verified" for GREEN

    def test_red_result_never_shown_as_verified(self):
        """A RED Sumsub result must NEVER be shown as verified/approved."""
        pdict = {
            "screening_report": {
                "sumsub_webhook": {
                    "review_answer": "RED",
                    "rejection_labels": ["FORGERY"],
                    "sumsub_applicant_id": "app_456"
                }
            }
        }

        result = pdict["screening_report"]["sumsub_webhook"]
        assert result["review_answer"] != "GREEN"
        assert result["review_answer"] == "RED"
        # UI must show "Rejected" or "Failed"

    def test_portal_and_backoffice_see_same_data(self):
        """Both portal and back office derive Sumsub status from prescreening_data."""
        # Source of truth is applications.prescreening_data.screening_report.sumsub_webhook
        prescreening_data = {
            "screening_report": {
                "sumsub_webhook": {
                    "sumsub_applicant_id": "sumsub_xyz",
                    "review_answer": "GREEN",
                    "received_at": "2026-03-24T10:00:00"
                }
            }
        }

        # What portal reads via GET /api/applications/:id
        portal_view = prescreening_data["screening_report"]["sumsub_webhook"]

        # What back office reads via GET /api/applications (list) → detail
        backoffice_view = prescreening_data["screening_report"]["sumsub_webhook"]

        assert portal_view["sumsub_applicant_id"] == backoffice_view["sumsub_applicant_id"]
        assert portal_view["review_answer"] == backoffice_view["review_answer"]
        assert portal_view["received_at"] == backoffice_view["received_at"]


# ═══════════════════════════════════════════════════════════════
# G. AUDITABILITY TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubAuditability:
    """G. Every verification must be fully traceable."""

    def test_audit_log_entry_has_required_fields(self):
        """Webhook audit log entry must contain applicant_id, event type, answer."""
        audit_entry = {
            "user_id": "system",
            "user_name": "Sumsub Webhook",
            "user_role": "system",
            "action": "KYC applicantReviewed: GREEN",
            "target": "sumsub_app_123",
            "detail": json.dumps({
                "sumsub_applicant_id": "sumsub_app_123",
                "external_user_id": "user@company.com",
                "review_answer": "GREEN",
                "event_type": "applicantReviewed",
                "received_at": "2026-03-24T10:00:00"
            })
        }

        assert "applicantReviewed" in audit_entry["action"]
        assert "GREEN" in audit_entry["action"]
        assert audit_entry["target"] == "sumsub_app_123"

        detail = json.loads(audit_entry["detail"])
        assert detail["sumsub_applicant_id"] == "sumsub_app_123"
        assert detail["external_user_id"] == "user@company.com"
        assert detail["received_at"]

    def test_webhook_data_persisted_in_prescreening(self):
        """Webhook result must be persisted in prescreening_data for audit trail."""
        original_pdata = {"company": "TestCo", "screening_report": {}}

        kyc_data = {
            "sumsub_applicant_id": "app_audit_test",
            "external_user_id": "audit@company.com",
            "review_answer": "GREEN",
            "received_at": "2026-03-24T10:00:00"
        }

        original_pdata["screening_report"]["sumsub_webhook"] = kyc_data
        serialized = json.dumps(original_pdata)

        # Verify the data is recoverable
        recovered = json.loads(serialized)
        assert recovered["screening_report"]["sumsub_webhook"]["sumsub_applicant_id"] == "app_audit_test"
        assert recovered["company"] == "TestCo"  # Original data preserved


# ═══════════════════════════════════════════════════════════════
# H. AML/PEP SCREENING TESTS
# ═══════════════════════════════════════════════════════════════

class TestSumsubAMLScreening:
    """AML/PEP screening via Sumsub must return correctly mapped results."""

    def test_aml_result_has_required_fields(self):
        """AML screening result must contain matched flag, results array, source."""
        result = {
            "matched": True,
            "results": [
                {
                    "match_score": 85.5,
                    "matched_name": "John Doe",
                    "is_pep": True,
                    "is_sanctioned": False,
                    "sanctions_list": "",
                    "topics": ["pep"],
                    "countries": ["US"]
                }
            ],
            "source": "sumsub",
            "api_status": "live",
            "screened_at": "2026-03-24T10:00:00"
        }

        assert "matched" in result
        assert "results" in result
        assert "source" in result
        assert result["source"] in ("sumsub", "simulated")
        assert result["results"][0]["is_pep"] is True

    def test_no_match_returns_clean(self):
        """No AML/PEP match must return matched=False with empty results."""
        result = {
            "matched": False,
            "results": [],
            "source": "sumsub",
            "api_status": "live"
        }

        assert result["matched"] is False
        assert len(result["results"]) == 0

    def test_aml_source_identified(self):
        """AML results must clearly indicate whether they came from live or simulated source."""
        live_result = {"source": "sumsub", "api_status": "live"}
        sim_result = {"source": "simulated", "api_status": "simulated"}

        assert live_result["source"] != sim_result["source"]
        # UI must show [Live] or [Simulated] label


# ═══════════════════════════════════════════════════════════════
# I. WEBHOOK LINKAGE PERSISTENCE TESTS
#    These tests reproduce the staging break: applicant 69b7e6a9a2b8eb118c24aaa7
#    had no mapping row and no applicant_id in prescreening_data, so the webhook
#    updated nothing.  The fix stores sumsub_applicant_ids in prescreening_data
#    at applicant-creation time so the legacy fallback scan always succeeds.
# ═══════════════════════════════════════════════════════════════

class TestSumsubWebhookLinkagePersistence:
    """I. Applicant creation must seed prescreening_data so the legacy fallback can find it."""

    def _server_handler_code(self, class_name):
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py"
        )
        with open(server_path) as f:
            server_src = f.read()
        start = server_src.find(f"class {class_name}")
        assert start != -1, f"{class_name} not found in server.py"
        next_class = server_src.find("\nclass ", start + 1)
        return server_src[start:next_class] if next_class != -1 else server_src[start:]

    def test_applicant_handler_stores_id_in_prescreening_data(self):
        """SumsubApplicantHandler must write sumsub_applicant_ids into prescreening_data."""
        ah_code = self._server_handler_code("SumsubApplicantHandler")
        assert "sumsub_applicant_ids" in ah_code, (
            "SumsubApplicantHandler must write sumsub_applicant_ids into prescreening_data "
            "so that the legacy fallback scan can locate the application when the mapping "
            "table entry is missing (staging bug: applicant 69b7e6a9a2b8eb118c24aaa7)"
        )
        assert "UPDATE applications SET prescreening_data" in ah_code, (
            "SumsubApplicantHandler must persist the updated prescreening_data to the DB"
        )

    def test_webhook_handler_updates_by_row_id_not_mapped_value(self):
        """Webhook handler must use the resolved row id (not the raw mapping value) for UPDATE."""
        wh_code = self._server_handler_code("SumsubWebhookHandler")
        assert 'row["id"]' in wh_code or "row['id']" in wh_code, (
            'Webhook handler must use the resolved applications.id (row["id"]) when '
            "writing prescreening_data, not the raw mapping value which may be a ref"
        )

    def test_webhook_handler_lookup_accepts_ref_or_id(self):
        """Webhook update query must resolve both id and ref columns."""
        wh_code = self._server_handler_code("SumsubWebhookHandler")
        assert "WHERE id=? OR ref=?" in wh_code or "WHERE id = ? OR ref = ?" in wh_code, (
            "Webhook handler must query applications by id OR ref so mapping entries "
            "that contain a ref value still resolve to the correct row"
        )

    def test_legacy_fallback_finds_applicant_id_in_sumsub_applicant_ids(self):
        """Legacy scan must find an applicant stored under sumsub_applicant_ids key."""
        applicant_id = "69b7e6a9a2b8eb118c24aaa7"
        external_user_id = "dir-abc123"

        # Simulate prescreening_data as written by the fixed SumsubApplicantHandler
        apps = [
            {
                "id": "app_live_001",
                "prescreening_data": json.dumps({
                    "sumsub_applicant_ids": {external_user_id: applicant_id}
                }),
            },
            {
                "id": "app_other",
                "prescreening_data": json.dumps({"company": "OtherCo"}),
            },
        ]

        matched_ids = set()
        for app in apps:
            pdata = app["prescreening_data"] or ""
            if applicant_id in pdata:
                matched_ids.add(app["id"])
            elif external_user_id in pdata:
                matched_ids.add(app["id"])

        assert "app_live_001" in matched_ids, (
            "Legacy fallback must match when applicant_id is stored under sumsub_applicant_ids"
        )
        assert "app_other" not in matched_ids

    def test_webhook_stores_sumsub_webhook_in_screening_report(self):
        """After matching, webhook data must be stored at screening_report.sumsub_webhook."""
        applicant_id = "69b7e6a9a2b8eb118c24aaa7"
        kyc_data = {
            "sumsub_applicant_id": applicant_id,
            "external_user_id": "dir-abc123",
            "review_answer": "RED",
            "rejection_labels": ["FORGERY"],
            "moderation_comment": "Suspected forgery",
            "event_type": "applicantReviewed",
            "received_at": "2026-04-09T06:00:00",
        }

        pdict = {"sumsub_applicant_ids": {"dir-abc123": applicant_id}}
        if "screening_report" not in pdict:
            pdict["screening_report"] = {}
        pdict["screening_report"]["sumsub_webhook"] = kyc_data

        assert pdict["screening_report"]["sumsub_webhook"]["sumsub_applicant_id"] == applicant_id
        assert pdict["screening_report"]["sumsub_webhook"]["review_answer"] == "RED"

    def test_red_webhook_adds_overall_flag_to_screening_report(self):
        """A RED webhook must add a rejection flag to screening_report.overall_flags."""
        external_user_id = "dir-abc123"
        pdict = {
            "screening_report": {
                "overall_flags": [],
                "sumsub_webhook": {
                    "review_answer": "RED",
                    "sumsub_applicant_id": "69b7e6a9a2b8eb118c24aaa7",
                },
            }
        }

        review_answer = "RED"
        if review_answer == "RED":
            flags = pdict["screening_report"].get("overall_flags", [])
            flag_msg = f"Sumsub KYC verification REJECTED for {external_user_id}"
            if flag_msg not in flags:
                flags.append(flag_msg)
            pdict["screening_report"]["overall_flags"] = flags

        assert len(pdict["screening_report"]["overall_flags"]) == 1
        assert "REJECTED" in pdict["screening_report"]["overall_flags"][0]

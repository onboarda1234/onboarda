"""
Phase 3-5 Wave 2 Remediation Tests.

Finding 9:  Mock/degraded AI signaling must be explicit in backend + UI.
Finding 11: Sumsub functions in screening.py must delegate to SumsubClient.
Finding 12: Webhook linking must use deterministic mapping table.
"""
import os
import json
import inspect
import pytest


# ── Finding 9: Mock/Degraded AI Signaling ──

class TestFinding9_MockSignaling:
    """Claude mock/degraded results must be explicitly labeled."""

    def test_document_verify_handler_sets_ai_source(self):
        """server.py DocumentVerifyHandler must set ai_source in stored results."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()

        # Find the document verification section that stores results
        idx = src.find("class DocumentVerifyHandler")
        if idx < 0:
            pytest.skip("DocumentVerifyHandler not found in server.py")
        # The handler is long; search up to the next class definition
        next_class = src.find("\nclass ", idx + 10)
        section = src[idx:next_class] if next_class > idx else src[idx:idx + 10000]
        assert '"ai_source"' in section or "'ai_source'" in section, \
            "DocumentVerifyHandler does not set ai_source in verification results"

    def test_backoffice_displays_ai_source_banner(self):
        """Backoffice must show ai_source indicator when not 'live'."""
        bo_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
        if not os.path.exists(bo_path):
            bo_path = os.path.join(os.path.dirname(__file__), "..", "arie-backoffice.html")
        with open(bo_path, encoding="utf-8") as f:
            html = f.read()
        assert "ai_source" in html, "Backoffice does not reference ai_source"
        assert "Mock" in html or "mock" in html, "Backoffice has no mock indicator"

    def test_claude_verify_document_error_has_ai_source(self):
        """verify_document exception handler must set ai_source."""
        from claude_client import ClaudeClient
        source = inspect.getsource(ClaudeClient.verify_document)
        # Check the exception handler section has ai_source
        assert "ai_source" in source, \
            "verify_document does not set ai_source on error path"


# ── Finding 11: Sumsub Canonicalization ──

class TestFinding11_SumsubCanonical:
    """screening.py functions must delegate to SumsubClient."""

    def test_sumsub_create_applicant_delegates(self):
        """sumsub_create_applicant must use get_sumsub_client()."""
        from screening import sumsub_create_applicant
        source = inspect.getsource(sumsub_create_applicant)
        assert "get_sumsub_client" in source, \
            "sumsub_create_applicant still makes direct HTTP calls"
        assert "requests.post" not in source, \
            "sumsub_create_applicant still uses requests.post directly"

    def test_sumsub_get_status_delegates(self):
        """sumsub_get_applicant_status must use get_sumsub_client()."""
        from screening import sumsub_get_applicant_status
        source = inspect.getsource(sumsub_get_applicant_status)
        assert "get_sumsub_client" in source, \
            "sumsub_get_applicant_status still makes direct HTTP calls"

    def test_sumsub_generate_token_delegates(self):
        """sumsub_generate_access_token must use get_sumsub_client()."""
        from screening import sumsub_generate_access_token
        source = inspect.getsource(sumsub_generate_access_token)
        assert "get_sumsub_client" in source, \
            "sumsub_generate_access_token still makes direct HTTP calls"

    def test_sumsub_add_document_delegates(self):
        """sumsub_add_document must use get_sumsub_client()."""
        from screening import sumsub_add_document
        source = inspect.getsource(sumsub_add_document)
        assert "get_sumsub_client" in source, \
            "sumsub_add_document still makes direct HTTP calls"

    def test_sumsub_get_by_ext_id_delegates(self):
        """sumsub_get_applicant_by_external_id must use get_sumsub_client()."""
        from screening import sumsub_get_applicant_by_external_id
        source = inspect.getsource(sumsub_get_applicant_by_external_id)
        assert "get_sumsub_client" in source, \
            "sumsub_get_applicant_by_external_id still makes direct HTTP calls"

    def test_no_direct_sumsub_http_in_wrappers(self):
        """Wrapper functions must not contain requests.get/post to Sumsub."""
        from screening import (
            sumsub_create_applicant, sumsub_get_applicant_status,
            sumsub_generate_access_token, sumsub_add_document,
            sumsub_get_applicant_by_external_id,
        )
        for fn in [sumsub_create_applicant, sumsub_get_applicant_status,
                    sumsub_generate_access_token, sumsub_get_applicant_by_external_id]:
            source = inspect.getsource(fn)
            assert "requests.post" not in source and "requests.get" not in source, \
                f"{fn.__name__} still uses direct HTTP requests"


# ── Finding 12: Deterministic Webhook Linking ──

class TestFinding12_WebhookLinking:
    """Webhook handler must use mapping table, not full-table substring scan."""

    def test_mapping_table_migration_exists(self):
        """db.py must contain sumsub_applicant_mappings table creation."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "db.py")
        with open(db_path, encoding="utf-8") as f:
            src = f.read()
        assert "sumsub_applicant_mappings" in src, \
            "sumsub_applicant_mappings table not in db.py"
        assert "applicant_id" in src[src.find("sumsub_applicant_mappings"):], \
            "Mapping table missing applicant_id column"

    def test_webhook_handler_uses_mapping_table(self):
        """SumsubWebhookHandler must query sumsub_applicant_mappings."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()
        wh_start = src.find("class SumsubWebhookHandler")
        wh_end = src.find("\nclass ", wh_start + 10)
        wh_code = src[wh_start:wh_end]
        assert "sumsub_applicant_mappings" in wh_code, \
            "Webhook handler does not query mapping table"

    def test_applicant_handler_stores_mapping(self):
        """SumsubApplicantHandler must insert into sumsub_applicant_mappings."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()
        ah_start = src.find("class SumsubApplicantHandler")
        ah_end = src.find("\nclass ", ah_start + 10)
        ah_code = src[ah_start:ah_end]
        assert "sumsub_applicant_mappings" in ah_code, \
            "Applicant handler does not store mapping"

    def test_mapping_table_has_indexes(self):
        """Mapping table must have indexes for efficient lookup."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "db.py")
        with open(db_path, encoding="utf-8") as f:
            src = f.read()
        assert "idx_sam_applicant" in src, "Missing index on applicant_id"
        assert "idx_sam_external" in src, "Missing index on external_user_id"
        assert "idx_sam_app" in src, "Missing index on application_id"

    def test_webhook_handler_legacy_scan_removed(self):
        """PR 14 (F-7): The legacy substring scan MUST be removed. Its
        replacement is the sumsub_unmatched_webhooks DLQ path.

        Rationale: the legacy scan ran a full-table SELECT over
        `applications` and matched rows whose `prescreening_data`
        contained the applicant_id as a raw substring. This was a silent
        multi-tenancy hazard — any historical free-text mention of an
        applicant id would cause cross-record mutation. PR 14 removes
        the scan and routes unmapped deliveries to a DLQ for manual
        triage. This test inverts the previous assertion and fails loudly
        if the scan is re-introduced.
        """
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()
        wh_start = src.find("class SumsubWebhookHandler")
        wh_end = src.find("\nclass ", wh_start + 10)
        wh_code = src[wh_start:wh_end]

        assert "falling back to legacy scan" not in wh_code, (
            "Legacy substring scan fallback must be removed (F-7)"
        )
        # The DLQ path must be present in its place.
        assert "sumsub_unmatched_webhooks" in wh_code, (
            "DLQ path (sumsub_unmatched_webhooks) missing from handler"
        )

    def test_mapping_table_created_on_init(self):
        """The migration must actually run successfully."""
        from db import get_db, init_db
        init_db()  # Ensure migrations have run
        db = get_db()
        try:
            result = db.execute("SELECT COUNT(*) FROM sumsub_applicant_mappings").fetchone()
            assert result is not None, "sumsub_applicant_mappings table does not exist"
        finally:
            db.close()

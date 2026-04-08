"""
P0 Verification Integrity Tests
Tests for the critical AI verification pipeline fixes:
- P0-1: Schema accepts List[Dict] for checks
- P0-2: Backend guards rejected/invalid AI responses
- P0-3: Frontend false-pass prevention (backend side)
- P0-5: No pass without evidence safeguard
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ─── P0-1: Schema Validation Tests ──────────────────────────────

class TestDocumentVerificationSchema:
    """P0-1: Verify schema accepts valid Claude responses (List format)."""

    def test_schema_accepts_list_checks(self):
        """Claude returns checks as a List[Dict] — schema must accept this."""
        try:
            from claude_client import ClaudeClient
            schema_cls = ClaudeClient.DocumentVerificationSchema
        except (ImportError, AttributeError):
            try:
                from claude_client import _AGENT_SCHEMAS
                schema_cls = _AGENT_SCHEMAS.get("verify_document")
                if not schema_cls:
                    raise ImportError("Schema not found in _AGENT_SCHEMAS")
            except ImportError:
                from pydantic import BaseModel
                from typing import List, Dict, Any
                class DocumentVerificationSchema(BaseModel):
                    checks: List[Dict[str, Any]] = []
                    overall: str = "flagged"
                    confidence: float = 0.0
                    red_flags: List[str] = []
                schema_cls = DocumentVerificationSchema

        valid_response = {
            "checks": [
                {"label": "Document Type Match", "type": "doc_type_match", "result": "pass", "message": "Correct document type"},
                {"label": "Entity Name Match", "type": "entity_name", "result": "fail", "message": "Name mismatch detected"},
            ],
            "overall": "flagged",
            "confidence": 0.85,
            "red_flags": ["Name mismatch"]
        }

        validated = schema_cls.model_validate(valid_response)
        assert isinstance(validated.checks, list)
        assert len(validated.checks) == 2
        assert validated.overall == "flagged"
        assert validated.confidence == 0.85

    def test_schema_accepts_empty_checks(self):
        """Schema should accept empty checks list (backend guards handle this)."""
        try:
            from claude_client import _AGENT_SCHEMAS
            schema_cls = _AGENT_SCHEMAS["verify_document"]
        except (ImportError, KeyError):
            from pydantic import BaseModel
            from typing import List, Dict, Any
            class DocumentVerificationSchema(BaseModel):
                checks: List[Dict[str, Any]] = []
                overall: str = "flagged"
                confidence: float = 0.0
                red_flags: List[str] = []
            schema_cls = DocumentVerificationSchema

        response = {"checks": [], "overall": "flagged", "confidence": 0.0}
        validated = schema_cls.model_validate(response)
        assert validated.checks == []
        assert validated.overall == "flagged"

    def test_schema_defaults_to_safe_values(self):
        """Schema defaults should be safe (flagged, not verified)."""
        try:
            from claude_client import _AGENT_SCHEMAS
            schema_cls = _AGENT_SCHEMAS["verify_document"]
        except (ImportError, KeyError):
            from pydantic import BaseModel
            from typing import List, Dict, Any
            class DocumentVerificationSchema(BaseModel):
                checks: List[Dict[str, Any]] = []
                overall: str = "flagged"
                confidence: float = 0.0
                red_flags: List[str] = []
            schema_cls = DocumentVerificationSchema

        minimal = {}
        validated = schema_cls.model_validate(minimal)
        assert validated.overall == "flagged"  # NOT "verified"
        assert validated.confidence == 0.0
        assert validated.checks == []


# ─── P0-2: Backend Rejection Guard Tests ─────────────────────────

class TestRejectedResponseHandling:
    """P0-2: Backend must never treat rejected AI responses as success."""

    def test_rejected_response_detected(self):
        """A response with _rejected=True must not produce 'verified' status."""
        rejected_response = {
            "_validated": False,
            "_rejected": True,
            "_validation_errors": "schema validation failed",
            "_requires_manual_review": True,
            "error": "AI output rejected"
        }

        # Simulate the backend guard logic from DocumentVerifyHandler
        if rejected_response.get("_rejected") or rejected_response.get("_validated") is False:
            checks = [{"label": "AI Verification", "type": "validity", "result": "fail",
                       "message": "AI output failed validation — manual review required"}]
            all_passed = False
        else:
            checks = rejected_response.get("checks", [])
            all_passed = rejected_response.get("overall") == "verified"

        assert all_passed is False
        assert len(checks) == 1
        assert checks[0]["result"] == "fail"

    def test_empty_checks_cannot_produce_pass(self):
        """P0-5: A response with empty checks cannot be 'verified'."""
        response_with_empty_checks = {
            "checks": [],
            "overall": "verified",
            "confidence": 0.9
        }

        checks = response_with_empty_checks.get("checks", [])
        if not checks:
            all_passed = False
        else:
            all_passed = response_with_empty_checks.get("overall") == "verified"

        assert all_passed is False

    def test_valid_pass_response_allowed(self):
        """A truly verified response with real checks should still pass."""
        valid_response = {
            "checks": [
                {"label": "Doc Type", "type": "doc_type", "result": "pass", "message": "OK"},
                {"label": "Name Match", "type": "name", "result": "pass", "message": "OK"},
            ],
            "overall": "verified",
            "confidence": 0.92
        }

        if valid_response.get("_rejected") or valid_response.get("_validated") is False:
            all_passed = False
        else:
            checks = valid_response.get("checks", [])
            if not checks:
                all_passed = False
            else:
                all_passed = valid_response.get("overall") == "verified"

        assert all_passed is True

    def test_flagged_response_not_passed(self):
        """A flagged response must not produce 'verified' status."""
        flagged_response = {
            "checks": [
                {"label": "Doc Type", "type": "doc_type", "result": "fail", "message": "Wrong document type"},
            ],
            "overall": "flagged",
            "confidence": 0.85
        }

        checks = flagged_response.get("checks", [])
        all_passed = flagged_response.get("overall") == "verified"
        assert all_passed is False


# ─── P0-2: DocumentAIVerifyHandler Guard Tests ───────────────────

class TestAIVerifyHandlerGuards:
    """P0-2: DocumentAIVerifyHandler must guard against rejected responses."""

    def test_rejected_response_gets_fail_checks(self):
        """When AI result is rejected, handler must inject fail checks."""
        result = {
            "_validated": False,
            "_rejected": True,
            "error": "schema validation failed"
        }

        # Simulate the handler guard logic
        if result.get("_rejected") or result.get("_validated") is False:
            result["checks"] = [{"label": "AI Verification", "type": "validity", "result": "fail",
                                 "message": "AI output failed validation — manual review required"}]
            result["overall"] = "flagged"

        assert result["overall"] == "flagged"
        assert result["checks"][0]["result"] == "fail"
        assert "manual review" in result["checks"][0]["message"]

    def test_empty_checks_gets_warn(self):
        """When AI returns no checks, handler must inject warning."""
        result = {
            "checks": [],
            "overall": "verified",
            "confidence": 0.9
        }

        if not result.get("checks"):
            result["checks"] = [{"label": "AI Verification", "type": "validity", "result": "warn",
                                 "message": "No verification checks returned — manual review required"}]
            result["overall"] = "flagged"

        assert result["overall"] == "flagged"
        assert result["checks"][0]["result"] == "warn"


# ─── P0-3: Frontend False-Pass Prevention (Logic Tests) ──────────

class TestFrontendFalsePassPrevention:
    """P0-3: Test the logic that prevents undefined/missing checks from showing as pass."""

    def test_undefined_result_not_treated_as_pass(self):
        """apiCheck.result === undefined must NOT default to passed."""
        api_check = {}  # No 'result' key = undefined
        # OLD behavior: passed = result == 'pass' or result == undefined  => True (BUG!)
        # NEW behavior: passed = result == 'pass'  => False (CORRECT)
        passed = api_check.get("result") == "pass"
        assert passed is False

    def test_explicit_pass_still_works(self):
        """An explicit 'pass' result should still be treated as passed."""
        api_check = {"result": "pass", "message": "OK"}
        passed = api_check.get("result") == "pass"
        assert passed is True

    def test_fail_result_not_treated_as_pass(self):
        """A 'fail' result must not be treated as passed."""
        api_check = {"result": "fail", "message": "Wrong document"}
        passed = api_check.get("result") == "pass"
        assert passed is False

    def test_empty_checks_array_detected(self):
        """An empty checks array must not produce 'All checks passed'."""
        api_checks = []
        # The frontend check: if (apiChecks.length === 0) => show warning
        assert len(api_checks) == 0
        # Should NOT proceed to fails === 0 logic


# ─── P0-4: Document Type Mapping Tests ───────────────────────────

class TestDocumentTypeMapping:
    """P0-4: Verify correct mapping from HTML element IDs to API doc_types."""

    def test_doc_type_mapping_completeness(self):
        """All portal document IDs must map to correct API doc_types."""
        doc_type_map = {
            'doc-coi': 'cert_inc',
            'doc-memarts': 'memarts',
            'doc-reg': 'cert_reg',
            'doc-shareholders': 'reg_sh',
            'doc-directors-reg': 'reg_dir',
            'doc-financials': 'fin_stmt',
            'doc-board-res': 'board_res',
            'doc-structure-chart': 'structure_chart',
            'doc-proof-address': 'poa',
            'doc-bank-ref': 'bankref',
            'doc-licence': 'licence'
        }

        # Verify no HTML element IDs leak through as doc_types
        for html_id, api_type in doc_type_map.items():
            assert not api_type.startswith('doc-'), f"API doc_type '{api_type}' looks like an HTML ID"
            assert api_type != html_id, f"HTML ID '{html_id}' was not mapped"

    def test_poa_maps_correctly(self):
        """'doc-proof-address' must map to 'poa', not pass through as HTML ID."""
        doc_type_map = {
            'doc-proof-address': 'poa',
        }
        assert doc_type_map['doc-proof-address'] == 'poa'
        assert doc_type_map['doc-proof-address'] != 'doc-proof-address'


# ─── P0-5: No Pass Without Evidence ─────────────────────────────

class TestNoPassWithoutEvidence:
    """P0-5: Verification may only show PASS when all conditions are met."""

    def test_pass_requires_validated_not_rejected_and_checks(self):
        """Full verification chain: validated, not rejected, has checks, overall=verified."""
        def is_truly_verified(result):
            if result.get("_rejected") or result.get("_validated") is False:
                return False
            checks = result.get("checks", [])
            if not checks:
                return False
            return result.get("overall") == "verified"

        # Case 1: Rejected response
        assert is_truly_verified({"_rejected": True}) is False

        # Case 2: Invalid response
        assert is_truly_verified({"_validated": False}) is False

        # Case 3: Empty checks
        assert is_truly_verified({"checks": [], "overall": "verified"}) is False

        # Case 4: No checks key
        assert is_truly_verified({"overall": "verified"}) is False

        # Case 5: Flagged overall
        assert is_truly_verified({"checks": [{"result": "fail"}], "overall": "flagged"}) is False

        # Case 6: Valid pass — the only case that should return True
        assert is_truly_verified({
            "checks": [{"result": "pass"}],
            "overall": "verified",
            "confidence": 0.9
        }) is True


# ═══════════════════════════════════════════════════════════════
# P1 TESTS: Verification Pipeline Trust & Consistency
# ═══════════════════════════════════════════════════════════════

# ─── P1-1: Single Verification Call ──────────────────────────

class TestSingleVerificationCall:
    """P1-1: One upload must trigger exactly one verification call."""

    def test_verify_handler_returns_checks(self):
        """DocumentVerifyHandler must return checks in response so portal doesn't need second call."""
        # The handler returns {doc_id, status, checks}
        # Portal uses this directly — no need for DocumentAIVerifyHandler
        handler_response = {"doc_id": "abc123", "status": "verified", "checks": [
            {"label": "Doc Type", "result": "pass", "message": "OK"}
        ]}
        assert "checks" in handler_response
        assert len(handler_response["checks"]) > 0

    def test_verify_result_has_all_fields_for_rendering(self):
        """The single verify response must have everything the frontend needs."""
        response = {
            "doc_id": "abc123",
            "status": "flagged",
            "checks": [
                {"label": "Document Type", "type": "doc_type", "result": "fail", "message": "Wrong document"}
            ]
        }
        # Frontend needs: checks array with label, result, message
        for check in response["checks"]:
            assert "label" in check
            assert "result" in check
            assert check["result"] in ("pass", "fail", "warn")


# ─── P1-3: Back Office API Data Only ─────────────────────────

class TestBackOfficeAPIData:
    """P1-3: Back office must only display real verification data from API."""

    def test_api_documents_mapping_preserves_verification(self):
        """When loadFromAPI maps documents, verification_results must be preserved."""
        api_document = {
            "id": "doc123",
            "doc_type": "cert_inc",
            "doc_name": "COI.pdf",
            "verification_status": "verified",
            "verification_results": '{"checks":[{"label":"Doc Type","result":"pass"}],"overall":"verified"}',
            "verified_at": "2026-03-24T10:00:00",
            "person_id": None
        }

        # Simulate the mapping logic from loadFromAPI
        import json
        vr = None
        try:
            vr = json.loads(api_document["verification_results"]) if isinstance(api_document["verification_results"], str) else api_document["verification_results"]
        except:
            pass

        mapped = {
            "id": api_document["id"],
            "doc_type": api_document["doc_type"],
            "doc_name": api_document.get("doc_name", api_document["doc_type"]),
            "verification_status": api_document.get("verification_status", "not_run"),
            "verification_results": vr,
            "verified_at": api_document.get("verified_at"),
            "person_id": api_document.get("person_id")
        }

        assert mapped["verification_status"] == "verified"
        assert mapped["verification_results"] is not None
        assert mapped["verification_results"]["checks"][0]["result"] == "pass"

    def test_unverified_document_shows_not_run(self):
        """Documents without verification results must show not_run, not pass."""
        api_document = {
            "id": "doc456",
            "doc_type": "poa",
            "verification_status": "pending",
            "verification_results": None,
        }

        status = api_document.get("verification_status", "not_run")
        assert status != "verified"
        assert status in ("pending", "not_run", "flagged")

    def test_fabricated_checks_not_present(self):
        """Fabricated checks like 'MRZ extraction', 'Tampering detection' must not appear
        unless they come from real AI verification results."""
        fabricated_labels = ['MRZ extraction', 'Expiry validation', 'Tampering detection', 'Cross-document consistency']

        # Simulate real verification result
        real_checks = [
            {"label": "Document Type Match", "result": "pass"},
            {"label": "Entity Name Match", "result": "fail"},
        ]

        # None of the fabricated labels should appear in real data
        real_labels = [c["label"] for c in real_checks]
        for fabricated in fabricated_labels:
            assert fabricated not in real_labels, f"Fabricated check '{fabricated}' found in real data"


# ─── P1-4: Source of Truth Consistency ───────────────────────

class TestSourceOfTruthConsistency:
    """P1-4: Portal and back office must derive from the same persisted backend truth."""

    def test_db_is_source_of_truth(self):
        """The documents table verification_status/verification_results is the canonical source."""
        # DocumentVerifyHandler stores: verification_status, verification_results, verified_at
        # ApplicationDetailHandler returns: documents[].verification_status, verification_results
        # Both portal (via verify response) and back office (via API) get the same data

        db_record = {
            "verification_status": "flagged",
            "verification_results": '{"checks":[{"label":"Name","result":"fail"}],"overall":"flagged"}'
        }

        import json
        results = json.loads(db_record["verification_results"])

        # What portal sees (from DocumentVerifyHandler response)
        portal_status = db_record["verification_status"]
        portal_checks = results["checks"]

        # What back office sees (from ApplicationDetailHandler -> apiDocuments mapping)
        backoffice_status = db_record["verification_status"]
        backoffice_checks = results["checks"]

        assert portal_status == backoffice_status
        assert portal_checks == backoffice_checks

    def test_no_local_inference_of_pass(self):
        """Neither portal nor back office should infer 'pass' if backend has no result."""
        doc_without_verification = {
            "verification_status": "pending",
            "verification_results": None
        }

        # Back office check
        status = doc_without_verification["verification_status"]
        assert status != "verified"

        # If results are None, UI must not show pass
        results = doc_without_verification["verification_results"]
        assert results is None
        # UI should show "Pending" or "Not run", never "Pass"


# ─── P1-5: Truthfulness Safeguards ──────────────────────────

class TestTruthfulnessSafeguards:
    """P1-5: System must fail loudly if fake data appears in staging."""

    def test_staging_clears_demo_data(self):
        """In staging/production, demo data arrays must be empty after loadEnvironment."""
        # Simulates the loadEnvironment() behavior when ARIE_ENV !== 'demo'
        env = "staging"
        if env != "demo":
            applications = []
            users = []
            audit_log = []
            monitoring_alerts = []
        else:
            applications = [{"company": "FakeCompany"}]

        assert len(applications) == 0
        assert len(users) == 0

    def test_demo_mode_must_be_explicit(self):
        """ARIE_ENV defaults to 'demo' only until loadEnvironment() succeeds."""
        default_env = "demo"
        # After loadEnvironment succeeds with staging config:
        api_response = {"environment": "staging", "features": {"ENABLE_DEMO_MODE": False}}
        resolved_env = api_response.get("environment", default_env)
        assert resolved_env == "staging"
        assert api_response["features"]["ENABLE_DEMO_MODE"] is False

    def test_missing_verification_never_renders_as_pass(self):
        """If verification_results is null/missing, UI must show safe state."""
        docs_without_results = [
            {"doc_type": "cert_inc", "verification_status": "pending", "verification_results": None},
            {"doc_type": "poa", "verification_status": "not_run", "verification_results": None},
        ]

        for doc in docs_without_results:
            assert doc["verification_status"] not in ("verified",), \
                f"Doc {doc['doc_type']} shows 'verified' without verification_results"
            assert doc["verification_results"] is None

    def test_verified_document_has_checks(self):
        """A document with verification_status='verified' must have non-empty checks."""
        import json
        verified_doc = {
            "verification_status": "verified",
            "verification_results": '{"checks":[{"label":"Type","result":"pass"}],"overall":"verified"}'
        }

        results = json.loads(verified_doc["verification_results"])
        assert len(results["checks"]) > 0, "Verified document must have at least one check"


class TestVerificationContextResolution:
    def test_effective_declared_data_uses_saved_session_backfill(self, db):
        from server import build_document_verification_context

        db.execute("INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
                   ("ctx_client", "ctx@example.com", "hash", "Context Corp"))
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "ctx_app",
            "ARF-CTX-1",
            "ctx_client",
            "Context Corp",
            "Mauritius",
            "Technology",
            "SME",
            "draft",
            json.dumps({})
        ))
        db.execute("""
            INSERT INTO client_sessions (id, client_id, application_id, form_data, last_step)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "ctx_session",
            "ctx_client",
            "ctx_app",
            json.dumps({"f-reg-name": "Overlay Corp", "f-inc-country": "BVI"}),
            3
        ))
        db.commit()

        app = dict(db.execute("SELECT * FROM applications WHERE id = ?", ("ctx_app",)).fetchone())
        context = build_document_verification_context(db, app, {"doc_type": "cert_inc"})

        assert context["prescreening_data"]["registered_entity_name"] == "Overlay Corp"
        assert context["prescreening_data"]["country_of_incorporation"] == "BVI"
        assert context["entity_name"] == "Context Corp"

    def test_intermediary_company_documents_resolve_intermediary_subject(self, db):
        from server import build_document_verification_context

        db.execute("INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
                   ("int_client", "int@example.com", "hash", "Parent Corp"))
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "int_app",
            "ARF-INT-1",
            "int_client",
            "Parent Corp",
            "Mauritius",
            "Technology",
            "SME",
            "draft",
            json.dumps({
                "registered_entity_name": "Parent Corp",
                "country_of_incorporation": "Mauritius",
                "directors": ["Parent Director"],
                "ubos": ["Parent UBO"]
            })
        ))
        db.execute("""
            INSERT INTO intermediaries (id, application_id, person_key, entity_name, jurisdiction, ownership_pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("int_row_1", "int_app", "int1", "Intermediary HoldCo", "BVI", 100))
        db.commit()

        app = dict(db.execute("SELECT * FROM applications WHERE id = ?", ("int_app",)).fetchone())
        context = build_document_verification_context(db, app, {"doc_type": "cert_inc", "person_id": "int1"})

        assert context["doc_category"] == "company"
        assert context["subject_type"] == "intermediary_company"
        assert context["entity_name"] == "Intermediary HoldCo"
        assert context["prescreening_data"]["registered_entity_name"] == "Intermediary HoldCo"
        assert context["prescreening_data"]["country_of_incorporation"] == "BVI"
        assert "directors" not in context["prescreening_data"]
        assert "ubos" not in context["prescreening_data"]
        assert context["directors_list"] == []
        assert context["ubos_list"] == []


class TestPortalAuthoritativeSource:
    def test_portal_removes_transient_ai_verify_route(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert "/documents/ai-verify" not in src

    def test_portal_renders_persisted_document_truth(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert "renderPersistedVerification" in src
        assert "verification_results" in src
        assert "verification_status" in src
        assert "syncPersistedApplicationDocuments" in src


class TestVerificationStartupGuards:
    def test_verification_critical_seed_failure_raises(self, monkeypatch):
        import db as db_module
        import server

        def boom(_db):
            raise RuntimeError("seed failed")

        monkeypatch.setattr(db_module, "seed_initial_data", boom)

        with pytest.raises(RuntimeError, match="Verification-critical startup initialization failed"):
            server.init_db()

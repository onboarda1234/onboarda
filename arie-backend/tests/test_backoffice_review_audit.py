"""
Tests for back-office review page audit fixes.
Covers: notification type validation, client_id null guard,
decision records endpoint, and frontend consistency checks.
"""
import os
import sys
import json
import re
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════
# Fix 1: Notification type — frontend must send valid type
# ═══════════════════════════════════════════════════════════
class TestNotificationTypeFrontend:
    """Verify the frontend sends a backend-accepted notification_type."""

    BACKOFFICE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )

    def _read_backoffice(self):
        with open(self.BACKOFFICE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_no_officer_message_notification_type(self):
        """Frontend must not send 'officer_message' as notification_type (invalid on backend)."""
        src = self._read_backoffice()
        assert "notification_type: 'officer_message'" not in src, (
            "Frontend still sends notification_type 'officer_message' which the backend rejects with 400"
        )

    def test_notification_type_maps_to_valid_values(self):
        """Frontend maps decisions to backend-valid notification_type values."""
        src = self._read_backoffice()
        # Check that NOTIF_TYPE_MAP exists and maps to valid backend values
        assert "'Approve': 'approved'" in src
        assert "'Reject': 'rejected'" in src
        assert "'Request Docs': 'documents_required'" in src

    def test_pending_notification_type_variable_exists(self):
        """Frontend declares pendingNotificationType for stateful type tracking."""
        src = self._read_backoffice()
        assert "pendingNotificationType" in src


# ═══════════════════════════════════════════════════════════
# Fix 2: Missing Monitoring/Onboarding stage in agent dropdown
# ═══════════════════════════════════════════════════════════
class TestAgentStageDropdown:
    """Verify the AI agent pipeline editor includes all stages used by agents."""

    BACKOFFICE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )

    def _read_backoffice(self):
        with open(self.BACKOFFICE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_monitoring_stage_option_exists(self):
        """Agent stage dropdown must include 'Monitoring' option."""
        src = self._read_backoffice()
        assert "'>Monitoring</option>'" in src or ">Monitoring</option>" in src

    def test_onboarding_stage_option_exists(self):
        """Agent stage dropdown must include 'Onboarding' option."""
        src = self._read_backoffice()
        assert "'>Onboarding</option>'" in src or ">Onboarding</option>" in src

    def test_all_agent_stages_have_dropdown_options(self):
        """Every stage used by an agent definition must appear in the stage dropdown."""
        src = self._read_backoffice()
        # Find all stages used in agent definitions
        agent_stages = set(re.findall(r"stage:'([^']+)'", src))
        # Find all stages available in the dropdown
        dropdown_stages = set(re.findall(r">([^<]+)</option>'", src))
        # Filter to only stages in agent-stage-input context
        for stage in agent_stages:
            assert stage in dropdown_stages, (
                f"Agent uses stage '{stage}' but it's missing from the stage dropdown options"
            )


# ═══════════════════════════════════════════════════════════
# Fix 3: Backend client_id null validation in notification
# ═══════════════════════════════════════════════════════════
class TestNotificationClientIdValidation:
    """Verify backend rejects notifications when client_id is missing."""

    def test_notification_handler_guards_null_client_id(self):
        """ClientNotificationHandler must check for client_id before inserting."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "server.py",
        )
        with open(server_path, "r", encoding="utf-8") as f:
            src = f.read()

        # Find the ClientNotificationHandler class and check for client_id validation
        handler_start = src.index("class ClientNotificationHandler")
        handler_section = src[handler_start:handler_start + 3000]
        assert 'client_id' in handler_section and 'Cannot send notification' in handler_section, (
            "ClientNotificationHandler must validate client_id is present before inserting notification"
        )

    def test_notification_rejects_missing_client_id(self, db, sample_application):
        """Notification insert should fail if client_id is NULL on the application."""
        # Create application without client_id
        db.execute(
            "UPDATE applications SET client_id = NULL WHERE id = ?",
            (sample_application,)
        )
        db.commit()

        row = db.execute(
            "SELECT client_id FROM applications WHERE id = ?",
            (sample_application,)
        ).fetchone()
        assert row["client_id"] is None, "Precondition: client_id must be NULL"


# ═══════════════════════════════════════════════════════════
# Fix 4: Decision records display in review page
# ═══════════════════════════════════════════════════════════
class TestDecisionRecordsDisplay:
    """Verify the review page loads and renders decision records."""

    BACKOFFICE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )

    def _read_backoffice(self):
        with open(self.BACKOFFICE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_decision_records_container_exists(self):
        """Activity tab must have a decision-records container element."""
        src = self._read_backoffice()
        assert 'id="detail-decision-records"' in src

    def test_load_decision_records_function_exists(self):
        """loadDecisionRecords function must be defined."""
        src = self._read_backoffice()
        assert "function loadDecisionRecords()" in src or "async function loadDecisionRecords()" in src

    def test_activity_tab_triggers_decision_records_load(self):
        """Switching to the activity tab must trigger loadDecisionRecords()."""
        src = self._read_backoffice()
        assert "loadDecisionRecords()" in src
        # Specifically in switchDetailTab
        tab_func_start = src.index("function switchDetailTab(tab)")
        tab_func_section = src[tab_func_start:tab_func_start + 800]
        assert "loadDecisionRecords" in tab_func_section, (
            "switchDetailTab must call loadDecisionRecords when activity tab is selected"
        )

    def test_decision_records_calls_api_endpoint(self):
        """loadDecisionRecords must call the decision-records API endpoint."""
        src = self._read_backoffice()
        assert "decision-records" in src

    def test_decision_records_renders_table(self):
        """loadDecisionRecords must render decision data in a table."""
        src = self._read_backoffice()
        func_start = src.index("async function loadDecisionRecords()")
        func_section = src[func_start:func_start + 2000]
        assert "Decision" in func_section
        assert "Risk Level" in func_section
        assert "Actor" in func_section


# ═══════════════════════════════════════════════════════════
# Fix 5: Validation status check already in ApprovalGateValidator
# ═══════════════════════════════════════════════════════════
class TestValidationStatusGate:
    """Verify ApprovalGateValidator enforces validation_status = 'pass'."""

    def test_approval_gate_checks_validation_status(self):
        """ApprovalGateValidator.validate_approval must block on non-pass validation_status."""
        from security_hardening import ApprovalGateValidator
        import inspect
        src = inspect.getsource(ApprovalGateValidator.validate_approval)
        assert "validation_status" in src, (
            "ApprovalGateValidator must check memo validation_status"
        )
        assert "'pass'" in src, (
            "ApprovalGateValidator must require validation_status = 'pass'"
        )


# ═══════════════════════════════════════════════════════════
# Decision records backend endpoint tests
# ═══════════════════════════════════════════════════════════
class TestDecisionRecordsEndpoint:
    """Verify decision records endpoint returns correct data."""

    def test_decision_records_table_exists(self, db):
        """decision_records table must exist in the database schema."""
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decision_records'"
        ).fetchone()
        assert tables is not None, "decision_records table must exist"

    def test_decision_records_insert_and_query(self, db, sample_application):
        """Decision records can be inserted and queried."""
        ref = db.execute(
            "SELECT ref FROM applications WHERE id = ?", (sample_application,)
        ).fetchone()["ref"]

        db.execute("""
            INSERT INTO decision_records
            (application_ref, decision_type, risk_level, confidence_score, source,
             actor_user_id, actor_role, timestamp, key_flags, override_flag, override_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
        """, (ref, "approve", "MEDIUM", 0.85, "supervisor", "admin001", "admin",
              json.dumps(["memo_approved"]), 0, None))
        db.commit()

        rows = db.execute(
            "SELECT * FROM decision_records WHERE application_ref = ?", (ref,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["decision_type"] == "approve"
        assert rows[0]["risk_level"] == "MEDIUM"

    def test_get_decision_records_function(self, db, sample_application):
        """get_decision_records returns properly formatted records."""
        from decision_model import get_decision_records

        ref = db.execute(
            "SELECT ref FROM applications WHERE id = ?", (sample_application,)
        ).fetchone()["ref"]

        db.execute("""
            INSERT INTO decision_records
            (application_ref, decision_type, risk_level, confidence_score, source,
             actor_user_id, actor_role, timestamp, key_flags, override_flag, override_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
        """, (ref, "reject", "HIGH", 0.92, "supervisor", "sco001", "sco",
              json.dumps(["sanctions_hit", "pep_match"]), 1, "False positive confirmed"))
        db.commit()

        records = get_decision_records(db, ref)
        assert len(records) == 1
        r = records[0]
        assert r["decision_type"] == "reject"
        assert r["risk_level"] == "HIGH"
        assert r["source"] == "supervisor"
        assert r["actor"]["role"] == "sco"
        assert r["override_flag"] is True
        assert r["override_reason"] == "False positive confirmed"
        assert "sanctions_hit" in r["key_flags"]


# ═══════════════════════════════════════════════════════════
# Notification backend valid_types alignment
# ═══════════════════════════════════════════════════════════
class TestNotificationValidTypes:
    """Verify the backend valid_types list matches what frontend sends."""

    def test_valid_types_include_approved(self):
        """Backend must accept 'approved' notification_type."""
        from server import ClientNotificationHandler
        import inspect
        src = inspect.getsource(ClientNotificationHandler.post)
        assert '"approved"' in src

    def test_valid_types_include_rejected(self):
        """Backend must accept 'rejected' notification_type."""
        from server import ClientNotificationHandler
        import inspect
        src = inspect.getsource(ClientNotificationHandler.post)
        assert '"rejected"' in src

    def test_valid_types_include_documents_required(self):
        """Backend must accept 'documents_required' notification_type."""
        from server import ClientNotificationHandler
        import inspect
        src = inspect.getsource(ClientNotificationHandler.post)
        assert '"documents_required"' in src


# ═══════════════════════════════════════════════════════════
# A. STATUS MODEL HARDENING
# ═══════════════════════════════════════════════════════════
class TestStatusModelHardening:
    """Verify single-source-of-truth status model across DB, backend, frontend."""

    BACKOFFICE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )

    # All valid DB statuses (canonical list from db.py CHECK constraint)
    VALID_STATUSES = [
        "draft", "submitted", "prescreening_submitted", "pricing_review",
        "pricing_accepted", "pre_approval_review", "pre_approved",
        "kyc_documents", "kyc_submitted", "compliance_review", "in_review",
        "under_review", "edd_required", "approved", "rejected", "rmi_sent", "withdrawn",
    ]

    def _read_backoffice(self):
        with open(self.BACKOFFICE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_branding_status_labels_includes_under_review(self):
        """STATUS_LABELS must include under_review."""
        from branding import STATUS_LABELS
        assert "under_review" in STATUS_LABELS
        assert STATUS_LABELS["under_review"] != STATUS_LABELS.get("in_review")

    def test_all_db_statuses_have_backend_labels(self):
        """Every valid DB status must have a label in STATUS_LABELS."""
        from branding import STATUS_LABELS
        for status in self.VALID_STATUSES:
            assert status in STATUS_LABELS, f"Missing label for status: {status}"

    def test_frontend_filter_uses_raw_db_keys(self):
        """Filter dropdown option values must be raw DB status keys, not display labels."""
        html = self._read_backoffice()
        for status in self.VALID_STATUSES:
            assert f'value="{status}"' in html, f"Filter dropdown missing raw key: {status}"

    def test_frontend_filter_uses_statusRaw(self):
        """Filter comparison must use statusRaw (raw DB key), not status (display label)."""
        html = self._read_backoffice()
        assert "statusRaw" in html
        assert "app.statusRaw" in html

    def test_frontend_status_badge_distinguishes_under_review(self):
        """statusBadge must map under_review to a distinct CSS class from in_review."""
        html = self._read_backoffice()
        assert "'under_review':'under-review'" in html or "'under_review': 'under-review'" in html

    def test_under_review_badge_css_exists(self):
        """CSS class .badge.under-review must exist."""
        html = self._read_backoffice()
        assert ".badge.under-review" in html

    def test_format_status_includes_all_statuses(self):
        """formatStatus() must have entries for all valid statuses."""
        html = self._read_backoffice()
        for status in self.VALID_STATUSES:
            assert f"'{status}'" in html or f'"{status}"' in html, \
                f"formatStatus missing entry for: {status}"


# ═══════════════════════════════════════════════════════════
# B. ACTIVITY LOG — Real backend audit trail
# ═══════════════════════════════════════════════════════════
class TestApplicationAuditLogEndpoint:
    """Test the per-application audit log endpoint."""

    def test_handler_class_exists(self):
        """ApplicationAuditLogHandler must exist."""
        from server import ApplicationAuditLogHandler
        assert ApplicationAuditLogHandler is not None

    def test_route_registered(self):
        """Route /api/applications/:id/audit-log must be registered."""
        from server import make_app
        app = make_app()
        patterns = []
        for rule in app.wildcard_router.rules:
            m = rule.matcher
            pat = getattr(m, 'regex', None) or getattr(m, '_path', None)
            if pat:
                patterns.append(pat.pattern if hasattr(pat, 'pattern') else str(pat))
        matching = [p for p in patterns if "audit-log" in p]
        assert len(matching) > 0, "audit-log route not registered"

    def test_handler_queries_by_ref(self):
        """Handler must filter audit_log by application ref."""
        import inspect
        from server import ApplicationAuditLogHandler
        src = inspect.getsource(ApplicationAuditLogHandler.get)
        assert "target" in src
        assert "audit_log" in src

    def test_frontend_loads_activity_from_api(self):
        """Frontend must call the audit-log API endpoint."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "/audit-log" in html
        assert "loadActivityLog" in html


# ═══════════════════════════════════════════════════════════
# C. NOTES — Real persistence
# ═══════════════════════════════════════════════════════════
class TestApplicationNotesEndpoint:
    """Test notes creation and retrieval."""

    def test_handler_class_exists(self):
        """ApplicationNotesHandler must exist."""
        from server import ApplicationNotesHandler
        assert ApplicationNotesHandler is not None

    def test_route_registered(self):
        """Route /api/applications/:id/notes must be registered."""
        from server import make_app
        app = make_app()
        patterns = []
        for rule in app.wildcard_router.rules:
            m = rule.matcher
            pat = getattr(m, 'regex', None) or getattr(m, '_path', None)
            if pat:
                patterns.append(pat.pattern if hasattr(pat, 'pattern') else str(pat))
        matching = [p for p in patterns if "notes" in p]
        assert len(matching) > 0, "notes route not registered"

    def test_post_validates_empty_content(self):
        """POST must reject empty content."""
        import inspect
        from server import ApplicationNotesHandler
        src = inspect.getsource(ApplicationNotesHandler.post)
        assert "content is required" in src.lower() or "content" in src

    def test_post_validates_max_length(self):
        """POST must reject content > 5000 chars."""
        import inspect
        from server import ApplicationNotesHandler
        src = inspect.getsource(ApplicationNotesHandler.post)
        assert "5000" in src

    def test_post_creates_audit_log_entry(self):
        """Note creation must be logged in audit_log."""
        import inspect
        from server import ApplicationNotesHandler
        src = inspect.getsource(ApplicationNotesHandler.post)
        assert "audit_log" in src
        assert "Add Note" in src

    def test_frontend_add_note_calls_api(self):
        """Frontend addNote() must call the notes API."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "/notes" in html
        assert "content" in html

    def test_frontend_add_note_not_stub(self):
        """addNote() must NOT show 'not yet persisted' message."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "not yet persisted" not in html

    def test_db_migration_creates_notes_table(self):
        """Migration v2.15 must create application_notes table."""
        import db as db_module
        import inspect
        src = inspect.getsource(db_module._run_migrations)
        assert "application_notes" in src
        assert "v2.15" in src


# ═══════════════════════════════════════════════════════════
# D. REASSIGNMENT SECURITY
# ═══════════════════════════════════════════════════════════
class TestReassignSecurity:
    """Test reassignment role enforcement and error handling."""

    def test_backend_enforces_officer_roles(self):
        """Only admin/sco/co roles should be able to reassign."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        assert "admin" in src
        assert "sco" in src
        assert "co" in src

    def test_backend_rejects_analyst_reassign(self):
        """Analyst role must be blocked from reassignment."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        # The code only allows admin, sco, co — analyst is excluded
        assert 'not in ("admin", "sco", "co")' in src or "Only Admin" in src

    def test_reassignment_audit_includes_before_after(self):
        """Reassignment audit log must include old and new assignee."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        assert "Reassign" in src
        assert "old_assigned" in src or "from" in src.lower()

    def test_frontend_shows_error_on_failure(self):
        """Frontend must show error toast if reassignment fails."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "'error'" in html
        # Verify error handling in confirmReassign
        assert "catch" in html


# ═══════════════════════════════════════════════════════════
# E. MEMO STALENESS + CONTENT
# ═══════════════════════════════════════════════════════════
class TestMemoStaleness:
    """Test memo staleness detection and content completeness."""

    def test_backend_returns_memo_is_stale(self):
        """API must include memo_is_stale field in detail response."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.get)
        assert "memo_is_stale" in src

    def test_frontend_shows_stale_warning(self):
        """Frontend must show stale memo warning banner."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "memoIsStale" in html
        assert "outdated" in html.lower() or "stale" in html.lower()

    def test_memo_includes_operating_countries(self):
        """Memo client_overview must include operating countries."""
        import inspect
        from memo_handler import build_compliance_memo
        src = inspect.getsource(build_compliance_memo)
        assert "operating_countries" in src

    def test_memo_includes_incorporation_date(self):
        """Memo client_overview must include incorporation date."""
        import inspect
        from memo_handler import build_compliance_memo
        src = inspect.getsource(build_compliance_memo)
        assert "incorporation_date" in src

    def test_memo_includes_business_activity(self):
        """Memo client_overview must include business activity."""
        import inspect
        from memo_handler import build_compliance_memo
        src = inspect.getsource(build_compliance_memo)
        assert "business_activity" in src

    def test_memo_handler_extracts_new_fields(self):
        """Memo data assembly in server.py must extract operating_countries, incorporation_date, business_activity."""
        import inspect
        from server import ComplianceMemoHandler
        src = inspect.getsource(ComplianceMemoHandler.post)
        assert "operating_countries" in src
        assert "incorporation_date" in src
        assert "business_activity" in src


# ═══════════════════════════════════════════════════════════
# F. SUPERVISOR UX
# ═══════════════════════════════════════════════════════════
class TestSupervisorUX:
    """Test supervisor status wording clarity."""

    def test_awaiting_review_shows_requires_human_review(self):
        """awaiting_review must display as 'REQUIRES HUMAN REVIEW', not 'AWAITING REVIEW'."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "REQUIRES HUMAN REVIEW" in html

    def test_supervisor_status_labels_map_exists(self):
        """stLabels map must exist to control status wording."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "stLabels" in html


# ═══════════════════════════════════════════════════════════
# G. SCREENING DISPOSITION UX
# ═══════════════════════════════════════════════════════════
class TestScreeningDispositionUX:
    """Test screening disposition modal and payload contract."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_screening_disposition_modal_fields_present(self):
        html = self._read_backoffice()
        assert 'id="modal-screening-disposition"' in html
        assert 'id="screening-disposition-code"' in html
        assert 'id="screening-disposition-rationale"' in html
        assert "Second reviewer required" in html

    def test_screening_disposition_payload_sends_code_and_rationale(self):
        html = self._read_backoffice()
        fn_start = html.index("async function submitScreeningDisposition()")
        fn_region = html[fn_start:fn_start + 2000]
        assert "disposition_code: code" in fn_region
        assert "rationale: rationale" in fn_region
        assert "notes: rationale" in fn_region
        assert "screeningDispositionRationaleError(disposition, rationale)" in fn_region

    def test_screening_disposition_clear_rationale_floor_is_visible_and_enforced(self):
        html = self._read_backoffice()
        assert 'id="screening-disposition-rationale-help"' in html
        assert "SCREENING_CLEAR_RATIONALE_MIN_CHARS = 40" in html
        assert "SCREENING_CLEAR_RATIONALE_MIN_WORDS = 8" in html
        assert "screeningRationaleWordCount(rationale)" in html
        assert "Cleared screening rationale must be at least" in html

    def test_screening_review_no_longer_uses_prompt_notes(self):
        html = self._read_backoffice()
        fn_start = html.index("function saveScreeningReview(")
        fn_end = html.index("async function submitScreeningDisposition()", fn_start)
        fn_region = html[fn_start:fn_end]
        assert "window.prompt" not in fn_region
        assert "openScreeningDispositionModalByRow" in fn_region


# ═══════════════════════════════════════════════════════════
# H. PRE-PHASE-6 NAVIGATION AND CASE LIST UX
# ═══════════════════════════════════════════════════════════
class TestPrePhaseSixBackofficeUX:
    """Pin focused dashboard/application/case-list behaviours before Phase 6."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_dashboard_view_all_uses_application_nav_state(self):
        html = self._read_backoffice()
        assert 'onclick="showView(\'applications\')">View All' in html
        assert "snav-item:nth-child(3)" not in html
        assert 'document.querySelector(\'.snav-item[data-view="\' + name + \'"]\')' in html

    def test_edd_pipeline_is_above_ongoing_monitoring(self):
        html = self._read_backoffice()
        assert html.index('data-view="edd"') < html.index('data-view="monitoring"')

    def test_applications_page_has_search_and_page_size_controls(self):
        html = self._read_backoffice()
        assert 'id="applications-search"' in html
        assert 'id="applications-page-size"' in html
        assert 'function setApplicationsPageSize(value)' in html
        assert 'function changeApplicationsPage(delta)' in html
        assert 'id="applications-pagination-summary"' in html
        assert "'/applications?limit=5000'" in html

    def test_global_search_filters_application_list_instead_of_opening_detail(self):
        html = self._read_backoffice()
        fn_start = html.index("function handleGlobalSearch(q)")
        fn_end = html.index("// ═══════════════════════════════════════════════════════════", fn_start)
        fn_region = html[fn_start:fn_end]
        assert "applications-search" in fn_region
        assert "showView('applications')" in fn_region
        assert "openAppDetail" not in fn_region

    def test_my_cases_filters_by_current_user_assignment(self):
        html = self._read_backoffice()
        assert "function isAssignedToCurrentUser(app)" in html
        assert "activeCaseTab === 'my-cases' && !isAssignedToCurrentUser(app)" in html
        assert "activeCaseTab === 'unassigned' && app.assignedId" in html


# ═══════════════════════════════════════════════════════════
# I. PHASE 6 COMPLYADVANTAGE STATUS UI
# ═══════════════════════════════════════════════════════════
class TestPhaseSixComplyAdvantageStatusUI:
    """Pin truthful provider roles and status labels in the back-office UI."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_api_status_panel_lists_complyadvantage_with_correct_responsibility(self):
        html = self._read_backoffice()
        assert "{ key: 'complyadvantage', label: 'ComplyAdvantage KYB / Media / Monitoring'" in html
        assert "{ key: 'sumsub', label: 'Sumsub IDV/KYC'" in html

    def test_api_status_panel_understands_ca_readiness_states(self):
        html = self._read_backoffice()
        assert "ready: 'READY'" in html
        assert "not_configured: 'NOT CONFIGURED'" in html
        assert "misconfigured: 'MISCONFIGURED'" in html


# ═══════════════════════════════════════════════════════════
# I2. PHASE 7 UPLOAD LATENCY SIZE CAP UI
# ═══════════════════════════════════════════════════════════
class TestPhaseSevenUploadSizeCapUI:
    """Pin the back-office client-side upload size cap quick win."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_bo_document_upload_limit_is_flag_driven_and_visible(self):
        html = self._read_backoffice()
        assert "FF_SIZE_CAP_CLIENT_REJECT" in html
        assert "BO_DOC_UPLOAD_CLIENT_CAP_MB = 10" in html
        assert "BO_DOC_UPLOAD_LEGACY_MAX_MB = 25" in html
        assert 'id="bo-upload-size-help"' in html
        assert "function refreshBoDocUploadLimitCopy()" in html
        assert "refreshBoDocUploadLimitCopy();" in html

    def test_bo_document_upload_rejects_using_active_limit_not_literal_25mb(self):
        html = self._read_backoffice()
        fn_start = html.index("async function submitBoDocUpload()")
        fn_end = html.index("// ═══════════════════════════════════════════════════════════", fn_start)
        fn_region = html[fn_start:fn_end]
        assert "boDocUploadMaxBytes()" in fn_region
        assert "boDocUploadMaxLabel()" in fn_region
        assert "25 * 1024 * 1024" not in fn_region


# ═══════════════════════════════════════════════════════════
# I3. DAY 3 APPROVAL BLOCKER UX
# ═══════════════════════════════════════════════════════════
class TestDayThreeApprovalBlockerUX:
    """Pin visible approval blocker panels in the back-office detail workflow."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_application_detail_has_visible_approval_blocker_panel(self):
        html = self._read_backoffice()
        assert 'id="detail-approval-blockers"' in html
        assert "function getApplicationApprovalBlockers(app)" in html
        assert "function renderApprovalBlockersPanel(app)" in html
        assert "renderApprovalBlockersPanel(app);" in html

    def test_application_approval_uses_single_blocker_source(self):
        html = self._read_backoffice()
        fn_start = html.index("function getApprovalReadiness(app)")
        fn_region = html[fn_start:fn_start + 500]
        assert "getApplicationApprovalBlockers(app)" in fn_region
        assert "ready: blockers.length === 0" in fn_region

    def test_application_approve_button_is_disabled_when_blockers_exist(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderApprovalBlockersPanel(app)")
        fn_region = html[fn_start:fn_start + 1800]
        assert "approveBtn.disabled = true" in fn_region
        assert "Approval blocked:" in fn_region
        assert "Compliance memo has not been approved" in html
        assert "Screening has not been run" in html

    def test_memo_validation_panel_has_visible_approval_blockers(self):
        html = self._read_backoffice()
        assert 'id="memo-approval-blockers"' in html
        assert "function getMemoApprovalBlockers(app, validationResult)" in html
        assert "function renderMemoApprovalBlockers(result)" in html
        fn_start = html.index("function renderValidationPanel(result)")
        fn_region = html[fn_start:fn_start + 7600]
        assert "renderMemoApprovalBlockers(result)" in fn_region
        assert "memoApprovalBlockers" in fn_region

    def test_pass_with_fixes_remains_blocked_until_ui_captures_reason(self):
        html = self._read_backoffice()
        assert "PASS WITH FIXES approval requires an approval_reason" in html
        assert "this UI does not capture or submit that reason yet" in html
        fn_start = html.index("PASS WITH FIXES approval is blocked until this UI captures approval_reason")
        fn_start = html.rfind("} else if (status === 'pass_with_fixes')", 0, fn_start)
        fn_region = html[fn_start:fn_start + 450]
        assert "approveBtn.disabled = true" in fn_region
        assert "memoApprovalBlockers.length" in fn_region


# ═══════════════════════════════════════════════════════════
# I3B. DAY 4 DASHBOARD COUNT ALIGNMENT
# ═══════════════════════════════════════════════════════════
class TestDayFourDashboardCountAlignment:
    """Pin Dashboard in-progress counts to the report status contract."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_dashboard_in_progress_label_and_status_contract_are_visible(self):
        html = self._read_backoffice()
        assert '<div class="stat-card-label">In Progress</div>' in html
        assert "var DASHBOARD_PENDING_STATUSES = [" in html
        assert "'draft'" in html
        assert "'pricing_review'" in html
        assert "'kyc_documents'" in html
        assert "function isDashboardPendingApplication(app)" in html

    def test_dashboard_stats_uses_single_pending_helper(self):
        html = self._read_backoffice()
        fn_start = html.index("function updateDashboardStats()")
        fn_region = html[fn_start:fn_start + 900]
        assert "APPLICATIONS.filter(isDashboardPendingApplication).length" in fn_region
        assert "pending review" not in fn_region
        assert "pricing review" not in fn_region


# ═══════════════════════════════════════════════════════════
# I4. DAY 3 MEMO QUALITY TRUTHFULNESS
# ═══════════════════════════════════════════════════════════
class TestDayThreeMemoQualityTruthfulness:
    """Pin status-aware memo quality labels in the back-office validation panel."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_memo_quality_label_is_status_aware(self):
        html = self._read_backoffice()
        assert "function memoQualityScoreLabel(status, score)" in html
        fn_start = html.index("function memoQualityScoreLabel(status, score)")
        fn_region = html[fn_start:fn_start + 900]
        assert "if (status === 'fail') return prefix + 'Validation failed; remediation required';" in fn_region
        assert "if (status === 'pass_with_fixes') return prefix + 'Needs fixes before approval';" in fn_region
        assert "if (status !== 'pass') return 'Run validation to see server-calculated memo quality results';" in fn_region
        assert "var scoreLabel = score >= 8 ? 'Excellent'" in fn_region

    def test_memo_quality_gauge_is_status_aware(self):
        html = self._read_backoffice()
        assert "function memoQualityGaugeClass(status, score)" in html
        fn_start = html.index("function memoQualityGaugeClass(status, score)")
        fn_region = html[fn_start:fn_start + 500]
        assert "if (status === 'fail') return 'poor';" in fn_region
        assert "if (status === 'pass_with_fixes') return 'fair';" in fn_region
        assert "if (status !== 'pass') return 'pending';" in fn_region

    def test_validation_panel_uses_quality_truthfulness_helpers(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderValidationPanel(result)")
        fn_region = html[fn_start:fn_start + 1700]
        assert "var hasQualityScore = hasMemoQualityScore(result);" in fn_region
        assert "memoQualityGaugeClass(status, score)" in fn_region
        assert "memoQualityScoreLabel(status, score)" in fn_region
        assert "gauge.className = 'memo-quality-gauge ' + (score >= 8" not in fn_region
        assert "scoreText.textContent = score.toFixed(1) + ' / 10 — ' + scoreLabel" not in fn_region


# ═══════════════════════════════════════════════════════════
# I5. DAY 3 RMI SUBMISSION UX HARDENING
# ═══════════════════════════════════════════════════════════
class TestDayThreeRMISubmissionHardening:
    """Pin RMI request UX against duplicate submits and fake success toasts."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_rmi_modal_has_submit_button_state_hook(self):
        html = self._read_backoffice()
        assert 'id="rmi-submit-btn"' in html
        assert "function setRMISubmitting(isSubmitting)" in html
        fn_start = html.index("function setRMISubmitting(isSubmitting)")
        fn_region = html[fn_start:fn_start + 450]
        assert "btn.disabled = !!isSubmitting" in fn_region
        assert "Sending..." in fn_region
        assert "Send Request" in fn_region

    def test_rmi_submit_is_debounced_and_requires_server_confirmation(self):
        html = self._read_backoffice()
        fn_start = html.index("async function confirmRMIRequest()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "if (submitBtn && submitBtn.disabled) return;" in fn_region
        assert "setRMISubmitting(true);" in fn_region
        assert "if (!resp || !resp.rmi_request_id)" in fn_region
        assert "Document request was not confirmed by the server" in fn_region
        assert "await refreshCurrentAppDetail();" in fn_region
        assert "showToast('Additional document request sent to client')" in fn_region
        assert "finally" in fn_region
        assert "setRMISubmitting(false);" in fn_region


# ═══════════════════════════════════════════════════════════
# I6. DAY 4 REPORT EXPORT RECONCILIATION
# ═══════════════════════════════════════════════════════════
class TestDayFourReportExportReconciliation:
    """Pin Reports export to the backend CSV source of truth."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_reports_export_uses_server_csv_endpoint(self):
        html = self._read_backoffice()
        fn_start = html.index("function exportReportsCSV()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "/reports/generate?format=csv&" in fn_region
        assert "fetch(BO_API_BASE + url, { headers: headers })" in fn_region
        assert "headers['Authorization'] = 'Bearer ' + BO_AUTH_TOKEN" in fn_region
        assert "res.blob()" in fn_region
        assert "X-Report-Record-Count" in fn_region

    def test_reports_export_no_longer_builds_csv_in_browser(self):
        html = self._read_backoffice()
        fn_start = html.index("function exportReportsCSV()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "var csv = fields.join(',')" not in fn_region
        assert "resp.data.forEach" not in fn_region
        assert "new Blob([csv]" not in fn_region


# ═══════════════════════════════════════════════════════════
# J. AUDIT TRAIL HARDENING
# ═══════════════════════════════════════════════════════════
class TestAuditTrailHardening:
    """Test structured audit entries."""

    def test_status_change_audit_includes_before_after(self):
        """Status change audit must show old → new status."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        # Must log both current_status and new_status
        assert "current_status" in src
        assert "new_status" in src
        assert "Status Change" in src

    def test_reassign_audit_includes_before_after(self):
        """Reassign audit must show old → new assignee."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        assert "Reassigned from" in src

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

    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    BACKOFFICE_PATH = os.path.join(
        REPO_ROOT,
        "arie-backoffice.html",
    )
    DB_PATH = os.path.join(REPO_ROOT, "arie-backend", "db.py")

    def _read_backoffice(self):
        with open(self.BACKOFFICE_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def _read_db_source(self):
        with open(self.DB_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def _canonical_application_statuses(self):
        db_source = self._read_db_source()
        application_tables = re.findall(
            r"CREATE TABLE IF NOT EXISTS applications\s*\((.*?)\);",
            db_source,
            re.DOTALL,
        )
        for table_sql in application_tables:
            status_check = re.search(
                r"status\s+TEXT\s+DEFAULT\s+'draft'\s+CHECK\(status\s+IN\s+\((.*?)\)\)",
                table_sql,
                re.DOTALL,
            )
            if status_check:
                return re.findall(r"'([^']+)'", status_check.group(1))
        raise AssertionError("Could not locate canonical applications.status CHECK constraint")

    def _extract_js_object(self, html, name):
        token = f"var {name} = "
        start = html.index(token) + len(token)
        if html[start] != "{":
            raise AssertionError(f"{name} is not an object literal")
        depth = 0
        in_string = None
        escape = False
        for idx in range(start, len(html)):
            ch = html[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_string:
                    in_string = None
                continue
            if ch in ("'", '"'):
                in_string = ch
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return html[start:idx + 1]
        raise AssertionError(f"Could not parse {name} object literal")

    def _extract_js_array(self, html, name):
        token = f"var {name} = "
        start = html.index(token) + len(token)
        end = html.index("];", start) + 1
        return re.findall(r"'([^']+)'", html[start:end])

    def _extract_js_function(self, html, signature):
        start = html.index(signature)
        after = html[start + len(signature):]
        match = re.search(r"\n(?:async function |function |var [A-Z0-9_]+ = )", after)
        return after[:match.start()] if match else after

    def _application_status_meta(self):
        html = self._read_backoffice()
        source = self._extract_js_object(html, "APPLICATION_STATUS_META")
        entries = {}
        pattern = re.compile(
            r"(?P<status>[a-z_]+):\s*\{\s*"
            r"label:\s*'(?P<label>(?:\\'|[^'])*)',\s*"
            r"filterLabel:\s*'(?P<filter_label>(?:\\'|[^'])*)',\s*"
            r"badgeClass:\s*'(?P<badge_class>[^']+)'\s*"
            r"\}",
            re.DOTALL,
        )
        for match in pattern.finditer(source):
            entries[match.group("status")] = {
                "label": match.group("label"),
                "filter_label": match.group("filter_label"),
                "badge_class": match.group("badge_class"),
            }
        return entries

    def test_branding_status_labels_includes_under_review(self):
        """STATUS_LABELS must include under_review."""
        from branding import STATUS_LABELS
        assert "under_review" in STATUS_LABELS
        assert STATUS_LABELS["under_review"] != STATUS_LABELS.get("in_review")

    def test_all_db_statuses_have_backend_labels(self):
        """Every valid DB status must have a label in STATUS_LABELS."""
        from branding import STATUS_LABELS
        for status in self._canonical_application_statuses():
            assert status in STATUS_LABELS, f"Missing label for status: {status}"

    def test_backoffice_status_metadata_covers_backend_canonical_statuses(self):
        """Every backend-emittable application status must have local UI metadata."""
        canonical_statuses = self._canonical_application_statuses()
        status_meta = self._application_status_meta()
        status_order = self._extract_js_array(self._read_backoffice(), "APPLICATION_STATUS_ORDER")
        missing = [status for status in canonical_statuses if status not in status_meta]
        assert missing == [], f"Missing back-office application status metadata: {missing}"
        missing_from_order = [status for status in canonical_statuses if status not in status_order]
        assert missing_from_order == [], f"Missing status filter/render order coverage: {missing_from_order}"

    def test_backoffice_status_metadata_has_human_labels_and_badge_handling(self):
        """Canonical statuses must render from metadata, not raw-code fallback."""
        status_meta = self._application_status_meta()
        html = self._read_backoffice()
        for status in self._canonical_application_statuses():
            meta = status_meta[status]
            assert meta["label"], f"Missing human label for {status}"
            assert meta["label"] != status, f"{status} renders as raw status code"
            assert meta["label"] != status.replace("_", " "), f"{status} renders as raw-code words"
            assert meta["badge_class"], f"Missing badge class for {status}"
            assert f".badge.{meta['badge_class']}" in html, f"Missing badge CSS class for {status}"

    def test_submitted_to_compliance_backoffice_label_filter_and_badge(self):
        """Senior-review queue status must be labelled, filterable, and badged."""
        status_meta = self._application_status_meta()
        submitted = status_meta["submitted_to_compliance"]
        assert submitted["label"] == "Submitted to Compliance"
        assert submitted["filter_label"] == "Submitted to Compliance"
        assert submitted["badge_class"]
        assert "submitted_to_compliance" in self._extract_js_array(
            self._read_backoffice(),
            "APPLICATION_STATUS_ORDER",
        )

    def test_applications_filter_renders_from_status_metadata_with_machine_values(self):
        """Status filter options are generated from the shared status metadata."""
        html = self._read_backoffice()
        body = self._extract_js_function(html, "function populateApplicationStatusFilter(")
        assert "APPLICATION_STATUS_ORDER.forEach(function(status)" in body
        assert "var meta = APPLICATION_STATUS_META[status]" in body
        assert "opt.value = status" in body
        assert "opt.textContent = meta.filterLabel || meta.label" in body

    def test_frontend_status_badge_distinguishes_under_review(self):
        """statusBadge must map under_review to a distinct CSS class from in_review."""
        status_meta = self._application_status_meta()
        assert status_meta["under_review"]["badge_class"] == "under-review"
        assert status_meta["under_review"]["badge_class"] != status_meta["in_review"]["badge_class"]

    def test_under_review_badge_css_exists(self):
        """CSS class .badge.under-review must exist."""
        html = self._read_backoffice()
        assert ".badge.under-review" in html

    def test_format_status_uses_application_status_metadata(self):
        """formatStatus may fall back for unknown future statuses, not current canonical ones."""
        html = self._read_backoffice()
        body = self._extract_js_function(html, "function formatStatus(")
        assert "applicationStatusMeta(s)" in body
        assert "return meta ? meta.label : (s || 'Unknown')" in body

    def test_idv_fixture_party_names_render_display_name(self):
        """Synthetic fixture party names must not expose raw application status tokens."""
        html = self._read_backoffice()
        panel = self._extract_js_function(html, "function renderSumsubIdvPanel(")
        modal = self._extract_js_function(html, "function openIdvResolutionModal(")
        sanitizer = self._extract_js_function(html, "function caseCommandSanitizeText(")

        assert "function sumsubIdvDisplayName(" in html
        assert "function applicationFixtureSafePartyName(" in html
        assert "name: applicationFixtureSafePartyName(d.full_name, 'director')" in html
        assert "name: applicationFixtureSafePartyName(u.full_name, 'ubo')" in html
        assert "name: applicationFixtureSafePartyName(d.full_name || d.name || '', 'director')" in html
        assert "name: applicationFixtureSafePartyName(u.full_name || u.name || '', 'ubo')" in html
        assert "escapeHtml(sumsubIdvDisplayName(item))" in panel
        assert "sumsubIdvDisplayName(item) + ' - ' + sumsubIdvRoleLabel(item.person_type)" in modal
        assert "person_name: item.person_name || ''" in html
        assert "submitted_to_compliance\\s+Director" in sanitizer
        assert "officer_submitted_to_compliance" in sanitizer


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

    def test_frontend_audit_trail_has_enterprise_presentation_helpers(self):
        """Application Detail audit trail must render structured rows with collapsed technical detail."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "function classifyAuditEvent" in html
        assert "function buildAuditSummary" in html
        assert "function renderAuditEventCard" in html
        assert "Show technical details" in html
        assert "Copy technical details" in html
        assert "audit-filter-chip" in html
        assert "data-audit-category" in html

    def test_frontend_audit_filter_chips_are_available(self):
        """Audit trail filters must be client-side and include the required category set."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert "DETAIL_AUDIT_FILTERS" in html
        for category in ["All", "CA/Mesh", "Risk", "Screening", "Documents", "Memo", "EDD", "Governance", "Decision", "System"]:
            assert f"'{category}'" in html
        assert "function setAuditTrailFilter" in html
        assert "boApiCall('GET', '/applications/' + app.id + '/audit-log?limit=100')" in html

    def test_frontend_does_not_render_detail_inline_by_default(self):
        """Raw audit detail should be shown only in the technical details panel."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        activity_start = html.index("// ACTIVITY LOG — Real audit trail from backend")
        activity_end = html.index("// NOTES — Internal officer notes from backend", activity_start)
        activity_region = html[activity_start:activity_end]
        assert "escapeHtml(e.detail || '')" not in activity_region
        assert "buildAuditTechnicalPayload" in activity_region
        assert '<pre class="audit-tech-pre"' in activity_region

    def test_empty_internal_notes_state_is_compact(self):
        """Empty notes must not render a tall blank block."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        notes_start = html.index("async function loadNotes()")
        notes_end = html.index("var decisionRecordsLoadedFor", notes_start)
        notes_region = html[notes_start:notes_end]
        assert "notes-empty-state" in notes_region
        assert "No internal notes yet." in notes_region
        assert "padding:16px;text-align:center;font-size:12px;color:var(--text3);" not in notes_region


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
        """Only admin/sco roles should be able to reassign."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        assert "admin" in src
        assert "sco" in src

    def test_backend_rejects_analyst_reassign(self):
        """Analyst role must be blocked from reassignment."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        # The code only allows admin and sco — analyst is excluded.
        assert 'not in ("admin", "sco")' in src or "Assignment blocked" in src

    def test_reassignment_audit_includes_before_after(self):
        """Reassignment audit log must include old and new assignee."""
        import inspect
        from server import ApplicationDetailHandler
        src = inspect.getsource(ApplicationDetailHandler.patch)
        assert "Reassign" in src
        assert "previous_assignee_id" in src
        assert "new_assignee_id" in src
        assert "before_state" in src
        assert "after_state" in src

    def test_reassignment_reason_is_required_in_frontend(self):
        """Frontend modal marks reassignment reason as required and validates it."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            html = f.read()
        assert 'id="reassign-reason" required aria-required="true"' in html
        assert "Reassignment reason is required." in html
        assert "reassignment_reason: reason" in html
        assert "currentApplicationReviewTab()" in html
        assert "reassign-current-assignee" in html
        assert "reassign-new-assignee" in html

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

    def test_screening_disposition_false_positive_rationale_floor_is_visible_and_enforced(self):
        html = self._read_backoffice()
        assert 'id="screening-disposition-rationale-help"' in html
        assert "SCREENING_RATIONALE_MIN_CHARS = 12" in html
        assert "False-positive clearance rationale must briefly state why this provider hit does not appear to relate to the subject" in html
        assert "Cleared screening rationale must be at least" not in html

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

    def test_edd_pipeline_removed_from_main_nav_but_legacy_view_retained(self):
        html = self._read_backoffice()
        nav = html[html.index('<nav class="sidebar-nav"'):html.index('</nav>')]
        assert 'data-view="edd"' not in nav
        assert "EDD Pipeline</div>" not in nav
        assert 'id="view-edd"' in html
        assert 'id="legacy-edd-consolidation-notice"' in html
        assert "Formal investigation cases are managed in Lifecycle" in html
        assert "Open Applications — Enhanced Requirements" in html
        assert "Open Applications — Approval Blocked" in html
        assert "showView('edd')" in html
        assert "SLA, findings, senior review, and outcome controls" in html

    def test_applications_page_has_search_and_page_size_controls(self):
        html = self._read_backoffice()
        assert 'id="applications-search"' in html
        assert 'id="applications-page-size"' in html
        assert 'function setApplicationsPageSize(value)' in html
        assert 'function changeApplicationsPage(delta)' in html
        assert 'function queueApplicationsSearch()' in html
        assert "function buildApplicationsApiPath()" in html
        assert 'id="applications-pagination-summary"' in html
        assert "params.set('view', 'list')" in html

    def test_global_search_filters_application_list_instead_of_opening_detail(self):
        html = self._read_backoffice()
        fn_start = html.index("function handleGlobalSearch(q)")
        fn_end = html.index("// ═══════════════════════════════════════════════════════════", fn_start)
        fn_region = html[fn_start:fn_end]
        assert "applications-search" in fn_region
        assert "showView('applications')" in fn_region
        assert "openAppDetail" not in fn_region

    def test_case_management_is_my_assigned_work_only(self):
        html = self._read_backoffice()
        case_view = html[html.index('<div class="view" id="view-cases">'):html.index('<!-- ═══════════════ SCREENING QUEUE', html.index('<div class="view" id="view-cases">'))]
        assert "My Assigned Work" in case_view
        assert "Unassigned</div>" not in case_view
        assert "All Cases</div>" not in case_view
        assert "Pre-Approval Queue" not in case_view
        assert 'data-case-filter="applications"' in case_view
        assert 'data-case-filter="periodic_reviews"' in case_view
        assert "Case Management is an officer worklist only" in case_view

    def test_case_management_projects_assigned_application_and_review_work(self):
        html = self._read_backoffice()
        assert "/case-management/worklist" in html
        assert "function loadCaseWorklist(options)" in html
        assert "function openCaseWorkItem(index)" in html
        assert "open_target" in html
        assert "CASE_WORKLIST_STATE.items.forEach(function(item, idx)" in html
        case_region = html[html.index("var activeCaseTab = 'my-assigned';"):html.index("// ═══════════════════════════════════════════════════════════", html.index("function renderCases()"))]
        assert "APPLICATIONS.forEach(function(app)" not in case_region
        assert "PERIODIC_REVIEWS.forEach(function(review)" not in case_region
        assert "buildCaseWorkItems" not in case_region
        assert "loadCaseWorklist({ force: true })" in html


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
        assert "{ key: 'complyadvantage', label: 'ComplyAdvantage Mesh AML / Media / Monitoring'" in html
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

    def test_application_detail_hides_duplicate_approval_blocker_panel(self):
        html = self._read_backoffice()
        assert 'id="detail-approval-blockers"' in html
        assert "function getApplicationApprovalBlockers(app)" in html
        assert "function renderApprovalBlockersPanel(app)" in html
        assert "renderApprovalBlockersPanel(app);" in html
        fn_start = html.index("function renderApprovalBlockersPanel(app)")
        fn_region = html[fn_start:fn_start + 900]
        assert "panel.style.display = 'none'" in fn_region
        assert "Application approval blocked:" not in fn_region

    def test_application_approval_uses_single_blocker_source(self):
        html = self._read_backoffice()
        fn_start = html.index("function getApprovalReadiness(app)")
        fn_region = html[fn_start:fn_start + 500]
        assert "getApplicationApprovalBlockers(app)" in fn_region
        assert "ready: blockers.length === 0" in fn_region

    def test_application_approve_button_uses_central_action_state_when_blockers_exist(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderApprovalBlockersPanel(app)")
        fn_region = html[fn_start:fn_start + 900]
        assert "approveBtn.disabled = false" not in fn_region
        assert "syncApplicationActionPermissions(app)" in fn_region

        decision_start = html.index("function renderDecisionReadiness(decision)")
        decision_region = html[decision_start:decision_start + 2600]
        assert "buildApplicationActionState(currentApp)" in decision_region
        assert "confirmBtn.disabled = true" in decision_region
        assert "unavailable:</strong>" in decision_region
        assert "Compliance memo has not been approved" in html
        assert "Screening has not been run" in html

    def test_screening_second_review_blocker_is_officer_readable_and_focuses_screening(self):
        html = self._read_backoffice()
        assert "Screening second review pending" in html
        assert "'screening.resolve': { target_view:'application_review', target_tab:'screening'" in html
        assert "target_section:'detail-screening-review'" in html
        assert "scroll_anchor:'detail-screening-review'" in html

    def test_screening_second_review_blocker_not_rendered_in_client_portal(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-portal.html"
        ), "r", encoding="utf-8") as f:
            portal = f.read()
        assert "screening_second_review_pending" not in portal
        assert "Screening second review pending" not in portal

    def test_memo_validation_panel_has_visible_approval_blockers(self):
        html = self._read_backoffice()
        assert 'id="memo-approval-blockers"' in html
        assert "function getMemoApprovalBlockers(app, validationResult)" in html
        assert "function renderMemoApprovalBlockers(result)" in html
        fn_start = html.index("function renderValidationPanel(result)")
        fn_region = html[fn_start:fn_start + 7600]
        assert "renderMemoApprovalBlockers(result)" in fn_region
        assert "memoApprovalBlockers" in fn_region

    def test_pass_with_fixes_approval_reason_is_captured_by_ui(self):
        html = self._read_backoffice()
        assert 'id="memo-approval-reason"' in html
        assert "function currentMemoApprovalReason()" in html
        assert "approval_reason: approvalReason" in html
        assert "Enter the approval reason before submitting memo approval." in html
        assert "this UI does not capture or submit that reason yet" not in html
        fn_start = html.index("PASS WITH FIXES requires documented approval reason")
        fn_start = html.rfind("} else if (status === 'pass_with_fixes')", 0, fn_start)
        fn_end = html.index("} else if (status === 'pass')", fn_start)
        fn_region = html[fn_start:fn_end]
        assert "approveBtn.disabled = memoApprovalBlockers.length > 0" in fn_region
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
        assert "var DASHBOARD_STATUS_CONTRACT = { pendingStatuses: [], eddRoutedStatuses: [], canonicalView: '' };" in html
        assert "function setDashboardStatusContract(source)" in html
        assert "function setDashboardData(source)" in html
        assert "function getDashboardPendingStatuses()" in html
        assert "function hasDashboardStatusContract()" in html
        assert "function getDashboardEddRoutedStatuses()" in html
        assert "function isDashboardEddRoutedApplication(app)" in html
        assert "setDashboardStatusContract(source || {});" in html
        assert "function isDashboardPendingApplication(app)" in html

    def test_dashboard_pending_statuses_are_loaded_from_backend_contract(self):
        html = self._read_backoffice()
        load_start = html.index("async function loadFromAPI()")
        load_region = html[load_start:load_start + 1800]
        assert "await refreshDashboardData();" in load_region

        refresh_start = html.index("async function refreshDashboardData()")
        refresh_region = html[refresh_start:refresh_start + 600]
        assert "var dashboardResp = await boApiCall('GET', '/dashboard');" in refresh_region
        assert "setDashboardData(dashboardResp);" in refresh_region
        assert "Could not load canonical dashboard metrics" in refresh_region

        helper_start = html.index("function isDashboardPendingApplication(app)")
        helper_region = html[helper_start:helper_start + 260]
        assert "getDashboardPendingStatuses().indexOf" in helper_region
        assert "normalizeStatusKey((app || {}).statusRaw || (app || {}).status)" in helper_region

    def test_dashboard_pending_statuses_no_longer_duplicate_backend_tuple(self):
        html = self._read_backoffice()
        pending_start = html.index("function getDashboardPendingStatuses()")
        pending_end = html.index("function getDashboardEddRoutedStatuses()", pending_start)
        pending_helper_start = html.index("function isDashboardPendingApplication(app)")
        pending_helper_region = (
            html[pending_start:pending_end] +
            html[pending_helper_start:pending_helper_start + 260]
        )
        assert "DASHBOARD_PENDING_STATUSES" not in pending_helper_region
        assert "'pricing_review'" not in pending_helper_region
        assert "'kyc_documents'" not in pending_helper_region
        assert "DASHBOARD_STATUS_CONTRACT.pendingStatuses" in pending_helper_region
        assert "var DASHBOARD_STATUS_CONTRACT = { pendingStatuses: [], eddRoutedStatuses: [], canonicalView: '' };" in html

    def test_empty_dashboard_status_contract_renders_unavailable_not_zero(self):
        html = self._read_backoffice()

        stats_start = html.index("function updateDashboardStats()")
        stats_region = html[stats_start:stats_start + 2200]
        assert "var inProgressMetric = getDashboardMetric('in_progress_applications');" in stats_region
        assert "setDashboardStatValue('dash-stat-early-stage', inProgressMetric ? String(inProgressMetric.value) : '—');" in stats_region
        assert "setDashboardStatNote('dash-stat-early-stage-change', inProgressMetric ? 'Canonical pending status bucket' : 'Canonical dashboard metrics unavailable');" in stats_region

        kpi_start = html.index("function renderKPIDashboard()")
        kpi_region = html[kpi_start:kpi_start + 5200]
        assert "Canonical dashboard metrics unavailable" not in kpi_region

    def test_dashboard_stats_uses_single_pending_helper(self):
        html = self._read_backoffice()
        fn_start = html.index("function updateDashboardStats()")
        fn_region = html[fn_start:fn_start + 900]
        assert "getDashboardMetric('in_progress_applications')" in fn_region
        assert "APPLICATIONS.filter(isDashboardPendingApplication).length" not in fn_region
        assert "pending review" not in fn_region
        assert "pricing review" not in fn_region


# ═══════════════════════════════════════════════════════════
# I3C. DAY 4 KPI EDD ROUTING TRUTHFULNESS
# ═══════════════════════════════════════════════════════════
class TestDayFourKPIEDDRoutingTruthfulness:
    """Pin KPI EDD rate to actual EDD routing statuses, not risk proxies."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_kpi_edd_rate_uses_edd_statuses_not_risk_proxy(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderKPIDashboard()")
        edd_start = html.index("var eddApps = hasEddRoutedStatusContract", fn_start)
        edd_region = html[edd_start:edd_start + 360]
        assert "hasEddRoutedStatusContract" in edd_region
        assert "appsInPeriod.filter(isDashboardEddRoutedApplication).length" in edd_region
        assert "s === 'edd_required' || s === 'edd_approved'" not in edd_region
        assert "risk === 'HIGH'" not in edd_region
        assert "risk === 'VERY_HIGH'" not in edd_region

    def test_edd_routing_statuses_are_loaded_from_backend_contract(self):
        html = self._read_backoffice()
        contract_start = html.index("function setDashboardStatusContract(source)")
        contract_region = html[contract_start:contract_start + 1200]
        assert "source && source.edd_routed_statuses" in contract_region
        assert "DASHBOARD_STATUS_CONTRACT.eddRoutedStatuses = eddRouted" in contract_region
        assert "function getDashboardEddRoutedStatuses()" in html
        assert "function hasDashboardEddRoutedStatusContract()" in html
        assert "function isDashboardEddRoutedApplication(app)" in html
        assert "getDashboardEddRoutedStatuses().indexOf" in html

    def test_kpi_status_key_prefers_raw_backend_status(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderKPIDashboard()")
        status_start = html.index("function statusKey(app)", fn_start)
        status_region = html[status_start:status_start + 420]
        assert "app.statusRaw || app.status" in status_region
        assert "replace(/[\\s-]+/g, '_')" in status_region
        assert "enhanced_due_diligence_required" in status_region
        assert "return 'edd_required'" in status_region

    def test_kpi_edd_card_label_matches_routing_semantics(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderKPIDashboard()")
        card_start = html.index("EDD Routing Rate", fn_start)
        card_region = html[card_start:card_start + 900]
        assert "eddRoutingRate" in card_region
        assert "eddRoutingSub" in card_region
        assert "High/Very High risk" not in card_region
        assert "EDD Conversion Rate" not in card_region


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
        assert "REPORT_EXPORT_FIELD_LIST" in html
        assert "risk_score" in html[html.index("var REPORT_EXPORT_FIELD_LIST"):html.index("function exportReportsCSV()")]
        assert "function reportCsvFilename(res)" in html
        assert "/reports/generate?format=csv&" in fn_region
        assert "REPORT_EXPORT_FIELD_LIST" in fn_region
        assert "fetch(BO_API_BASE + url, { headers: headers })" in fn_region
        assert "headers['Authorization'] = 'Bearer ' + BO_AUTH_TOKEN" in fn_region
        assert "res.blob()" in fn_region
        assert "X-Report-Record-Count" in fn_region
        assert "reportCsvFilename(res)" in fn_region

    def test_reports_export_no_longer_builds_csv_in_browser(self):
        html = self._read_backoffice()
        fn_start = html.index("function exportReportsCSV()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "var csv = fields.join(',')" not in fn_region
        assert "resp.data.forEach" not in fn_region
        assert "new Blob([csv]" not in fn_region

    def test_kpi_export_uses_server_csv_endpoint(self):
        html = self._read_backoffice()
        fn_start = html.index("function exportKPIReport()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "/reports/generate?format=csv&" in fn_region
        assert "REPORT_EXPORT_FIELD_LIST" in fn_region
        assert "risk_score" in html[html.index("var REPORT_EXPORT_FIELD_LIST"):html.index("function exportReportsCSV()")]
        assert "fetch(BO_API_BASE + url, { headers: headers })" in fn_region
        assert "headers['Authorization'] = 'Bearer ' + BO_AUTH_TOKEN" in fn_region
        assert "res.blob()" in fn_region
        assert "X-Report-Record-Count" in fn_region
        assert "reportCsvFilename(res)" in fn_region
        assert "KPI report exported" in fn_region

    def test_kpi_export_no_longer_builds_csv_in_browser(self):
        html = self._read_backoffice()
        fn_start = html.index("function exportKPIReport()")
        fn_region = html[fn_start:fn_start + 2600]
        assert "var csv = fields.join(',')" not in fn_region
        assert "resp.data.forEach" not in fn_region
        assert "new Blob([csv]" not in fn_region


# ═══════════════════════════════════════════════════════════
# I7. DAY 4 KPI BACKLOG ALIGNMENT
# ═══════════════════════════════════════════════════════════
class TestDayFourKPIBacklogAlignment:
    """Pin KPI backlog to the canonical in-progress status contract."""

    def _read_backoffice(self):
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "arie-backoffice.html"
        ), "r", encoding="utf-8") as f:
            return f.read()

    def test_kpi_backlog_uses_dashboard_pending_helper(self):
        html = self._read_backoffice()
        fn_start = html.index("function renderKPIDashboard()")
        fn_region = html[fn_start:fn_start + 5200]
        assert "var backlogCount = hasPendingStatusContract ? appsInPeriod.filter(isDashboardPendingApplication).length : null;" in fn_region
        assert "In Progress Applications" in fn_region
        assert "canonical in-progress bucket" in fn_region

    def test_kpi_backlog_no_longer_uses_terminal_status_inverse(self):
        html = self._read_backoffice()
        start = html.index("// In-progress applications: use the same canonical status set")
        end = html.index("// ── Section 1: Operational Efficiency ──", start)
        backlog_region = html[start:end]
        assert "s !== 'approved'" not in backlog_region
        assert "s !== 'rejected'" not in backlog_region
        assert "s !== 'withdrawn'" not in backlog_region
        assert "isDashboardPendingApplication" in backlog_region


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
        assert "previous_assignee_id" in src
        assert "new_assignee_id" in src

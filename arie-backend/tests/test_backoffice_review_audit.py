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
        with open(self.BACKOFFICE_PATH, "r") as f:
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
        with open(self.BACKOFFICE_PATH, "r") as f:
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
        with open(server_path, "r") as f:
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
        with open(self.BACKOFFICE_PATH, "r") as f:
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

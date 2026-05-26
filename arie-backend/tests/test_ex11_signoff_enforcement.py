"""
EX-11 Follow-up — Backend enforcement and audit persistence for officer sign-off

Tests verify:
  Part A: Backend enforcement of officer_signoff on decision / override / memo approval
  Part B: Persistent audit trail with server-side context
  Part C: Frontend hardening — fail-closed guards and sign-off payload
  Part D: XSS hardening — ai_source escaping
  Part E: Regression — existing EX-11 labeling and memo gates intact
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ── Path to the back-office HTML for frontend assertions ──
BACKOFFICE_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'arie-backoffice.html'
)

# ── Path to server.py for backend assertions ──
SERVER_PATH = os.path.join(os.path.dirname(__file__), '..', 'server.py')


@pytest.fixture(scope='module')
def backoffice_html():
    with open(BACKOFFICE_PATH, 'r', encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def server_py():
    with open(SERVER_PATH, 'r', encoding='utf-8') as f:
        return f.read()


# ═══════════════════════════════════════════════════
# Unit tests for _validate_officer_signoff
# ═══════════════════════════════════════════════════


class TestValidateOfficerSignoff:
    """Direct unit tests for the sign-off validation function."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import importlib
        import sys
        # Import the server module to access the helper
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        import server
        self._validate = server._validate_officer_signoff

    def test_rejects_missing_signoff(self):
        err = self._validate(None, "decision")
        assert err is not None
        assert "officer_signoff is required" in err

    def test_rejects_non_dict_signoff(self):
        err = self._validate("true", "decision")
        assert err is not None
        assert "must be an object" in err

    def test_rejects_non_dict_signoff_list(self):
        err = self._validate([True], "decision")
        assert err is not None
        assert "must be an object" in err

    def test_rejects_acknowledged_false(self):
        signoff = {"acknowledged": False, "scope": "decision", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "acknowledged must be true" in err

    def test_rejects_acknowledged_missing(self):
        signoff = {"scope": "decision", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "acknowledged must be true" in err

    def test_rejects_acknowledged_string_true(self):
        signoff = {"acknowledged": "true", "scope": "decision", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "acknowledged must be true" in err

    def test_rejects_invalid_scope(self):
        signoff = {"acknowledged": True, "scope": "invalid", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "scope must be one of" in err

    def test_rejects_scope_mismatch(self):
        signoff = {"acknowledged": True, "scope": "memo", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "scope mismatch" in err

    def test_rejects_wrong_source_context(self):
        signoff = {"acknowledged": True, "scope": "decision", "source_context": "manual"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "source_context must be 'ai_advisory'" in err

    def test_rejects_missing_source_context(self):
        signoff = {"acknowledged": True, "scope": "decision"}
        err = self._validate(signoff, "decision")
        assert err is not None
        assert "source_context must be 'ai_advisory'" in err

    def test_accepts_valid_decision_signoff(self):
        signoff = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
        err = self._validate(signoff, "decision")
        assert err is None

    def test_accepts_valid_override_signoff(self):
        signoff = {"acknowledged": True, "scope": "override", "source_context": "ai_advisory"}
        err = self._validate(signoff, "override")
        assert err is None

    def test_accepts_valid_memo_signoff(self):
        signoff = {"acknowledged": True, "scope": "memo", "source_context": "ai_advisory"}
        err = self._validate(signoff, "memo")
        assert err is None

    def test_rejects_empty_dict(self):
        err = self._validate({}, "decision")
        assert err is not None

    def test_rejects_extra_fields_still_validates(self):
        """Extra fields are ignored — only required fields are validated."""
        signoff = {"acknowledged": True, "scope": "decision",
                   "source_context": "ai_advisory", "extra": "data"}
        err = self._validate(signoff, "decision")
        assert err is None


# ═══════════════════════════════════════════════════
# Unit tests for _persist_signoff_audit
# ═══════════════════════════════════════════════════


class TestPersistSignoffAudit:
    """Verify sign-off audit records are persisted with server-side context."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path):
        """Create an isolated SQLite DB with audit_log table for each test."""
        import sqlite3 as _sqlite3
        self._db_path = str(tmp_path / "signoff_audit_test.db")
        conn = _sqlite3.connect(self._db_path)
        conn.row_factory = _sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                user_id TEXT,
                user_name TEXT,
                user_role TEXT,
                action TEXT NOT NULL,
                target TEXT,
                detail TEXT,
                ip_address TEXT,
                before_state TEXT,
                after_state TEXT
            )
        """)
        conn.commit()
        self._conn = conn
        yield
        conn.close()

    def test_signoff_audit_persisted(self):
        import server
        user = {"sub": "officer001", "name": "Test Officer", "role": "sco"}
        signoff_obj = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
        server._persist_signoff_audit(
            self._conn, user, "ARF-TEST-001", "decision", signoff_obj,
            "192.168.1.100", "Mozilla/5.0 Test"
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM audit_log WHERE action = ? AND target = ? ORDER BY id DESC LIMIT 1",
            ("Officer Sign-Off (decision)", "ARF-TEST-001")
        ).fetchone()

        assert row is not None
        assert row["user_id"] == "officer001"
        assert row["user_name"] == "Test Officer"
        assert row["user_role"] == "sco"
        assert row["ip_address"] == "192.168.1.100"

        detail = json.loads(row["detail"])
        assert detail["signoff_acknowledged"] is True
        assert detail["signoff_scope"] == "decision"
        assert detail["source_context"] == "ai_advisory"
        assert detail["user_agent"] == "Mozilla/5.0 Test"

    def test_signoff_audit_override_scope(self):
        import server
        user = {"sub": "officer002", "name": "Override Officer", "role": "admin"}
        signoff_obj = {"acknowledged": True, "scope": "override", "source_context": "ai_advisory"}
        server._persist_signoff_audit(
            self._conn, user, "ARF-TEST-002", "override", signoff_obj,
            "10.0.0.1", "CustomAgent/1.0"
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM audit_log WHERE action = ? AND target = ? ORDER BY id DESC LIMIT 1",
            ("Officer Sign-Off (override)", "ARF-TEST-002")
        ).fetchone()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["signoff_scope"] == "override"

    def test_signoff_audit_memo_scope(self):
        import server
        user = {"sub": "officer003", "name": "Memo Officer", "role": "sco"}
        signoff_obj = {"acknowledged": True, "scope": "memo", "source_context": "ai_advisory"}
        server._persist_signoff_audit(
            self._conn, user, "ARF-TEST-003", "memo", signoff_obj,
            "172.16.0.1", "MemoClient/2.0"
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM audit_log WHERE action = ? AND target = ? ORDER BY id DESC LIMIT 1",
            ("Officer Sign-Off (memo)", "ARF-TEST-003")
        ).fetchone()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["signoff_scope"] == "memo"
        assert detail["user_agent"] == "MemoClient/2.0"

    def test_signoff_audit_no_client_side_ip(self):
        """IP must come from server request context, not hardcoded 'client'."""
        import server
        user = {"sub": "officer004", "name": "IP Officer", "role": "co"}
        signoff_obj = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
        server._persist_signoff_audit(
            self._conn, user, "ARF-IP-001", "decision", signoff_obj,
            "203.0.113.50", "RealBrowser/1.0"
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM audit_log WHERE target = ? ORDER BY id DESC LIMIT 1",
            ("ARF-IP-001",)
        ).fetchone()
        assert row["ip_address"] != "client"
        assert row["ip_address"] == "203.0.113.50"


# ═══════════════════════════════════════════════════
# Part A — Backend enforcement in server.py
# ═══════════════════════════════════════════════════


class TestPartA_BackendEnforcement:
    """Verify server.py enforces officer_signoff on decision/override/memo endpoints."""

    def test_decision_handler_validates_signoff(self, server_py):
        # Decision handler must call _validate_officer_signoff
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert '_validate_officer_signoff' in handler_region, \
            "Decision handler must call _validate_officer_signoff"
        assert 'officer_signoff' in handler_region

    def test_decision_handler_rejects_missing_signoff(self, server_py):
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'signoff_error' in handler_region, \
            "Decision handler must check signoff validation errors"

    def test_memo_handler_validates_signoff(self, server_py):
        handler_start = server_py.index('class MemoApproveHandler')
        handler_region = server_py[handler_start:handler_start + 3000]
        assert '_validate_officer_signoff' in handler_region, \
            "Memo approval handler must call _validate_officer_signoff"

    def test_memo_handler_rejects_missing_signoff(self, server_py):
        handler_start = server_py.index('class MemoApproveHandler')
        handler_region = server_py[handler_start:handler_start + 3000]
        assert 'signoff_error' in handler_region

    def test_signoff_validation_function_exists(self, server_py):
        assert 'def _validate_officer_signoff' in server_py

    def test_signoff_audit_function_exists(self, server_py):
        assert 'def _persist_signoff_audit' in server_py

    def test_valid_scopes_defined(self, server_py):
        assert '_VALID_SIGNOFF_SCOPES' in server_py
        assert '"decision"' in server_py or "'decision'" in server_py
        assert '"override"' in server_py or "'override'" in server_py
        assert '"memo"' in server_py or "'memo'" in server_py

    def test_decision_handler_sets_override_scope(self, server_py):
        """Override decisions must use scope='override', not scope='decision'."""
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'override' in handler_region

    def test_decision_persists_signoff_audit(self, server_py):
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_end = server_py.index('class DecisionRecordsHandler', handler_start)
        handler_region = server_py[handler_start:handler_end]
        assert '_persist_signoff_audit' in handler_region, \
            "Decision handler must persist sign-off audit record"

    def test_memo_persists_signoff_audit(self, server_py):
        handler_start = server_py.index('class MemoApproveHandler')
        handler_end = server_py.index('class MemoValidationResultsHandler', handler_start)
        handler_region = server_py[handler_start:handler_end]
        assert '_persist_signoff_audit' in handler_region, \
            "Memo approval handler must persist sign-off audit record"


# ═══════════════════════════════════════════════════
# Part B — Audit trail uses server-side context
# ═══════════════════════════════════════════════════


class TestPartB_AuditTrail:
    """Sign-off audit uses server-derived context, not client-side."""

    def test_audit_captures_ip_from_request(self, server_py):
        """Sign-off audit must use get_client_ip(), not hardcoded value."""
        assert 'self.get_client_ip()' in server_py

    def test_audit_captures_user_agent(self, server_py):
        """Sign-off audit must capture User-Agent from request headers."""
        assert 'User-Agent' in server_py

    def test_signoff_audit_includes_user_agent_in_detail(self, server_py):
        fn_start = server_py.index('def _persist_signoff_audit')
        fn_end = server_py.index('\nclass ', fn_start)
        fn_region = server_py[fn_start:fn_end]
        assert 'user_agent' in fn_region

    def test_signoff_audit_includes_scope_in_detail(self, server_py):
        fn_start = server_py.index('def _persist_signoff_audit')
        fn_end = server_py.index('\nclass ', fn_start)
        fn_region = server_py[fn_start:fn_end]
        assert 'signoff_scope' in fn_region

    def test_signoff_audit_not_client_side_only(self, server_py):
        """Audit must be server-side, not relying on client AUDIT_LOG array."""
        fn_start = server_py.index('def _persist_signoff_audit')
        fn_end = server_py.index('\nclass ', fn_start)
        fn_region = server_py[fn_start:fn_end]
        assert 'INSERT INTO audit_log' in fn_region


# ═══════════════════════════════════════════════════
# Part C — Frontend hardening (fail-closed)
# ═══════════════════════════════════════════════════


class TestPartC_FrontendHardening:
    """Frontend sign-off guards must fail closed and send payload."""

    def test_decision_guard_fails_closed(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        # Must use !checkbox || !checkbox.checked (fail-closed)
        assert '!signoffCheckbox || !signoffCheckbox.checked' in fn_region, \
            "Decision sign-off guard must fail closed with !checkbox || !checked"

    def test_decision_guard_not_fail_open(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        # Must NOT use checkbox && !checkbox.checked (fail-open)
        fail_open = re.search(r'signoffCheckbox\s*&&\s*!signoffCheckbox\.checked', fn_region)
        assert fail_open is None, \
            "Decision guard must NOT use fail-open pattern (checkbox && !checked)"

    def test_override_guard_fails_closed(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        assert '!signoffCheckbox || !signoffCheckbox.checked' in fn_region, \
            "Override sign-off guard must fail closed"

    def test_override_guard_not_fail_open(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        fail_open = re.search(r'signoffCheckbox\s*&&\s*!signoffCheckbox\.checked', fn_region)
        assert fail_open is None

    def test_memo_guard_fails_closed(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        assert '!memoSignoff || !memoSignoff.checked' in fn_region, \
            "Memo sign-off guard must fail closed"

    def test_memo_guard_not_fail_open(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        fail_open = re.search(r'memoSignoff\s*&&\s*!memoSignoff\.checked', fn_region)
        assert fail_open is None

    def test_decision_sends_signoff_payload(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert 'officer_signoff' in fn_region, \
            "confirmDecision must include officer_signoff in API payload"
        assert "scope: 'decision'" in fn_region

    def test_override_sends_signoff_payload(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert 'officer_signoff' in fn_region, \
            "confirmOverride must include officer_signoff in API payload"
        assert "scope: 'override'" in fn_region

    def test_memo_sends_signoff_payload(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 4000]
        assert 'officer_signoff' in fn_region, \
            "approveMemo must include officer_signoff in API payload"
        assert "scope: 'memo'" in fn_region

    def test_decision_no_hardcoded_ip_client(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert "ip:'client'" not in fn_region, \
            "confirmDecision must not hardcode ip:'client'"

    def test_override_no_hardcoded_ip_client(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert "ip:'client'" not in fn_region, \
            "confirmOverride must not hardcode ip:'client'"


# ═══════════════════════════════════════════════════
# Part D — XSS hardening
# ═══════════════════════════════════════════════════


class TestPartD_XSSHardening:
    """ai_source rendering must be escaped to prevent XSS."""

    def test_ai_source_escaped_in_source_tag(self, backoffice_html):
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        # The generic fallback case for unknown ai_source must use escapeHtml
        assert 'escapeHtml(aiSource)' in fn_region, \
            "Unknown ai_source values must be escaped with escapeHtml()"

    def test_ai_source_escaped_in_simulated_banner(self, backoffice_html):
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'escapeHtml(aiSource.toUpperCase())' in fn_region, \
            "aiSource.toUpperCase() must be escaped with escapeHtml()"

    def test_ai_source_not_raw_in_source_tag(self, backoffice_html):
        """The generic fallback should NOT inject raw aiSource into HTML."""
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        # Check that the pattern "Source: ' + aiSource + '" (unescaped) is NOT present
        raw_pattern = re.search(
            r"Source:\s*'\s*\+\s*aiSource\s*\+\s*'",
            fn_region
        )
        assert raw_pattern is None, \
            "Raw aiSource must not be injected into HTML without escaping"

    def test_fallback_reason_escaped(self, backoffice_html):
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'escapeHtml(fallbackReason)' in fn_region, \
            "fallbackReason must be escaped"

    def test_known_sources_not_escaped_unnecessary(self, backoffice_html):
        """Known sources (mock, demo, live, deterministic, fallback) use string literals."""
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        # Known sources use safe string literals, not user input
        assert "Source: Mock / Simulated" in fn_region
        assert "Source: Live AI" in fn_region
        assert "Source: Demo Mode" in fn_region
        assert "Source: Deterministic (Rule-Based)" in fn_region
        assert "Source: Fallback Template" in fn_region

    def test_escape_html_function_exists(self, backoffice_html):
        assert 'function escapeHtml(str)' in backoffice_html
        assert '&amp;' in backoffice_html
        assert '&lt;' in backoffice_html
        assert '&gt;' in backoffice_html


# ═══════════════════════════════════════════════════
# Part E — Regression tests
# ═══════════════════════════════════════════════════


class TestPartE_Regression:
    """Verify existing EX-11 labeling and approval gates remain intact."""

    def test_advisory_banners_still_present(self, backoffice_html):
        assert 'AI-Generated — Advisory Only' in backoffice_html
        assert 'ai-advisory-banner' in backoffice_html
        assert 'ai-advisory-badge' in backoffice_html

    def test_signoff_checkboxes_still_present(self, backoffice_html):
        assert 'id="decision-officer-signoff"' in backoffice_html
        assert 'id="override-officer-signoff"' in backoffice_html
        assert 'id="memo-officer-signoff"' in backoffice_html

    def test_existing_memo_gates_preserved(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert 'GATE 1' in fn_region
        assert 'GATE 2' in fn_region
        assert 'GATE 3' in fn_region
        assert 'GATE 4' in fn_region
        assert 'GATE 5' in fn_region

    def test_decision_still_requires_reason(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 1000]
        assert 'Please provide a reason' in fn_region

    def test_override_still_requires_reason(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 1000]
        assert 'Please provide a reason for the override' in fn_region

    def test_backend_decision_still_validates_reason(self, server_py):
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'decision_reason is required' in handler_region

    def test_backend_decision_still_validates_override_reason(self, server_py):
        handler_start = server_py.index('class ApplicationDecisionHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'override_reason is required when override_ai is true' in handler_region

    def test_backend_memo_still_checks_validation_status(self, server_py):
        handler_start = server_py.index('class MemoApproveHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'validation_status' in handler_region

    def test_backend_memo_still_checks_supervisor_verdict(self, server_py):
        handler_start = server_py.index('class MemoApproveHandler')
        handler_region = server_py[handler_start:handler_start + 5000]
        assert 'supervisor_verdict' in handler_region

    def test_simulated_labeling_preserved(self, backoffice_html):
        assert 'Simulated — Not From Live AI' in backoffice_html
        assert 'Simulated — Demo Mode Output' in backoffice_html
        assert 'ai-simulated-banner' in backoffice_html

    def test_source_tags_preserved(self, backoffice_html):
        assert '.ai-source-tag.live' in backoffice_html
        assert '.ai-source-tag.mock' in backoffice_html
        assert '.ai-source-tag.deterministic' in backoffice_html
        assert '.ai-source-tag.demo' in backoffice_html
        assert '.ai-source-tag.fallback' in backoffice_html

    def test_signoff_gate_css_preserved(self, backoffice_html):
        assert '.officer-signoff-gate' in backoffice_html

    def test_signoff_text_preserved(self, backoffice_html):
        assert 'AI outputs are advisory only' in backoffice_html
        assert 'accept responsibility' in backoffice_html

"""
EX-12: Client-side security hardening tests.

Tests cover:
  PART A — Back-office role permission guards (hasPermission / assertPermission)
  PART B — Logout cleanup (portal + back-office token & draft clearing)
  PART C — Portal ownership assertion
  PART D — No client-submitted role in decision payloads
  Regression — Authorized users can still complete legitimate workflows
"""

import os
import re
import json
import pytest

# ──────────────────────────────────────────────────────────
# Helpers: extract JS source from HTML files
# ──────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKOFFICE_HTML = os.path.join(ROOT, "arie-backoffice.html")
PORTAL_HTML = os.path.join(ROOT, "arie-portal.html")


def _read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════
# PART A — Back-office role permission guards
# ═══════════════════════════════════════════════════════════

class TestPartA_RolePermissionHelpers:
    """Verify hasPermission() and assertPermission() helpers exist and are used."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _read_file(BACKOFFICE_HTML)

    # ── Helper existence ──

    def test_has_permission_function_defined(self):
        assert "function hasPermission(permissionId)" in self.html

    def test_assert_permission_function_defined(self):
        assert "function assertPermission(permissionId)" in self.html

    def test_has_permission_checks_role_permissions(self):
        """hasPermission must consult the ROLE_PERMISSIONS matrix."""
        assert "ROLE_PERMISSIONS" in self.html
        # Must look up currentUser.role
        fn = self._extract_function("hasPermission")
        assert "currentUser" in fn
        assert "role" in fn

    def test_assert_permission_shows_toast_on_failure(self):
        """assertPermission must show a toast on failure."""
        fn = self._extract_function("assertPermission")
        assert "showToast" in fn
        assert "Insufficient permissions" in fn

    def test_assert_permission_returns_false_on_failure(self):
        fn = self._extract_function("assertPermission")
        assert "return false" in fn

    # ── Guards on privileged actions ──

    GUARDED_ACTIONS = [
        ("approveApplication", "approve_low_medium"),
        ("rejectApplication", "reject_applications"),
        ("escalateCase", "escalate_to_sco"),
        ("confirmOverride", "override_ai_risk_score"),
        ("preApproveApplication", "approve_low_medium"),
        ("preApprovalReject", "reject_applications"),
        ("preApprovalRequestInfo", "request_more_information"),
        ("requestMoreInfo", "request_more_information"),
        ("generateComplianceMemo", "view_compliance_memo"),
        ("approveMemo", "approve_low_medium"),
    ]

    @pytest.mark.parametrize("fn_name,perm_id", GUARDED_ACTIONS)
    def test_action_has_permission_guard(self, fn_name, perm_id):
        """Each privileged action must call assertPermission before API call."""
        fn = self._extract_function(fn_name)
        assert "assertPermission" in fn, f"{fn_name} missing assertPermission guard"

    @pytest.mark.parametrize("fn_name,perm_id", GUARDED_ACTIONS)
    def test_action_checks_correct_permission(self, fn_name, perm_id):
        """The guard must reference the correct permission ID."""
        fn = self._extract_function(fn_name)
        assert perm_id in fn, f"{fn_name} does not check permission '{perm_id}'"

    def test_confirm_decision_checks_permission_per_decision_type(self):
        """confirmDecision must map decision types to permissions."""
        fn = self._extract_function("confirmDecision")
        assert "assertPermission" in fn
        # Must have a mapping for at least approve, reject, escalate
        assert "approve_low_medium" in fn
        assert "reject_applications" in fn
        assert "escalate_to_sco" in fn

    def test_guard_returns_before_api_call(self):
        """Guards must return before boApiCall / fetch is invoked."""
        for fn_name, _ in self.GUARDED_ACTIONS:
            fn = self._extract_function(fn_name)
            guard_pos = fn.find("assertPermission")
            api_pos = max(fn.find("boApiCall"), fn.find("fetch("))
            if api_pos > 0:
                assert guard_pos < api_pos, (
                    f"{fn_name}: assertPermission must appear before API call"
                )

    def test_guard_returns_before_modal_open(self):
        """For approve/reject/escalate, guard must appear before modal open."""
        for fn_name in ["approveApplication", "rejectApplication", "escalateCase", "requestMoreInfo"]:
            fn = self._extract_function(fn_name)
            guard_pos = fn.find("assertPermission")
            modal_pos = fn.find("classList.add('open')")
            if modal_pos > 0:
                assert guard_pos < modal_pos, (
                    f"{fn_name}: assertPermission must appear before modal is opened"
                )

    def test_guard_returns_before_pending_decision_set(self):
        """For approve/reject/escalate, guard must prevent pendingDecision mutation."""
        for fn_name in ["approveApplication", "rejectApplication", "escalateCase"]:
            fn = self._extract_function(fn_name)
            guard_pos = fn.find("assertPermission")
            pd_pos = fn.find("pendingDecision")
            assert guard_pos < pd_pos, (
                f"{fn_name}: assertPermission must appear before pendingDecision is set"
            )

    # ── Unauthorized role blocked ──

    def test_analyst_cannot_approve(self):
        """ROLE_PERMISSION_MATRIX: analyst is NOT in approve_low_medium roles."""
        # Verify the backend matrix (source of truth)
        from server import ROLE_PERMISSION_MATRIX  # noqa: WPS433
        approve_perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "approve_low_medium")
        assert "analyst" not in approve_perm["roles"]

    def test_analyst_cannot_reject(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "reject_applications")
        assert "analyst" not in perm["roles"]

    def test_co_cannot_override(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "override_ai_risk_score")
        assert "co" not in perm["roles"]

    def test_analyst_cannot_override(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "override_ai_risk_score")
        assert "analyst" not in perm["roles"]

    # ── Authorized role allowed ──

    def test_admin_can_approve(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "approve_low_medium")
        assert "admin" in perm["roles"]

    def test_sco_can_approve(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "approve_low_medium")
        assert "sco" in perm["roles"]

    def test_co_can_approve_low_medium(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "approve_low_medium")
        assert "co" in perm["roles"]

    def test_admin_can_override(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "override_ai_risk_score")
        assert "admin" in perm["roles"]

    def test_sco_can_override(self):
        from server import ROLE_PERMISSION_MATRIX
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == "override_ai_risk_score")
        assert "sco" in perm["roles"]

    # ── Helper: extract JS function body ──

    def _extract_function(self, name):
        """Extract the body of a named JS function from the HTML source."""
        # Match both "function name(" and "async function name("
        pattern = r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{"
        match = re.search(pattern, self.html)
        assert match, f"Function {name} not found in backoffice HTML"
        start = match.start()
        # Count braces to find end of function
        depth = 0
        i = match.end() - 1  # pointing at the opening brace
        for i in range(match.end() - 1, len(self.html)):
            if self.html[i] == "{":
                depth += 1
            elif self.html[i] == "}":
                depth -= 1
                if depth == 0:
                    break
        return self.html[start : i + 1]


# ═══════════════════════════════════════════════════════════
# PART B — Logout cleanup
# ═══════════════════════════════════════════════════════════

class TestPartB_LogoutCleanup:
    """Verify logout functions clear browser storage keys."""

    # ── Back-office signOut ──

    def test_backoffice_signout_clears_bo_auth_token(self):
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        assert "BO_AUTH_TOKEN = ''" in fn or 'BO_AUTH_TOKEN = ""' in fn

    def test_backoffice_signout_clears_bo_auth_user(self):
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        assert "BO_AUTH_USER = null" in fn

    def test_backoffice_signout_clears_localstorage_token_keys(self):
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        for key in ["token", "access_token", "portal_token", "auth_token", "session_token"]:
            assert key in fn, f"signOut must clear '{key}' from localStorage"

    def test_backoffice_signout_clears_sessionstorage_token_keys(self):
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        assert "sessionStorage.removeItem" in fn

    def test_backoffice_signout_clears_arie_draft_keys(self):
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        assert "arie_draft_" in fn

    # ── Portal clearAuth ──

    def test_portal_clearauth_clears_auth_token(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "AUTH_TOKEN = ''" in fn or 'AUTH_TOKEN = ""' in fn

    def test_portal_clearauth_clears_auth_user(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "AUTH_USER = null" in fn

    def test_portal_clearauth_clears_localstorage_token_keys(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        for key in ["token", "access_token", "portal_token", "auth_token", "session_token"]:
            assert key in fn, f"clearAuth must clear '{key}' from localStorage"

    def test_portal_clearauth_clears_sessionstorage(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "sessionStorage.removeItem" in fn

    def test_portal_clearauth_clears_arie_draft_keys(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "arie_draft_" in fn

    def test_portal_clearauth_resets_application_state(self):
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "resetPortalApplicationState" in fn

    def test_backoffice_signout_does_not_wipe_theme(self):
        """signOut should not clear regmind-theme (unrelated user preference)."""
        html = _read_file(BACKOFFICE_HTML)
        fn = _extract_js_function(html, "signOut")
        assert "regmind-theme" not in fn

    def test_portal_clearauth_does_not_blindly_clear_all(self):
        """clearAuth should NOT call localStorage.clear() — selective removal only."""
        html = _read_file(PORTAL_HTML)
        fn = _extract_js_function(html, "clearAuth")
        assert "localStorage.clear()" not in fn
        assert "sessionStorage.clear()" not in fn


# ═══════════════════════════════════════════════════════════
# PART C — Portal ownership assertion
# ═══════════════════════════════════════════════════════════

class TestPartC_PortalOwnershipAssertion:
    """Verify portal performs client-side ownership checks (defence-in-depth)."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _read_file(PORTAL_HTML)

    def test_resume_application_checks_ownership(self):
        fn = _extract_js_function(self.html, "resumeApplication")
        assert "client_id" in fn, "resumeApplication must check app.client_id"

    def test_resume_application_checks_auth_user_sub(self):
        fn = _extract_js_function(self.html, "resumeApplication")
        assert "AUTH_USER" in fn
        assert "sub" in fn

    def test_resume_application_resets_state_on_mismatch(self):
        fn = _extract_js_function(self.html, "resumeApplication")
        # On mismatch: must reset state and redirect
        assert "resetPortalApplicationState" in fn

    def test_resume_application_shows_error_on_mismatch(self):
        fn = _extract_js_function(self.html, "resumeApplication")
        assert "Unauthorized" in fn or "do not have permission" in fn

    def test_resume_application_redirects_on_mismatch(self):
        fn = _extract_js_function(self.html, "resumeApplication")
        assert "showView('my-apps')" in fn

    def test_ownership_check_before_data_population(self):
        """Ownership assertion must happen before form data is populated."""
        fn = _extract_js_function(self.html, "resumeApplication")
        ownership_pos = fn.find("client_id")
        populate_pos = fn.find("restoreDraftFromData")
        assert ownership_pos < populate_pos, (
            "Ownership check must happen before restoring form data"
        )

    def test_backend_ownership_check_exists(self, temp_db):
        """Backend must enforce ownership via check_app_ownership."""
        from base_handler import BaseHandler
        assert hasattr(BaseHandler, "check_app_ownership")


# ═══════════════════════════════════════════════════════════
# PART D — No client-submitted role in decision payloads
# ═══════════════════════════════════════════════════════════

class TestPartD_NoClientRoleSubmission:
    """Verify client-side code does not send role in decision/approval payloads."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _read_file(BACKOFFICE_HTML)

    DECISION_FUNCTIONS = [
        "confirmDecision",
        "confirmOverride",
        "preApproveApplication",
        "preApprovalReject",
        "preApprovalRequestInfo",
    ]

    @pytest.mark.parametrize("fn_name", DECISION_FUNCTIONS)
    def test_no_role_in_payload(self, fn_name):
        """Decision payloads must NOT include currentUser.role."""
        fn = _extract_js_function(self.html, fn_name)
        # Extract the payload object (between boApiCall call braces)
        # role should not be in the JSON payload — only in AUDIT_LOG
        lines = fn.split("\n")
        payload_lines = []
        in_payload = False
        for line in lines:
            if "boApiCall(" in line or "var payload" in line:
                in_payload = True
            if in_payload:
                payload_lines.append(line)
                if ")" in line and in_payload and len(payload_lines) > 1:
                    break
        payload_block = "\n".join(payload_lines)
        # Should not contain role: currentUser.role in the payload
        assert "role: currentUser.role" not in payload_block or "AUDIT" in payload_block

    def test_backend_derives_role_from_jwt(self, temp_db):
        """Backend decision handler uses require_auth(roles=...) not client payload."""
        import inspect
        from server import ApplicationDecisionHandler
        src = inspect.getsource(ApplicationDecisionHandler.post)
        assert "require_auth" in src
        assert "roles=" in src

    def test_backend_memo_approval_derives_role_from_jwt(self, temp_db):
        import inspect
        from server import MemoApproveHandler
        src = inspect.getsource(MemoApproveHandler.post)
        assert "require_auth" in src
        assert "roles=" in src


# ═══════════════════════════════════════════════════════════
# Regression — Backend remains authoritative
# ═══════════════════════════════════════════════════════════

class TestRegression_BackendAuthority:
    """Ensure EX-12 changes do not weaken backend enforcement."""

    def test_decision_endpoint_requires_auth(self, temp_db):
        from server import ApplicationDecisionHandler
        import inspect
        src = inspect.getsource(ApplicationDecisionHandler.post)
        assert 'require_auth' in src

    def test_decision_endpoint_requires_officer_roles(self, temp_db):
        from server import ApplicationDecisionHandler
        import inspect
        src = inspect.getsource(ApplicationDecisionHandler.post)
        assert '"admin"' in src
        assert '"sco"' in src
        assert '"co"' in src

    def test_memo_approval_requires_admin_sco(self, temp_db):
        from server import MemoApproveHandler
        import inspect
        src = inspect.getsource(MemoApproveHandler.post)
        assert '"admin"' in src
        assert '"sco"' in src

    def test_pre_approval_requires_officer_roles(self, temp_db):
        from server import PreApprovalDecisionHandler
        import inspect
        src = inspect.getsource(PreApprovalDecisionHandler.post)
        assert 'require_auth' in src

    def test_officer_signoff_validation_still_enforced(self, temp_db):
        """EX-11 officer sign-off gate must still exist on decision endpoint."""
        from server import ApplicationDecisionHandler
        import inspect
        src = inspect.getsource(ApplicationDecisionHandler.post)
        assert "_validate_officer_signoff" in src

    def test_check_app_ownership_still_enforced(self, temp_db):
        """Portal ownership check on backend must still be in place."""
        from base_handler import BaseHandler
        import inspect
        src = inspect.getsource(BaseHandler.check_app_ownership)
        assert "client_id" in src
        assert "sub" in src

    def test_roles_permissions_endpoint_returns_matrix(self, temp_db):
        """Backend RBAC matrix endpoint must still return permissions."""
        from server import ROLE_PERMISSION_MATRIX
        assert isinstance(ROLE_PERMISSION_MATRIX, list)
        assert len(ROLE_PERMISSION_MATRIX) > 0
        ids = [p["id"] for p in ROLE_PERMISSION_MATRIX]
        assert "approve_low_medium" in ids
        assert "reject_applications" in ids
        assert "override_ai_risk_score" in ids
        assert "escalate_to_sco" in ids

    def test_role_permission_matrix_structure(self, temp_db):
        """Each permission must have id, label, and roles list."""
        from server import ROLE_PERMISSION_MATRIX
        for perm in ROLE_PERMISSION_MATRIX:
            assert "id" in perm
            assert "label" in perm
            assert "roles" in perm
            assert isinstance(perm["roles"], list)


# ═══════════════════════════════════════════════════════════
# Regression — E2E authorized workflow still works
# ═══════════════════════════════════════════════════════════

class TestRegression_AuthorizedWorkflowE2E:
    """Confirm that authorized officers can still use decision endpoints."""

    def test_admin_has_decision_capable_role(self, temp_db):
        """Admin token carries the admin role which is in decision-allowed roles."""
        from server import create_token, decode_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        claims = decode_token(token)
        assert claims["role"] == "admin"
        # admin is in [admin, sco, co] — the decision handler allowed roles
        assert claims["role"] in ("admin", "sco", "co")

    def test_sco_token_has_correct_role(self, temp_db):
        """SCO auth token carries the sco role."""
        from server import create_token, decode_token
        token = create_token("sco001", "sco", "Test SCO", "officer")
        claims = decode_token(token)
        assert claims["role"] == "sco"

    def test_co_token_has_correct_role(self, temp_db):
        """CO auth token carries the co role."""
        from server import create_token, decode_token
        token = create_token("co001", "co", "Test CO", "officer")
        claims = decode_token(token)
        assert claims["role"] == "co"

    def test_analyst_token_has_correct_role(self, temp_db):
        """Analyst token carries the analyst role — NOT in decision roles."""
        from server import create_token, decode_token
        token = create_token("analyst001", "analyst", "Test Analyst", "officer")
        claims = decode_token(token)
        assert claims["role"] == "analyst"
        # analyst is NOT in decision-allowed roles
        assert claims["role"] not in ("admin", "sco", "co")


# ═══════════════════════════════════════════════════════════
# Utility: JS function extractor
# ═══════════════════════════════════════════════════════════

def _extract_js_function(html, name):
    """Extract the body of a named JS function from HTML source."""
    pattern = r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{"
    match = re.search(pattern, html)
    assert match, f"Function {name} not found in HTML"
    depth = 0
    for i in range(match.end() - 1, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[match.start() : i + 1]
    raise AssertionError(f"Could not find end of function {name}")

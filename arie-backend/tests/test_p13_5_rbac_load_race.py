"""P13-5 / FEO-006 — close the RBAC-matrix load race in the back office.

**What this does NOT do, and why.** The register item reads "role-UI
fail-closed until RBAC matrix loads". Implemented literally, that inverts a
deliberate decision: `test_approval_ux_gates_static.py::
test_ux_gates_fail_open_when_permission_matrix_unloaded` pins the fail-OPEN
behaviour by name, and its docstring records why — a prior staging smoke
failure where a legitimate CO lost the Approve button on a matrix-load race.
Inverting it would reintroduce a smoke blocker to satisfy a LOW-severity item.

So the *window* is removed instead of the policy. The matrix now settles
before the authenticated shell paints on both the auto-login and fresh-login
paths, so role-dependent controls are no longer rendered optimistically with
`ROLE_PERMISSIONS === null` and then corrected. When the matrix genuinely
fails, the UI still fails open exactly as documented — and now says so, via
the existing degraded-load banner, instead of silently presenting a permissive
toolbar.

The wait is bounded: `ensureRolePermissionsLoaded` already converts failures
to `false`, but a SLOW or hanging matrix endpoint would otherwise delay
sign-in, which is the property
`test_backoffice_login_resilience_runtime.py` protects.
"""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HTML = (REPO / "arie-backoffice.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required for the runtime harness")


def _extract(name, kind="function"):
    match = re.search(
        r"^(?:async )?" + kind + r" " + re.escape(name) + r"\(.*?^}",
        HTML, re.S | re.M)
    assert match, f"{name} not found"
    return match.group(0)


SETTLE = _extract("settleRolePermissionsBeforeShell")


class TestFailOpenPolicyIsUnchanged:
    """The guard rail: this item must not silently invert the documented
    policy that a prior smoke blocker established."""

    def test_role_permissions_loaded_gate_still_exists(self):
        assert "function rolePermissionsLoaded(" in HTML

    def test_approve_hide_still_requires_a_loaded_matrix(self):
        """The literal string test_approval_ux_gates_static.py pins."""
        assert "rolePermissionsLoaded() && !hasPermission('approve_low_medium')" in HTML

    def test_detail_action_visibility_still_fails_open(self):
        assert ("setDetailActionVisibility('btn-override', !rolePermissionsLoaded() "
                "|| hasPermission('override_ai_risk_score'))") in HTML


class TestMatrixSettlesBeforeTheShellPaints:
    def _startup_block(self):
        match = re.search(
            r"document\.addEventListener\('DOMContentLoaded', async function\(\).*?^\}\);",
            HTML, re.S | re.M)
        assert match
        return match.group(0)

    def _login_block(self):
        return _extract("handleLogin", kind="function")

    def test_auto_login_settles_the_matrix_before_activating_the_shell(self):
        block = self._startup_block()
        settle_at = block.index("settleRolePermissionsBeforeShell")
        shell_at = block.index("activateAuthenticatedShell()")
        assert settle_at < shell_at, (
            "the matrix must settle before the shell paints, or the "
            "optimistic-render window remains")

    def test_auto_login_starts_the_matrix_load_in_parallel_with_auth_me(self):
        """Kicking it off before awaiting /auth/me keeps the added latency at
        roughly zero."""
        block = self._startup_block()
        kickoff_at = block.index("ensureRolePermissionsLoaded()")
        auth_await_at = block.index("await boApiCall('GET', '/auth/me')")
        assert kickoff_at < auth_await_at

    def test_fresh_login_settles_the_matrix_before_activating_the_shell(self):
        block = self._login_block()
        settle_at = block.index("settleRolePermissionsBeforeShell")
        shell_at = block.index("activateAuthenticatedShell()")
        assert settle_at < shell_at

    def test_both_paths_report_a_failed_matrix_to_the_operator(self):
        for block in (self._startup_block(), self._login_block()):
            assert "'Roles and permissions'" in block
            assert "showDashboardLoadWarning(failedModules)" in block


class TestBoundedWaitRuntime:
    """The wait must never hold sign-in hostage."""

    def _run(self, scenario_js):
        script = "\n".join([SETTLE, scenario_js])
        proc = subprocess.run(["node", "-e", script], capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def test_resolved_true_passes_through(self):
        out = self._run(textwrap.dedent("""
            (async () => {
              const r = await settleRolePermissionsBeforeShell(Promise.resolve(true));
              console.log(String(r));
            })();
        """))
        assert out == "true"

    def test_resolved_false_passes_through(self):
        out = self._run(textwrap.dedent("""
            (async () => {
              const r = await settleRolePermissionsBeforeShell(Promise.resolve(false));
              console.log(String(r));
            })();
        """))
        assert out == "false"

    def test_a_rejected_promise_becomes_false_and_does_not_throw(self):
        out = self._run(textwrap.dedent("""
            (async () => {
              try {
                const r = await settleRolePermissionsBeforeShell(
                  Promise.reject(new Error('boom')));
                console.log(String(r));
              } catch (e) { console.log('THREW'); }
            })();
        """))
        assert out == "false"

    def test_a_hanging_matrix_load_does_not_block_forever(self):
        """A never-settling promise must time out rather than wedge sign-in."""
        out = self._run(textwrap.dedent("""
            (async () => {
              const started = Date.now();
              const r = await settleRolePermissionsBeforeShell(new Promise(() => {}));
              console.log(JSON.stringify({
                value: r, elapsedUnder: (Date.now() - started) < 10000
              }));
              process.exit(0);
            })();
        """))
        import json
        parsed = json.loads(out)
        assert parsed["value"] is False
        assert parsed["elapsedUnder"] is True

    def test_the_timeout_is_bounded_and_stated(self):
        assert "timeoutMs" in SETTLE
        bound = int(re.search(r"var timeoutMs = (\d+);", SETTLE).group(1))
        assert 0 < bound <= 5000, f"unbounded or excessive wait: {bound}ms"

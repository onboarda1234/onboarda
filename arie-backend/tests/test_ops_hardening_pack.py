"""Ops-hardening pack (2026-07) — static + unit guards.

Pins the four repo-side halves of the pack:
- P12-10 (DCI-025): deploy fails closed on a backend services-stable timeout.
- Screening-queue ops ticket: the queue handler emits the p95 latency metric
  line, hard-isolated after the response.
- p95 provisioning script builds a correct metric filter + ExtendedStatistic
  alarm (dry-run safe).
- P10-7 grants pack + P9-12/alarm runbook exist and carry the load-bearing
  statements.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "arie-backend"
sys.path.insert(0, str(BACKEND))

WORKFLOW = (REPO / ".github" / "workflows" / "deploy-staging.yml").read_text(encoding="utf-8")
SERVER = (BACKEND / "server.py").read_text(encoding="utf-8")


# ── P12-10 / DCI-025: fail-closed services-stable wait ──────────────────────
class TestDeployTimeoutFailClosed:
    def test_backend_wait_exits_nonzero_on_timeout(self):
        # The backend stabilization block must exit 1 — not proceed on WARNING.
        block = WORKFLOW.split("Deploying task definition:")[1].split(
            "Deploy verification worker")[0]
        assert "exit 1" in block, (
            "P12-10: backend services-stable timeout must FAIL the deploy"
        )
        assert "WARNING: ECS service did not stabilize" not in block, (
            "the fail-open warning path must be gone"
        )
        assert "describe-services" in block, "diagnostics must be dumped on failure"

    def test_backend_wait_has_two_attempts(self):
        block = WORKFLOW.split("Deploying task definition:")[1].split(
            "Deploy verification worker")[0]
        assert block.count("aws ecs wait services-stable") == 2, (
            "two waiter attempts (~20 min budget) before failing closed"
        )

    def test_worker_wait_still_fails_closed(self):
        worker_block = WORKFLOW.split("Deploy verification worker")[1]
        assert "exit 1" in worker_block.split("ops-enforce-staging")[0]


# ── Screening-queue latency emission (change-controlled surface: additive,
#    response-isolated) ──────────────────────────────────────────────────────
class TestQueueLatencyEmission:
    def _handler_body(self):
        start = SERVER.index("class ScreeningQueueHandler")
        end = SERVER.index("_SCREENING_HIT_DISPOSITIONS")
        return SERVER[start:end]

    def test_metric_emitted_after_response(self):
        body = self._handler_body()
        assert "ScreeningQueueLatencyMs" in body
        # Emission must come AFTER the response write so it can never alter
        # the change-controlled queue payload.
        assert body.index("self.success(payload)") < body.index(
            "ScreeningQueueLatencyMs")

    def test_emission_is_hard_isolated(self):
        body = self._handler_body()
        tail = body[body.index("self.success(payload)"):]
        assert "try:" in tail and "except Exception:" in tail and "pass" in tail, (
            "metric emission must be wrapped so observability failures cannot "
            "reach the handler"
        )


# ── p95 provisioning script (dry-run unit) ──────────────────────────────────
class TestP95ProvisioningScript:
    def _mod(self):
        import importlib.util
        path = BACKEND / "scripts" / "provision_screening_queue_p95_alarm.py"
        spec = importlib.util.spec_from_file_location("p95prov", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_metric_filter_targets_emitted_line(self):
        mod = self._mod()
        f = mod.build_metric_filter(environment="staging",
                                    log_group="/ecs/regmind-staging")
        assert f["logGroupName"] == "/ecs/regmind-staging"
        assert 'cloudwatch_metric' in f["filterPattern"]
        assert "ScreeningQueueLatencyMs" in f["filterPattern"]
        t = f["metricTransformations"][0]
        assert t["metricValue"] == "$.metric_value"
        assert t["unit"] == "Milliseconds"

    def test_alarm_uses_extended_statistic_p95(self):
        mod = self._mod()
        a = mod.build_alarm_spec(environment="staging",
                                 alarm_action_arn="", threshold_ms=2000)
        assert a["ExtendedStatistic"] == "p95"
        assert "Statistic" not in a  # mutually exclusive with ExtendedStatistic
        assert a["Threshold"] == 2000.0
        assert a["TreatMissingData"] == "notBreaching"
        assert a["AlarmActions"] == []  # empty ARN → no broken action ref

    def test_dry_run_is_default_and_prints_plan(self):
        proc = subprocess.run(
            [sys.executable,
             str(BACKEND / "scripts" / "provision_screening_queue_p95_alarm.py")],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "DRY-RUN" in proc.stdout
        assert "put_metric_alarm" in proc.stdout


# ── P10-7 grants pack + runbook presence ────────────────────────────────────
class TestGrantsPackAndRunbook:
    def test_grants_sql_strips_and_keeps_the_right_privileges(self):
        sql = (BACKEND / "scripts" / "apply_audit_log_append_only_grants.sql"
               ).read_text(encoding="utf-8")
        assert re.search(r"REVOKE UPDATE, DELETE, TRUNCATE.*ON TABLE audit_log", sql)
        assert re.search(r"GRANT INSERT, SELECT ON TABLE audit_log", sql)
        assert "has_table_privilege" in sql  # built-in verification
        assert "ON_ERROR_STOP" in sql

    def test_grants_sql_boot_and_purge_safety(self):
        # Review round-1 defects, pinned:
        # (1) TRIGGER must NOT be revoked — the boot path re-creates the
        #     append-only triggers as the app role every start; revoking it
        #     crash-loops the next deploy.
        # (2) The NOLOGIN maintenance role must be made assumable (PG15 needs
        #     explicit membership even for the master user).
        sql = (BACKEND / "scripts" / "apply_audit_log_append_only_grants.sql"
               ).read_text(encoding="utf-8")
        revoke_lines = [l for l in sql.splitlines() if l.strip().startswith("REVOKE")]
        assert revoke_lines and all("TRIGGER" not in l for l in revoke_lines), (
            "TRIGGER must not be revoked from the table-owning app role"
        )
        assert re.search(r'GRANT :"maint_role" TO :"admin_role"', sql), (
            "maintenance role must be granted to the admin role (SET ROLE)"
        )
        # The sanctioned-purge role change must be documented where operators look.
        rb = (REPO / "docs" / "OPS_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")
        assert "SET ROLE regmind_audit_maint" in rb
        purge_doc = (REPO / "docs" / "compliance" / "MANUAL_PURGE_PROCEDURE.md"
                     ).read_text(encoding="utf-8")
        assert "SET ROLE regmind_audit_maint" in purge_doc

    def test_runbook_covers_all_three_ops_halves(self):
        rb = (REPO / "docs" / "OPS_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")
        assert "put-image-tag-mutability" in rb          # P9-12
        assert "ImageTagAlreadyExistsException" in rb     # re-run caveat documented
        assert "provision_screening_queue_p95_alarm" in rb
        assert "apply_audit_log_append_only_grants.sql" in rb

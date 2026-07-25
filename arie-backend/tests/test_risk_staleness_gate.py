"""P10-3 / RDI-004 — decision-time stale-risk gate + config-update failure surfacing.

Covers:
  1. `_application_risk_staleness_error` — blocks approval when the stored risk
     score was not computed against the current risk config; blocks apps carrying
     the `stale:recompute_failed` quarantine sentinel; fails CLOSED when stored
     provenance is absent/malformed or the current validated configuration
     cannot be obtained; passes only when both timestamped versions match.
  2. `_summarize_risk_recompute_results` — reports recompute failures explicitly
     instead of silently counting them as successes.
  3. Quarantine stamping — `recompute_risk_for_active_apps` marks failed apps
     with the sentinel so the gate blocks them regardless of prior provenance.
  4. Source guard — the staleness gate is wired into the approve branch after
     the authority gate, and the config save stamps a microsecond-precision
     version timestamp (no same-second version collisions).

The gate helper imports `_get_validated_risk_config_version_strict` at call time, so these
tests monkeypatch it for deterministic, DB-neutral behaviour (identical on
SQLite/PG).
"""
import os

from fixture_safe_refs import fixture_safe_suffix
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _patch_current_version(monkeypatch, value):
    import rule_engine
    monkeypatch.setattr(
        rule_engine,
        "_get_validated_risk_config_version_strict",
        lambda db: value,
    )


class TestRiskStalenessGate:
    @pytest.mark.parametrize(
        "version",
        [
            "risk_config:2026-07-07 00:00:00.123456",
            "risk_config:2026-07-07T00:00:00Z",
        ],
    )
    def test_matching_config_version_passes(self, monkeypatch, version):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, version)
        app = {"id": "A1", "status": "kyc_submitted",
               "risk_config_version": version}
        assert _application_risk_staleness_error(object(), app, "approve application") is None

    def test_older_config_version_blocks(self, monkeypatch):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T12:00:00Z")
        app = {"id": "A2", "status": "kyc_submitted",
               "risk_config_version": "risk_config:2026-01-01T00:00:00Z"}
        err = _application_risk_staleness_error(object(), app, "approve application")
        assert err is not None
        assert "older" in err
        assert "Recompute risk" in err

    def test_quarantine_sentinel_blocks_with_specific_message(self, monkeypatch):
        """An app stamped stale:recompute_failed is blocked even though its prior
        provenance may have been NULL — this is the RDI-004 quarantine path."""
        from rule_engine import RISK_CONFIG_VERSION_RECOMPUTE_FAILED
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T12:00:00Z")
        app = {"id": "AQ", "status": "kyc_submitted",
               "risk_config_version": RISK_CONFIG_VERSION_RECOMPUTE_FAILED}
        err = _application_risk_staleness_error(object(), app, "approve application")
        assert err is not None
        assert "FAILED" in err
        assert "quarantined" in err

    @pytest.mark.parametrize(
        "invalid_version",
        [
            None,
            "",
            "   ",
            "risk_config:",
            "risk_config:v1",
            "not-a-risk-version",
            " risk_config:2026-07-07T12:00:00Z",
            "risk_config:2026-07-07T12:00:00Z ",
            "risk_config:2026-13-07T12:00:00Z",
        ],
    )
    def test_missing_or_malformed_application_version_fails_closed(
        self, monkeypatch, invalid_version
    ):
        from server import (
            _APPROVAL_RISK_PROVENANCE_REQUIRED,
            _application_risk_staleness_error,
        )
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T12:00:00Z")
        app = {
            "id": "A3",
            "status": "kyc_submitted",
            "risk_config_version": invalid_version,
        }
        assert (
            _application_risk_staleness_error(
                object(), app, "approve application"
            )
            == _APPROVAL_RISK_PROVENANCE_REQUIRED
        )

    def test_missing_current_config_version_fails_closed(self, monkeypatch):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, None)
        app = {
            "id": "A4",
            "status": "kyc_submitted",
            "risk_config_version": "risk_config:2026-07-07T12:00:00Z",
        }
        err = _application_risk_staleness_error(
            object(), app, "approve application"
        )
        assert err is not None
        assert "Recompute risk" in err

    def test_version_lookup_failure_fails_closed(self, monkeypatch):
        """A provenance check that cannot run must NOT approve (fail closed)."""
        import rule_engine
        from server import _application_risk_staleness_error

        def _boom(db):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            rule_engine, "_get_validated_risk_config_version_strict", _boom
        )
        app = {
            "id": "A5",
            "status": "kyc_submitted",
            "risk_config_version": "risk_config:2026-07-07T12:00:00Z",
        }
        err = _application_risk_staleness_error(object(), app, "approve application")
        assert err is not None
        assert "could not be verified" in err

    def test_empty_app_returns_none(self, monkeypatch):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T12:00:00Z")
        assert _application_risk_staleness_error(object(), None, "approve application") is None


class TestVersionHelpers:
    def test_nonstrict_swallows_strict_errors(self, monkeypatch):
        """_get_risk_config_version keeps its legacy swallow-and-None contract."""
        import rule_engine

        def _boom(db):
            raise RuntimeError("db down")

        monkeypatch.setattr(rule_engine, "_get_risk_config_version_strict", _boom)
        assert rule_engine._get_risk_config_version(object()) is None

    def test_sentinel_never_equals_a_real_version(self):
        from rule_engine import RISK_CONFIG_VERSION_RECOMPUTE_FAILED
        assert not RISK_CONFIG_VERSION_RECOMPUTE_FAILED.startswith("risk_config:")
        assert RISK_CONFIG_VERSION_RECOMPUTE_FAILED.startswith("stale:")


class TestQuarantineStamping:
    def test_failed_recompute_stamps_sentinel(self, monkeypatch, temp_db):
        """A failed per-app recompute in the config-update sweep must stamp the
        quarantine sentinel so the staleness gate blocks approval."""
        import uuid
        import rule_engine
        from rule_engine import (
            recompute_risk_for_active_apps,
            RISK_CONFIG_VERSION_RECOMPUTE_FAILED,
        )
        from db import get_db

        db = get_db()
        ok_id = f"q_ok_{uuid.uuid4().hex[:8]}"
        bad_id = f"q_bad_{uuid.uuid4().hex[:8]}"
        for app_id, ref_sfx in ((ok_id, "OK"), (bad_id, "BAD")):
            db.execute(
                """INSERT INTO applications
                       (id, ref, client_id, company_name, country, sector, entity_type,
                        status, risk_level, final_risk_level, risk_score,
                        risk_config_version, prescreening_data,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'SME',
                           'kyc_submitted', 'LOW', 'LOW', 20,
                           NULL, '{}', datetime('now'), datetime('now'))""",
                (app_id, f"QRT-{ref_sfx}-{fixture_safe_suffix(6, prefix=f'QRT-{ref_sfx}-')}", f"{app_id}_c",
                 f"Quarantine {ref_sfx} Ltd"),
            )
        db.commit()

        real_recompute = rule_engine.recompute_risk

        def _selective(db_, app_id, reason, **kwargs):
            if app_id == bad_id:
                # Simulate the swallowed-failure shape recompute_risk returns
                # when its internal try/except catches an error.
                return {"recomputed": False, "old_score": 20, "old_level": "LOW",
                        "new_score": None, "new_level": None, "changed": False}
            return real_recompute(db_, app_id, reason, **kwargs)

        monkeypatch.setattr(rule_engine, "recompute_risk", _selective)
        try:
            results = recompute_risk_for_active_apps(db, "test_config_update")
            db.commit()
        finally:
            monkeypatch.undo()

        by_id = {r["app_id"]: r for r in results}
        assert by_id[bad_id]["recomputed"] is False

        bad_row = db.execute(
            "SELECT risk_config_version FROM applications WHERE id=?", (bad_id,)
        ).fetchone()
        assert bad_row["risk_config_version"] == RISK_CONFIG_VERSION_RECOMPUTE_FAILED, (
            "failed recompute must stamp the quarantine sentinel")

        ok_row = db.execute(
            "SELECT risk_config_version FROM applications WHERE id=?", (ok_id,)
        ).fetchone()
        assert ok_row["risk_config_version"] != RISK_CONFIG_VERSION_RECOMPUTE_FAILED

        # And the gate blocks the quarantined app.
        from server import _application_risk_staleness_error
        app = dict(db.execute("SELECT * FROM applications WHERE id=?", (bad_id,)).fetchone())
        err = _application_risk_staleness_error(db, app, "approve application")
        assert err is not None and "quarantined" in err

        # Cleanup so other suites are unaffected.
        db.execute("DELETE FROM applications WHERE id IN (?, ?)", (ok_id, bad_id))
        db.commit()
        db.close()


class TestRiskRecomputeSummary:
    def test_all_recomputed_no_warning(self):
        from server import _summarize_risk_recompute_results
        results = [
            {"app_id": "A", "recomputed": True, "changed": True},
            {"app_id": "B", "recomputed": True, "changed": False},
        ]
        s = _summarize_risk_recompute_results(results)
        assert s["risk_recomputed_apps"] == 2
        assert s["risk_recompute_failures"] == 0
        assert s["risk_changed_apps"] == 1
        assert "warning" not in s
        assert "risk_recompute_failed_app_ids" not in s

    def test_failures_surfaced_not_counted_as_success(self):
        from server import _summarize_risk_recompute_results
        results = [
            {"app_id": "A", "recomputed": True, "changed": True},
            {"app_id": "B", "recomputed": False, "changed": False},   # swallowed failure
            {"app_id": "C", "recomputed": False, "changed": False},
        ]
        s = _summarize_risk_recompute_results(results)
        # Only genuine successes are counted as recomputed.
        assert s["risk_recomputed_apps"] == 1
        assert s["risk_recompute_attempted"] == 3
        assert s["risk_recompute_failures"] == 2
        assert set(s["risk_recompute_failed_app_ids"]) == {"B", "C"}
        assert "warning" in s and "quarantined" in s["warning"]

    def test_empty_results(self):
        from server import _summarize_risk_recompute_results
        s = _summarize_risk_recompute_results([])
        assert s["risk_recomputed_apps"] == 0
        assert s["risk_recompute_failures"] == 0
        assert "warning" not in s


class TestStalenessGateWiring:
    def test_gate_is_wired_into_approve_branch(self):
        """Source guard: the staleness gate must be invoked on the approve path,
        AFTER the authority gate (authority errors keep precedence)."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "server.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        # Helper exists and is called on approval with the approve label.
        assert "def _application_risk_staleness_error(" in src
        gate_call = '_application_risk_staleness_error(db, app, "approve application")'
        assert gate_call in src
        # Authority gate runs before the staleness gate within the approve branch.
        approve_branch = src.split('if decision == "approve":')[1]
        assert approve_branch.index("can_decide_application(") < approve_branch.index(
            "_application_risk_staleness_error("), (
            "authority gate must run before the staleness gate")
        assert "return self.error(staleness_error, 409)" in approve_branch
        # Config-update path routes through the honest summary helper.
        assert "_summarize_risk_recompute_results(recomp_results)" in src
        # Config save stamps a microsecond-precision version timestamp
        # (prevents same-second version collisions on SQLite).
        assert "%Y-%m-%d %H:%M:%S.%f" in src

    def test_sweep_stamps_quarantine_sentinel_in_source(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "rule_engine.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        assert 'RISK_CONFIG_VERSION_RECOMPUTE_FAILED = "stale:recompute_failed"' in src
        assert "RISK_CONFIG_VERSION_RECOMPUTE_FAILED, row[\"id\"]" in src

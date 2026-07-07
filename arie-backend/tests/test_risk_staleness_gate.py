"""P10-3 / RDI-004 — decision-time stale-risk gate + config-update failure surfacing.

Covers:
  1. `_application_risk_staleness_error` — blocks approval when the stored risk
     score was not computed against the current risk config; passes when current;
     no-ops when versioning is not in use.
  2. `_summarize_risk_recompute_results` — reports recompute failures explicitly
     instead of silently counting them as successes.
  3. Source guard — the staleness gate is wired into the approve branch.

The gate helper imports `_get_risk_config_version` at call time, so these tests
monkeypatch it for deterministic, DB-neutral behaviour (identical on SQLite/PG).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _patch_current_version(monkeypatch, value):
    import rule_engine
    monkeypatch.setattr(rule_engine, "_get_risk_config_version", lambda db: value)


class TestRiskStalenessGate:
    def test_matching_config_version_passes(self, monkeypatch):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T00:00:00Z")
        app = {"id": "A1", "status": "kyc_submitted",
               "risk_config_version": "risk_config:2026-07-07T00:00:00Z"}
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

    def test_missing_config_version_does_not_block(self, monkeypatch):
        """Unknown provenance (no stored version) is a documented residual: it is
        NOT blocked, because many legitimately-scored apps predate version
        stamping and the live scoring path stamps versions going forward. The
        gate targets the precise RDI-004 case: present-and-different only."""
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:2026-07-07T12:00:00Z")
        for missing in (None, ""):
            app = {"id": "A3", "status": "kyc_submitted", "risk_config_version": missing}
            assert _application_risk_staleness_error(object(), app, "approve application") is None

    def test_no_current_config_does_not_block(self, monkeypatch):
        """Versioning not in use (no risk_config) -> gate must not invent a block."""
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, None)
        app = {"id": "A4", "status": "kyc_submitted", "risk_config_version": None}
        assert _application_risk_staleness_error(object(), app, "approve application") is None

    def test_version_lookup_failure_does_not_block(self, monkeypatch):
        """If the current version cannot be read, do not fabricate a block."""
        import rule_engine
        from server import _application_risk_staleness_error

        def _boom(db):
            raise RuntimeError("db down")

        monkeypatch.setattr(rule_engine, "_get_risk_config_version", _boom)
        app = {"id": "A5", "status": "kyc_submitted", "risk_config_version": "risk_config:v1"}
        assert _application_risk_staleness_error(object(), app, "approve application") is None

    def test_empty_app_returns_none(self, monkeypatch):
        from server import _application_risk_staleness_error
        _patch_current_version(monkeypatch, "risk_config:v1")
        assert _application_risk_staleness_error(object(), None, "approve application") is None


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
        assert "warning" in s and "stale risk score" in s["warning"]

    def test_empty_results(self):
        from server import _summarize_risk_recompute_results
        s = _summarize_risk_recompute_results([])
        assert s["risk_recomputed_apps"] == 0
        assert s["risk_recompute_failures"] == 0
        assert "warning" not in s


class TestStalenessGateWiring:
    def test_gate_is_wired_into_approve_branch(self):
        """Source guard: the staleness gate must be invoked on the approve path."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "server.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        # Helper exists and is called on approval with the approve label.
        assert "def _application_risk_staleness_error(" in src
        assert '_application_risk_staleness_error(db, app, "approve application")' in src
        # Config-update path routes through the honest summary helper.
        assert "_summarize_risk_recompute_results(recomp_results)" in src

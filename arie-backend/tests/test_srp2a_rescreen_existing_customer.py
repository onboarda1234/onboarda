"""SRP-2a Phase D — existing-customer re-screen via the Mesh rescreen workflow.

Pins the delta-merge fail-closed contract:

* Flag OFF → today's create-and-screen path byte-identical (conflict net intact).
* Flag ON + stored subscription → DELTA rescreen against the STORED Mesh
  customer UUID (never our external identifier), relaxed UUID recovered via
  the external-identifier lookup, unique idempotency key per call, and
  create-and-screen is never invoked for that subject.
* NO CHANGES FOUND carries the baseline forward verbatim — it is NOT zero hits.
* Delta hits APPEND with dedup by stable provider reference; per-subject and
  report-level hit counts are monotonically non-decreasing.
* Any failed pass (errored / timed out / lookup 404 / 403 feature-gate)
  carries the baseline forward AND degrades the report so the rule-engine
  risk-lowering hold engages. Never fewer hits, never a fabricated "clear".
"""

import json
import os
import re
import sqlite3
import sys
import uuid as uuid_module
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter
from screening_complyadvantage.exceptions import CABadRequest
from screening_complyadvantage.models import CAAlertResponse
from screening_complyadvantage.normalizer import ScreeningApplicationContext
from screening_complyadvantage.orchestrator import (
    ComplyAdvantageScreeningOrchestrator,
    RESCREEN_COMPLETED_CHANGES,
    RESCREEN_COMPLETED_NO_CHANGES,
    RESCREEN_CUSTOMER_NOT_FOUND,
    RESCREEN_ERRORED,
    RESCREEN_ERRORED_DEGRADED_SOURCE,
    RESCREEN_NOT_FOUND_DEGRADED_SOURCE,
    _normalise_risk_as_alert,
    _parse_risk_detail,
    _RescreenPassResult,
)
from screening_complyadvantage.rescreen import (
    build_rescreen_subject_report,
    find_previous_subject_section,
    harvested_conflict_customer_identifiers,
)
from screening_complyadvantage.subscriptions import (
    application_has_active_subscriptions,
    find_subscription_customer_identifier,
)
from rule_engine import _screening_report_is_non_terminal


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
BACKOFFICE_HTML = os.path.join(REPO_ROOT, "arie-backoffice.html")
SERVER_PY = os.path.join(BACKEND_DIR, "server.py")


# ══════════════════════════════════════════════════════════
# Fixtures / helpers
# ══════════════════════════════════════════════════════════

APP_ID = "app-srp2a-1"
CLIENT_ID = "client-srp2a"


def _subscriptions_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE screening_monitoring_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            person_key TEXT,
            customer_identifier TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'test'
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX uq_screening_monitoring_subs_customer "
        "ON screening_monitoring_subscriptions (client_id, provider, customer_identifier)"
    )
    return conn


def _seed_subscription(conn, customer_identifier, person_key=None, application_id=APP_ID):
    conn.execute(
        "INSERT INTO screening_monitoring_subscriptions "
        "(client_id, application_id, provider, person_key, customer_identifier) "
        "VALUES (?, ?, ?, ?, ?)",
        (CLIENT_ID, application_id, "complyadvantage", person_key, customer_identifier),
    )
    conn.commit()


def _previous_report():
    """Stored baseline: 1 company hit + 2 director hits = total_hits 3."""
    return {
        "provider": "complyadvantage",
        "normalized_version": "2.0",
        "screening_mode": "live",
        "screened_at": "2026-06-01T00:00:00Z",
        "total_hits": 3,
        "company_screening_coverage": "full",
        "has_company_screening_hit": True,
        "company_screening": {
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": True,
            "results": [
                {"name": "Acme Sanction Match", "risk_id": "risk-ent-1",
                 "profile_identifier": "prof-ent-1", "is_sanctioned": True},
            ],
            "sanctions": {
                "source": "complyadvantage", "api_status": "live", "matched": True,
                "results": [{"name": "Acme Sanction Match", "risk_id": "risk-ent-1",
                             "profile_identifier": "prof-ent-1"}],
            },
            "adverse_media": {
                "source": "complyadvantage", "api_status": "live", "matched": False, "results": [],
            },
        },
        "director_screenings": [
            {
                "person_name": "jane doe",
                "subject_name": "Jane Doe",
                "person_key": "p1",
                "person_type": "director",
                "nationality": "",
                "declared_pep": "No",
                "provider_detected_pep": True,
                "undeclared_pep": True,
                "has_pep_hit": True,
                "has_sanctions_hit": False,
                "has_adverse_media_hit": None,
                "adverse_media_coverage": "none",
                "screening": {
                    "provider": "complyadvantage", "source": "complyadvantage",
                    "api_status": "live", "matched": True, "person_key": "p1",
                    "results": [
                        {"name": "jane doe", "risk_id": "risk-p1-1",
                         "profile_identifier": "prof-p1-1", "is_pep": True},
                        {"name": "jane doe", "risk_id": "risk-p1-2",
                         "profile_identifier": "prof-p1-2", "is_pep": True},
                    ],
                },
                "screening_state": "completed_match",
                "requires_review": True,
                "is_rca": False,
                "pep_classes": ["PEP_CLASS_1"],
            },
        ],
        "ubo_screenings": [],
        "intermediary_screenings": [],
        "overall_flags": [],
        "degraded_sources": [],
        "any_non_terminal_subject": False,
    }


def _mesh_profile(profile_id="prof-new-1", name="New Delta Hit", aml_types=("sanction",)):
    return {
        "identifier": profile_id,
        "entity_type": "person",
        "name": name,
        "match_types": ["name_exact"],
        "risk_indicators": {"aml_types": list(aml_types)},
    }


def _delta_pass(risk_id="risk-new-1", profile_id="prof-new-1", name="New Delta Hit",
                aml_types=("sanction",), outcome=RESCREEN_COMPLETED_CHANGES,
                customer_identifier="uuid-strict"):
    profile = _mesh_profile(profile_id=profile_id, name=name, aml_types=aml_types)
    alert = CAAlertResponse.model_validate(
        _normalise_risk_as_alert(risk_id, {"identifier": risk_id, "profile": profile}, alert_id="alert-1")
    )
    deep = _parse_risk_detail({"profile": profile})
    return _RescreenPassResult(
        outcome=outcome,
        raw={},
        alerts=[alert],
        deep_risks={risk_id: deep},
        customer_identifier=customer_identifier,
    )


def _no_changes_pass(customer_identifier="uuid-x"):
    return _RescreenPassResult(
        outcome=RESCREEN_COMPLETED_NO_CHANGES,
        raw={"status": "COMPLETED"},
        customer_identifier=customer_identifier,
    )


def _failed_pass(outcome=RESCREEN_ERRORED, customer_identifier=""):
    return _RescreenPassResult(outcome=outcome, customer_identifier=customer_identifier)


def _context(kind="director", person_key="p1", name="Jane Doe"):
    return ScreeningApplicationContext(
        application_id=APP_ID,
        client_id=CLIENT_ID,
        screening_subject_kind=kind,
        screening_subject_name=name,
        screening_subject_person_key=person_key,
    )


class FakeConfig:
    screening_configuration_identifier = "cfg-123"


class FakeOrchestrator:
    """Records calls; rescreen passes are configurable per subject person_key."""

    def __init__(self, rescreen_result_fn=None):
        self.create_calls = []
        self.rescreen_calls = []
        self._rescreen_result_fn = rescreen_result_fn or (
            lambda kwargs: {"strict": _no_changes_pass(), "relaxed": _no_changes_pass()}
        )

    def screen_customer_two_pass(self, **kwargs):
        self.create_calls.append(kwargs)
        context = kwargs["application_context"]
        kind = context.screening_subject_kind
        person = {
            "person_name": context.screening_subject_name,
            "person_key": context.screening_subject_person_key,
            "person_type": "ubo" if kind == "ubo" else "director",
            "nationality": "",
            "declared_pep": "No",
            "provider_detected_pep": False,
            "undeclared_pep": False,
            "has_pep_hit": False,
            "has_sanctions_hit": False,
            "has_adverse_media_hit": None,
            "adverse_media_coverage": "none",
            "screening": {"provider": "complyadvantage", "source": "complyadvantage",
                          "api_status": "live", "matched": False, "results": []},
            "screening_state": "completed_clear",
            "requires_review": False,
            "is_rca": False,
            "pep_classes": None,
        }
        report = {
            "provider": "complyadvantage",
            "normalized_version": "2.0",
            "screened_at": "2026-07-18T00:00:00Z",
            "company_screening_coverage": "none",
            "has_company_screening_hit": None,
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "intermediary_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
            "any_non_terminal_subject": False,
            "provider_specific": {"complyadvantage": {}},
            "source_screening_report_hash": "cs-" + (context.screening_subject_person_key or "ent"),
        }
        if kind == "entity":
            report["company_screening_coverage"] = "full"
            report["has_company_screening_hit"] = False
            report["company_screening"] = {
                "provider": "complyadvantage", "source": "complyadvantage",
                "api_status": "live", "matched": False, "results": [],
            }
        elif kind == "ubo":
            report["ubo_screenings"] = [person]
        elif kind == "intermediary":
            report["intermediary_screenings"] = [dict(person, person_type="intermediary")]
        else:
            report["director_screenings"] = [person]
        return report

    def rescreen_customer_two_pass(self, **kwargs):
        self.rescreen_calls.append(kwargs)
        return self._rescreen_result_fn(kwargs)


def _adapter(db, previous_report, orchestrator=None):
    return ComplyAdvantageScreeningAdapter(
        config=FakeConfig(),
        orchestrator=orchestrator or FakeOrchestrator(),
        db=db,
        previous_report=previous_report,
    )


def _app_data():
    return {"application_id": APP_ID, "client_id": CLIENT_ID, "company_name": "Acme Ltd"}


DIRECTORS = [{"full_name": "Jane Doe", "person_key": "p1"}]


# ══════════════════════════════════════════════════════════
# Feature flag
# ══════════════════════════════════════════════════════════

class TestFeatureFlag:
    def test_flag_defaults_false_in_every_environment(self, monkeypatch):
        from screening_config import is_ca_rescreen_enabled, _CA_RESCREEN_DEFAULTS
        monkeypatch.delenv("ENABLE_CA_RESCREEN", raising=False)
        assert set(_CA_RESCREEN_DEFAULTS.values()) == {False}
        for env in ("development", "testing", "demo", "staging", "production"):
            monkeypatch.setenv("ENVIRONMENT", env)
            assert is_ca_rescreen_enabled() is False

    def test_flag_env_var_truthy_set(self, monkeypatch):
        from screening_config import is_ca_rescreen_enabled
        for value, expected in (("true", True), ("1", True), ("yes", True), ("on", True),
                                ("false", False), ("0", False), ("off", False), ("", False)):
            monkeypatch.setenv("ENABLE_CA_RESCREEN", value)
            assert is_ca_rescreen_enabled() is expected


# ══════════════════════════════════════════════════════════
# Client: extra headers merge, Authorization not overridable
# ══════════════════════════════════════════════════════════

class TestClientHeaders:
    def _client(self):
        from screening_complyadvantage.client import ComplyAdvantageClient
        config = MagicMock()
        config.api_base_url = "https://api.example.test"
        config.realm = "regmind"
        config.username = "user"
        token_client = MagicMock()
        token_client.get_token.return_value = "good-token"
        client = ComplyAdvantageClient(config, token_client=token_client)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        session = MagicMock()
        session.request.return_value = response
        client.session = session
        return client, session

    def test_extra_headers_merged_and_auth_wins(self):
        client, session = self._client()
        client.post(
            "/v2/customers/u1/workflows/sync/rescreen",
            params={"rescreen_type": "DELTA"},
            headers={"X-ComplyAdvantage-Idempotency-Key": "idem-1", "Authorization": "Bearer evil"},
        )
        sent = session.request.call_args.kwargs
        assert sent["headers"]["X-ComplyAdvantage-Idempotency-Key"] == "idem-1"
        assert sent["headers"]["Authorization"] == "Bearer good-token"
        assert sent["params"] == {"rescreen_type": "DELTA"}

    def test_no_headers_arg_behaviour_unchanged(self):
        client, session = self._client()
        client.get("/v2/workflows/wf-1")
        sent = session.request.call_args.kwargs
        assert sent["headers"] == {"Authorization": "Bearer good-token"}


# ══════════════════════════════════════════════════════════
# Orchestrator rescreen pathway (fake client, no network)
# ══════════════════════════════════════════════════════════

class FakeRescreenClient:
    def __init__(self, *, lookup_uuid="uuid-relaxed", lookup_error=None, post_error=None,
                 rescreen_response=None, delta_profile=None):
        self.get_calls = []
        self.post_calls = []
        self.lookup_uuid = lookup_uuid
        self.lookup_error = lookup_error
        self.post_error = post_error
        self.rescreen_response = rescreen_response or {
            "workflow_instance_identifier": "wf-nc",
            "workflow_type": "rescreen",
            "status": "COMPLETED",
            "step_details": {"screening": {"status": "COMPLETED", "message": "NO CHANGES FOUND"}},
        }
        self.delta_profile = delta_profile or _mesh_profile()

    def get(self, path, params=None, *, timeout=None, headers=None):
        self.get_calls.append({"path": path, "params": params, "headers": headers})
        if path.startswith("/v2/customers/external/"):
            if self.lookup_error is not None:
                raise self.lookup_error
            return {"identifier": self.lookup_uuid}
        if path.startswith("/v2/alerts/"):
            return {"risks": [{"identifier": "risk-9", "profile": self.delta_profile}], "next": None}
        if path.startswith("/v2/entity-screening/risks/"):
            return {"profile": self.delta_profile}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, json_body=None, *, params=None, timeout=None, headers=None):
        self.post_calls.append({"path": path, "params": params, "headers": headers, "json_body": json_body})
        if path == "/v2/workflows/create-and-screen":
            raise AssertionError("rescreen pathway must NEVER call create-and-screen")
        if self.post_error is not None:
            raise self.post_error
        return self.rescreen_response


class TestOrchestratorRescreen:
    def _run(self, client, *, strict_uuid="uuid-strict", db=None, context=None):
        orch = ComplyAdvantageScreeningOrchestrator(client)
        return orch.rescreen_customer_two_pass(
            strict_customer_identifier=strict_uuid,
            strict_external_identifier=f"{APP_ID}:director:key-p1:strict",
            relaxed_external_identifier=f"{APP_ID}:director:key-p1:relaxed",
            application_context=context,
            db=db,
        )

    def test_delta_rescreen_uses_stored_uuid_and_unique_idempotency_keys(self):
        client = FakeRescreenClient()
        passes = self._run(client)

        post_paths = sorted(call["path"] for call in client.post_calls)
        assert post_paths == [
            "/v2/customers/uuid-relaxed/workflows/sync/rescreen",
            "/v2/customers/uuid-strict/workflows/sync/rescreen",
        ]
        for call in client.post_calls:
            assert call["params"] == {"rescreen_type": "DELTA"}
            key = call["headers"]["X-ComplyAdvantage-Idempotency-Key"]
            uuid_module.UUID(key)  # client-generated uuid
        keys = {call["headers"]["X-ComplyAdvantage-Idempotency-Key"] for call in client.post_calls}
        assert len(keys) == 2, "idempotency key must be unique per call"

        # Strict pass used the STORED Mesh UUID — no external lookup for it.
        lookup_paths = [c["path"] for c in client.get_calls if c["path"].startswith("/v2/customers/external/")]
        assert lookup_paths == [f"/v2/customers/external/{APP_ID}:director:key-p1:relaxed"]

        assert passes["strict"].outcome == RESCREEN_COMPLETED_NO_CHANGES
        assert passes["relaxed"].outcome == RESCREEN_COMPLETED_NO_CHANGES

    def test_completed_changes_fetches_delta_alerts(self):
        client = FakeRescreenClient(rescreen_response={
            "workflow_instance_identifier": "wf-d",
            "workflow_type": "rescreen",
            "status": "COMPLETED",
            "step_details": {},
            "alerts": [{"identifier": "alert-9"}],
        })
        passes = self._run(client)
        assert passes["strict"].outcome == RESCREEN_COMPLETED_CHANGES
        assert len(passes["strict"].alerts) == 1
        assert "risk-9" in passes["strict"].deep_risks

    def test_lookup_404_is_customer_not_found_not_zero_hits(self):
        client = FakeRescreenClient(
            lookup_error=CABadRequest("not found", status_code=404, path="/v2/customers/external/x"),
        )
        passes = self._run(client)
        assert passes["relaxed"].outcome == RESCREEN_CUSTOMER_NOT_FOUND
        assert passes["relaxed"].alerts == []
        # Strict pass (stored uuid) still ran.
        assert passes["strict"].outcome == RESCREEN_COMPLETED_NO_CHANGES

    def test_provider_403_feature_gate_fails_closed_as_errored(self):
        client = FakeRescreenClient(
            post_error=CABadRequest("monitor on demand not enabled", status_code=403, path="x"),
        )
        passes = self._run(client)
        assert passes["strict"].outcome == RESCREEN_ERRORED
        assert passes["relaxed"].outcome == RESCREEN_ERRORED
        assert passes["strict"].alerts == []

    def test_errored_workflow_response_is_errored(self):
        client = FakeRescreenClient(rescreen_response={
            "workflow_instance_identifier": "wf-e",
            "workflow_type": "rescreen",
            "status": "ERRORED",
            "step_details": {},
        })
        passes = self._run(client)
        assert passes["strict"].outcome == RESCREEN_ERRORED

    def test_recovered_strict_uuid_seeds_same_customer_subscription(self):
        db = _subscriptions_db()
        client = FakeRescreenClient(lookup_uuid="uuid-recovered")
        self._run(client, strict_uuid="", db=db, context=_context())
        rows = db.execute(
            "SELECT customer_identifier, person_key, source FROM screening_monitoring_subscriptions"
        ).fetchall()
        identifiers = {row["customer_identifier"] for row in rows}
        assert identifiers == {"uuid-recovered"}, "only the recovered UUID may be seeded — never a new customer"
        assert rows[0]["source"] == "srp2a_rescreen_recovery"

    def test_stored_strict_uuid_never_seeds_new_subscription(self):
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-strict", person_key="p1")
        client = FakeRescreenClient()
        self._run(client, strict_uuid="uuid-strict", db=db, context=_context())
        rows = db.execute("SELECT customer_identifier FROM screening_monitoring_subscriptions").fetchall()
        assert [row["customer_identifier"] for row in rows] == ["uuid-strict"]


# ══════════════════════════════════════════════════════════
# Subscription lookup helpers
# ══════════════════════════════════════════════════════════

class TestSubscriptionLookup:
    def test_person_and_entity_lookup(self):
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")
        assert find_subscription_customer_identifier(db, APP_ID, "p1") == "uuid-p1"
        assert find_subscription_customer_identifier(db, APP_ID, None) == "uuid-ent"
        assert find_subscription_customer_identifier(db, APP_ID, "missing") is None
        assert find_subscription_customer_identifier(db, "other-app", "p1") is None
        assert application_has_active_subscriptions(db, APP_ID) is True
        assert application_has_active_subscriptions(db, "other-app") is False

    def test_inactive_subscription_rows_ignored(self):
        db = _subscriptions_db()
        db.execute(
            "INSERT INTO screening_monitoring_subscriptions "
            "(client_id, application_id, provider, person_key, customer_identifier, status) "
            "VALUES (?, ?, 'complyadvantage', 'p1', 'uuid-cancelled', 'cancelled')",
            (CLIENT_ID, APP_ID),
        )
        db.commit()
        assert find_subscription_customer_identifier(db, APP_ID, "p1") is None
        assert application_has_active_subscriptions(db, APP_ID) is False


# ══════════════════════════════════════════════════════════
# Delta-merge semantics (the core fail-closed rule)
# ══════════════════════════════════════════════════════════

class TestDeltaMerge:
    def test_no_changes_carries_subject_forward_verbatim_with_stamp(self):
        prev = _previous_report()
        prev_entry = prev["director_screenings"][0]
        report = build_rescreen_subject_report(
            kind="director",
            context=_context(),
            previous_section=deepcopy(prev_entry),
            strict=_no_changes_pass("uuid-strict"),
            relaxed=_no_changes_pass("uuid-relaxed"),
        )
        entry = report["director_screenings"][0]
        stamp = entry.pop("rescreen")
        assert stamp["outcome"] == "no_changes"
        assert stamp["mode"] == "delta"
        assert stamp["rescreened_at"]
        assert entry == prev_entry, "NO CHANGES FOUND must carry the baseline forward verbatim"
        assert report["total_hits"] == 2
        assert report["degraded_sources"] == []
        assert _screening_report_is_non_terminal(report) is False

    def test_delta_appends_and_dedups_by_provider_reference(self):
        prev = _previous_report()
        prev_entry = prev["director_screenings"][0]
        # Delta carries one ALREADY-PRESENT risk_id and one new one.
        strict = _delta_pass(risk_id="risk-p1-1", profile_id="prof-p1-1", name="jane doe")
        relaxed = _delta_pass(risk_id="risk-new-9", profile_id="prof-new-9", name="Jane D",
                              outcome=RESCREEN_COMPLETED_CHANGES)
        report = build_rescreen_subject_report(
            kind="director",
            context=_context(),
            previous_section=deepcopy(prev_entry),
            strict=strict,
            relaxed=relaxed,
        )
        entry = report["director_screenings"][0]
        results = entry["screening"]["results"]
        risk_ids = [row.get("risk_id") for row in results]
        assert risk_ids.count("risk-p1-1") == 1, "already-present provider reference must not double"
        assert "risk-new-9" in risk_ids
        assert len(results) == 3  # 2 baseline + 1 new
        assert report["total_hits"] == 3 >= 2
        assert entry["rescreen"]["outcome"] == "delta_applied"
        assert entry["has_sanctions_hit"] is True  # new sanctions delta rolls up
        assert entry["has_pep_hit"] is True  # baseline rollup never lowered
        assert _screening_report_is_non_terminal(report) is False

    @pytest.mark.parametrize("failed", [
        _failed_pass(RESCREEN_ERRORED),
        _failed_pass("timed_out"),
        _failed_pass(RESCREEN_CUSTOMER_NOT_FOUND),
    ])
    def test_failed_pass_carries_baseline_and_degrades(self, failed):
        prev = _previous_report()
        prev_entry = prev["director_screenings"][0]
        report = build_rescreen_subject_report(
            kind="director",
            context=_context(),
            previous_section=deepcopy(prev_entry),
            strict=failed,
            relaxed=_no_changes_pass(),
        )
        entry = report["director_screenings"][0]
        # Previous hits are NEVER reduced by a failed rescreen.
        assert len(entry["screening"]["results"]) == 2
        assert entry["has_pep_hit"] is True
        assert entry["rescreen"]["outcome"] == "failed"
        assert entry["screening_state"] == "pending_provider"
        assert entry["requires_review"] is True
        assert entry["screening"]["api_status"] == "pending"
        expected_source = (
            RESCREEN_NOT_FOUND_DEGRADED_SOURCE
            if failed.outcome == RESCREEN_CUSTOMER_NOT_FOUND
            else RESCREEN_ERRORED_DEGRADED_SOURCE
        )
        assert expected_source in report["degraded_sources"]
        assert report["total_hits"] == 2
        assert _screening_report_is_non_terminal(report) is True, \
            "failed rescreen must engage the risk-lowering hold"

    def test_entity_no_changes_and_failure(self):
        prev = _previous_report()
        section = find_previous_subject_section(prev, "entity", None, "Acme Ltd")
        assert section["company_screening"]["results"], "entity baseline section resolved"

        ok = build_rescreen_subject_report(
            kind="entity", context=_context("entity", None, "Acme Ltd"),
            previous_section=section,
            strict=_no_changes_pass(), relaxed=_no_changes_pass(),
        )
        assert ok["company_screening"]["results"] == prev["company_screening"]["results"]
        assert ok["has_company_screening_hit"] is True
        assert ok["total_hits"] == 1
        assert _screening_report_is_non_terminal(ok) is False

        bad = build_rescreen_subject_report(
            kind="entity", context=_context("entity", None, "Acme Ltd"),
            previous_section=section,
            strict=_failed_pass(RESCREEN_ERRORED), relaxed=_no_changes_pass(),
        )
        assert bad["company_screening"]["results"] == prev["company_screening"]["results"]
        assert bad["has_company_screening_hit"] is True, "failure must not lower the company hit flag"
        assert bad["company_screening"]["api_status"] == "pending"
        assert RESCREEN_ERRORED_DEGRADED_SOURCE in bad["degraded_sources"]
        assert _screening_report_is_non_terminal(bad) is True

    def test_find_previous_subject_section_by_key_and_name(self):
        prev = _previous_report()
        assert find_previous_subject_section(prev, "director", "p1", "someone else") is not None
        assert find_previous_subject_section(prev, "director", None, "Jane Doe") is not None
        assert find_previous_subject_section(prev, "director", "px", "Nobody Here") is None
        assert find_previous_subject_section(prev, "entity", None, "Acme Ltd") is not None
        assert find_previous_subject_section({}, "entity", None, "Acme Ltd") is None


# ══════════════════════════════════════════════════════════
# Adapter routing: flag gate, subscription gate, mixed applications
# ══════════════════════════════════════════════════════════

class TestAdapterRouting:
    def test_flag_off_uses_create_and_screen_only(self, monkeypatch):
        monkeypatch.delenv("ENABLE_CA_RESCREEN", raising=False)
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")
        orch = FakeOrchestrator()
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        assert orch.rescreen_calls == [], "rescreen must never run with the flag off"
        assert len(orch.create_calls) == 2  # company + Jane
        assert "rescreen_summary" not in report

    def test_flag_on_with_subscription_rescreens_with_stored_uuid(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")
        orch = FakeOrchestrator()
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])

        assert orch.create_calls == [], "subscribed subjects must not go through create-and-screen"
        assert len(orch.rescreen_calls) == 2
        by_uuid = {call["strict_customer_identifier"]: call for call in orch.rescreen_calls}
        assert set(by_uuid) == {"uuid-ent", "uuid-p1"}
        jane = by_uuid["uuid-p1"]
        assert jane["strict_external_identifier"].endswith(":strict")
        assert jane["relaxed_external_identifier"].endswith(":relaxed")
        assert jane["strict_external_identifier"].startswith(f"{APP_ID}:director:key-p1")

        summary = report["rescreen_summary"]
        assert summary == {
            "requested_subjects": 2,
            "rescreened": 2,
            "no_changes": 2,
            "delta_applied": 0,
            "failed": 0,
            "carried_forward_baseline": True,
        }
        # NO CHANGES on every subject: hit counts identical to baseline.
        assert report["total_hits"] == 3
        assert report["director_screenings"][0]["screening"]["results"] == \
            _previous_report()["director_screenings"][0]["screening"]["results"]
        assert report["director_screenings"][0]["rescreen"]["outcome"] == "no_changes"
        assert report["company_screening"]["rescreen"]["outcome"] == "no_changes"
        assert _screening_report_is_non_terminal(report) is False

        # No new subscription with a different customer UUID was created.
        rows = db.execute("SELECT customer_identifier FROM screening_monitoring_subscriptions").fetchall()
        assert sorted(row["customer_identifier"] for row in rows) == ["uuid-ent", "uuid-p1"]

    def test_mixed_application_unsubscribed_subject_keeps_existing_path(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")
        orch = FakeOrchestrator()
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(
            _app_data(), DIRECTORS, [{"full_name": "Bob New", "person_key": "p2"}],
        )
        assert len(orch.rescreen_calls) == 2  # company + Jane
        assert len(orch.create_calls) == 1  # Bob has no subscription
        assert orch.create_calls[0]["application_context"].screening_subject_person_key == "p2"
        assert report["rescreen_summary"]["rescreened"] == 2

    def test_delta_applied_report_counts_are_monotonic(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")

        def rescreen_result(kwargs):
            if kwargs["strict_customer_identifier"] == "uuid-p1":
                return {
                    "strict": _delta_pass(risk_id="risk-new-9", profile_id="prof-new-9"),
                    "relaxed": _no_changes_pass(),
                }
            return {"strict": _no_changes_pass(), "relaxed": _no_changes_pass()}

        orch = FakeOrchestrator(rescreen_result)
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        assert report["total_hits"] == 4  # baseline 3 + 1 new delta hit
        assert report["total_hits"] >= _previous_report()["total_hits"]
        assert report["rescreen_summary"]["delta_applied"] == 1
        assert report["rescreen_summary"]["no_changes"] == 1

    def test_failed_rescreen_combined_report_engages_risk_hold(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-ent", person_key=None)
        _seed_subscription(db, "uuid-p1", person_key="p1")

        def rescreen_result(kwargs):
            return {"strict": _failed_pass(RESCREEN_ERRORED), "relaxed": _no_changes_pass()}

        orch = FakeOrchestrator(rescreen_result)
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        assert report["total_hits"] >= _previous_report()["total_hits"]
        assert RESCREEN_ERRORED_DEGRADED_SOURCE in report["degraded_sources"]
        assert report["any_non_terminal_subject"] is True
        assert report["rescreen_summary"]["failed"] == 2
        assert _screening_report_is_non_terminal(report) is True

    def test_total_hits_clamped_to_baseline_never_lower(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        _seed_subscription(db, "uuid-p1", person_key="p1")
        prev = _previous_report()
        prev["total_hits"] = 9  # baseline larger than merged sections can produce
        orch = FakeOrchestrator()
        adapter = _adapter(db, prev, orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        assert report["total_hits"] == 9, "report total_hits must never drop below the baseline"


# ══════════════════════════════════════════════════════════
# Endpoint: archive-first ordering + branch gating
# ══════════════════════════════════════════════════════════

class TestEndpointRescreen:
    def test_branch_gate_requires_flag_provider_baseline_and_subscription(self, db, monkeypatch):
        import server
        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        monkeypatch.delenv("ENABLE_CA_RESCREEN", raising=False)
        baseline = _previous_report()
        app_id = f"app-srp2a-gate-{uuid_module.uuid4().hex[:8]}"
        db.execute(
            "INSERT INTO screening_monitoring_subscriptions "
            "(client_id, application_id, provider, customer_identifier) VALUES (?, ?, 'complyadvantage', ?)",
            (CLIENT_ID, app_id, f"uuid-{app_id}"),
        )
        db.commit()
        assert server._ca_rescreen_branch_active(db, app_id, baseline) is False  # flag off
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        assert server._ca_rescreen_branch_active(db, app_id, baseline) is True
        assert server._ca_rescreen_branch_active(db, app_id, None) is False  # no baseline
        assert server._ca_rescreen_branch_active(db, "no-subs-app", baseline) is False
        monkeypatch.setenv("SCREENING_PROVIDER", "sumsub")
        assert server._ca_rescreen_branch_active(db, app_id, baseline) is False  # CA not active

    def test_rescreen_ui_enabled_flag(self, monkeypatch):
        import server
        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        assert server._rescreen_ui_enabled() is True
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "false")
        assert server._rescreen_ui_enabled() is False
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")
        assert server._rescreen_ui_enabled() is False

    def test_status_and_queue_payloads_carry_rescreen_enabled(self):
        source = open(SERVER_PY, encoding="utf-8").read()
        occurrences = source.count('"rescreen_enabled": _rescreen_ui_enabled()')
        assert occurrences >= 2, (
            "/api/screening/status payload AND the screening queue metrics must "
            "both carry the server-sent rescreen_enabled flag"
        )

    def test_endpoint_archives_outgoing_report_before_provider_call(self, temp_db, db, monkeypatch):
        """POST /api/screening/run (rescreen branch) archives FIRST, then screens."""
        import asyncio
        import socket
        import threading
        import time as time_module

        import requests as http_requests
        import tornado.httpserver
        import tornado.ioloop

        import server
        from server import create_token, make_app

        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")

        suffix = uuid_module.uuid4().hex[:8]
        app_id = f"app-srp2a-arch-{suffix}"
        ref = f"ARF-SRP2A-{suffix}"
        baseline = _previous_report()
        db.execute(
            """INSERT INTO applications
               (id, ref, company_name, country, sector, entity_type, status,
                risk_level, risk_score, prescreening_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (app_id, ref, "Acme Rescreen Ltd", "Mauritius", "Technology", "SME",
             "in_review", "MEDIUM", 50, json.dumps({"screening_report": baseline})),
        )
        db.execute(
            "INSERT INTO screening_monitoring_subscriptions "
            "(client_id, application_id, provider, customer_identifier) VALUES (?, ?, 'complyadvantage', ?)",
            (CLIENT_ID, app_id, f"uuid-{suffix}"),
        )
        db.commit()

        recorded = {}

        def fake_run_full_screening(application_data, directors, ubos, intermediaries=None,
                                    client_ip=None, db=None, provider_options=None):
            check = sqlite3.connect(temp_db)
            check.row_factory = sqlite3.Row
            try:
                row = check.execute(
                    "SELECT COUNT(*) AS n, MAX(archived_by) AS actor, MAX(reason) AS reason "
                    "FROM screening_report_archive WHERE application_id = ?",
                    (app_id,),
                ).fetchone()
            finally:
                check.close()
            recorded["archive_rows_at_provider_call"] = row["n"]
            recorded["archived_by"] = row["actor"]
            recorded["reason"] = row["reason"]
            recorded["provider_options"] = provider_options
            fresh = deepcopy(baseline)
            fresh["screened_at"] = "2026-07-18T09:00:00Z"
            fresh["rescreen_summary"] = {
                "requested_subjects": 2, "rescreened": 2, "no_changes": 2,
                "delta_applied": 0, "failed": 0, "carried_forward_baseline": True,
            }
            return fresh

        monkeypatch.setattr(server, "run_full_screening", fake_run_full_screening)

        tornado_app = make_app()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server_ref = {}
        started = threading.Event()

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            io_loop = tornado.ioloop.IOLoop.current()
            srv = tornado.httpserver.HTTPServer(tornado_app)
            srv.listen(port, "127.0.0.1")
            server_ref["srv"] = srv
            server_ref["loop"] = io_loop
            started.set()
            io_loop.start()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        started.wait(timeout=3)
        time_module.sleep(0.2)

        try:
            token = create_token("admin001", "admin", "Test Admin", "officer")
            resp = http_requests.post(
                f"http://127.0.0.1:{port}/api/screening/run",
                json={"application_id": app_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            assert resp.status_code == 200, resp.text
            assert recorded["archive_rows_at_provider_call"] == 1, \
                "the outgoing report must be archived (and committed) BEFORE provider I/O"
            assert recorded["archived_by"] == "officer_rescreen_endpoint"
            assert recorded["reason"] == "srp2a_officer_rescreen"
            assert recorded["provider_options"] == {"previous_report": baseline}, \
                "the stored baseline must be threaded to the provider as previous_report"
            archived = db.execute(
                "SELECT report_json FROM screening_report_archive WHERE application_id = ?",
                (app_id,),
            ).fetchone()
            assert json.loads(archived["report_json"])["total_hits"] == baseline["total_hits"]
        finally:
            io_loop = server_ref.get("loop")
            srv = server_ref.get("srv")
            if io_loop and srv:
                io_loop.add_callback(srv.stop)
                io_loop.add_callback(io_loop.stop)
            thread.join(timeout=2)


# ══════════════════════════════════════════════════════════
# Static UI pins (arie-backoffice.html)
# ══════════════════════════════════════════════════════════

def _extract_js_function(html, name):
    marker = f"function {name}("
    start = html.index(marker)
    depth = 0
    seen_open = False
    for index in range(start, len(html)):
        char = html[index]
        if char == "{":
            depth += 1
            seen_open = True
        elif char == "}":
            depth -= 1
            if seen_open and depth == 0:
                return html[start:index + 1]
    raise AssertionError(f"could not extract function {name}")


class TestBackofficeStaticPins:
    @pytest.fixture(scope="class")
    def html(self):
        with open(BACKOFFICE_HTML, encoding="utf-8") as fh:
            return fh.read()

    def test_rescreen_button_gated_on_server_flag_in_entity_card_only(self, html):
        gate = _extract_js_function(html, "screeningRescreenEnabled")
        assert "SCREENING_QUEUE.metrics.rescreen_enabled === true" in gate, \
            "the button must be gated on the server-sent rescreen_enabled flag"

        entity_card = _extract_js_function(html, "buildEntityScreeningReviewCard")
        assert "screeningRescreenEnabled()" in entity_card
        assert "screeningRescreenButtonHtml(app.ref)" in entity_card

        person_card = _extract_js_function(html, "buildPersonScreeningReviewCard")
        assert "screeningRescreenButtonHtml" not in person_card, "entity card only"

    def test_rescreen_button_idiom_confirm_guard_and_wiring(self, html):
        button = _extract_js_function(html, "screeningRescreenButtonHtml")
        assert "btn btn-outline btn-sm" in button  # canonical header button idiom
        assert "↻ Re-screen" in button
        assert "confirmReScreenApplication" in button
        # PR-A (audit C1): appRef is interpolated into a single-quoted JS string
        # inside the onclick, so it must go through escapeJsAttr (JS-string safe),
        # not escapeHtml (which is decoded back to a breakout quote at click time).
        assert "escapeJsAttr(appRef)" in button
        assert "escapeHtml(appRef)" not in button

        guard = _extract_js_function(html, "confirmReScreenApplication")
        assert "confirm('Re-screen this application against the provider now? " \
               "Existing results are archived first and never reduced by a re-screen.')" in guard
        assert "reScreenApplication(appRef)" in guard

        # The existing endpoint wiring is reused, not duplicated.
        rescreen_fn = _extract_js_function(html, "reScreenApplication")
        assert "'/screening/run'" in rescreen_fn

    def test_absent_flag_renders_no_button(self, html):
        entity_card = _extract_js_function(html, "buildEntityScreeningReviewCard")
        gate_index = entity_card.index("screeningRescreenEnabled()")
        line_start = entity_card.rfind("\n", 0, gate_index)
        line = entity_card[line_start:entity_card.index("\n", gate_index)]
        assert line.strip().startswith("if (screeningRescreenEnabled())"), \
            "button push must be conditional — absent/false flag renders today's UI"

    def test_provider_score_surfaces_raw_never_percent(self, html):
        # Compact Agent 3 re-anchor: the audit-trace/table helpers were retired
        # with the approved one-paragraph callout; the invariant (scores render
        # raw — never a percentage) now lives in the narrative prose and the
        # ranked hit cards' score block.
        narrative = _extract_js_function(html, "agent3TriageNarrativeHtml")
        assert "%" not in narrative, "triage score must not be rendered as a percentage"
        block = _extract_js_function(html, "screeningTriageScoreBlock")
        assert "%" not in block
        assert "Not a provider score" in block


# ══════════════════════════════════════════════════════════
# Harvested-conflict UUID recovery (ARF-QAFIX-001 → SRP-2a wiring)
# ══════════════════════════════════════════════════════════

class TestHarvestedConflictRecovery:
    """Subscription absent + lookup failed + harvested UUID present → rescreen
    proceeds against the harvested UUID; with none of the three → degraded
    fail-closed exactly as before."""

    def _run(self, client, *, strict_uuid="", harvested_strict=None, harvested_relaxed=None,
             db=None, context=None):
        orch = ComplyAdvantageScreeningOrchestrator(client)
        return orch.rescreen_customer_two_pass(
            strict_customer_identifier=strict_uuid,
            strict_external_identifier=f"{APP_ID}:director:key-p1:strict",
            relaxed_external_identifier=f"{APP_ID}:director:key-p1:relaxed",
            strict_harvested_customer_identifier=harvested_strict,
            relaxed_harvested_customer_identifier=harvested_relaxed,
            application_context=context,
            db=db,
        )

    def test_lookup_404_with_harvested_uuid_proceeds_against_it(self):
        client = FakeRescreenClient(
            lookup_error=CABadRequest("not found", status_code=404, path="/v2/customers/external/x"),
        )
        passes = self._run(
            client,
            harvested_strict="uuid-harvested-strict",
            harvested_relaxed="uuid-harvested-relaxed",
        )
        post_paths = sorted(call["path"] for call in client.post_calls)
        assert post_paths == [
            "/v2/customers/uuid-harvested-relaxed/workflows/sync/rescreen",
            "/v2/customers/uuid-harvested-strict/workflows/sync/rescreen",
        ], "both passes must rescreen against the harvested existing-customer UUIDs"
        assert passes["strict"].outcome == RESCREEN_COMPLETED_NO_CHANGES
        assert passes["relaxed"].outcome == RESCREEN_COMPLETED_NO_CHANGES
        assert passes["strict"].customer_identifier == "uuid-harvested-strict"

    def test_stored_subscription_uuid_wins_over_harvested(self):
        client = FakeRescreenClient()
        self._run(
            client,
            strict_uuid="uuid-strict",
            harvested_strict="uuid-harvested-strict",
        )
        strict_posts = [c["path"] for c in client.post_calls if "uuid-strict" in c["path"]]
        assert strict_posts == ["/v2/customers/uuid-strict/workflows/sync/rescreen"], \
            "the stored subscription UUID is authoritative; harvested is a last resort only"
        assert not any("uuid-harvested-strict" in c["path"] for c in client.post_calls)

    def test_harvested_strict_recovery_seeds_same_customer_subscription(self):
        db = _subscriptions_db()
        client = FakeRescreenClient(
            lookup_error=CABadRequest("not found", status_code=404, path="/v2/customers/external/x"),
        )
        self._run(client, harvested_strict="uuid-harvested-strict", db=db, context=_context())
        rows = db.execute(
            "SELECT customer_identifier, source FROM screening_monitoring_subscriptions"
        ).fetchall()
        assert [row["customer_identifier"] for row in rows] == ["uuid-harvested-strict"]
        assert rows[0]["source"] == "srp2a_rescreen_recovery"

    def test_none_of_the_three_fails_closed_degraded(self):
        client = FakeRescreenClient(
            lookup_error=CABadRequest("not found", status_code=404, path="/v2/customers/external/x"),
        )
        passes = self._run(client)  # no subscription uuid, lookup 404, no harvested
        assert client.post_calls == [], "no rescreen call may be made without a customer UUID"
        assert passes["strict"].outcome == RESCREEN_CUSTOMER_NOT_FOUND
        assert passes["relaxed"].outcome == RESCREEN_CUSTOMER_NOT_FOUND
        report = build_rescreen_subject_report(
            kind="director",
            context=_context(),
            previous_section=_previous_report()["director_screenings"][0],
            strict=passes["strict"],
            relaxed=passes["relaxed"],
        )
        assert RESCREEN_NOT_FOUND_DEGRADED_SOURCE in report["degraded_sources"]
        assert report["any_non_terminal_subject"] is True
        assert report["total_hits"] >= 2, "baseline hits carried forward, never zeroed"

    def test_reader_combined_shape_returns_subject_entry_only(self):
        prev = _previous_report()
        prev["customer_identifier_conflict_existing_customers"] = {
            "p1": {"strict": "uuid-h-p1-strict", "relaxed": "uuid-h-p1-relaxed"},
            "entity": {"strict": "uuid-h-ent-strict"},
        }
        assert harvested_conflict_customer_identifiers(prev, "director", "p1") == {
            "strict": "uuid-h-p1-strict",
            "relaxed": "uuid-h-p1-relaxed",
        }
        assert harvested_conflict_customer_identifiers(prev, "entity", None) == {
            "strict": "uuid-h-ent-strict",
        }
        assert harvested_conflict_customer_identifiers(prev, "director", "p2") == {}, \
            "a UUID must never be attributed to a different subject"

    def test_reader_flat_shape_requires_matching_subject_scope(self):
        flat = {"strict": "uuid-h-strict", "relaxed": "uuid-h-relaxed"}
        scoped = {
            "screening_subject_kind": "director",
            "screening_subject_person_key": "p1",
            "customer_identifier_conflict_existing_customers": dict(flat),
        }
        assert harvested_conflict_customer_identifiers(scoped, "director", "p1") == flat
        assert harvested_conflict_customer_identifiers(scoped, "director", "p2") == {}
        assert harvested_conflict_customer_identifiers(scoped, "entity", None) == {}
        unscoped = {"customer_identifier_conflict_existing_customers": dict(flat)}
        assert harvested_conflict_customer_identifiers(unscoped, "director", "p1") == {}, \
            "flat-shape UUIDs without subject scope must not be attributed — fail closed"
        assert harvested_conflict_customer_identifiers(None, "director", "p1") == {}
        assert harvested_conflict_customer_identifiers({}, "director", "p1") == {}

    def test_adapter_rescreens_via_harvested_when_subscription_absent(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()  # NO subscriptions at all
        prev = _previous_report()
        prev["customer_identifier_conflict_existing_customers"] = {
            "p1": {"strict": "uuid-h-strict", "relaxed": "uuid-h-relaxed"},
        }
        orch = FakeOrchestrator()
        adapter = _adapter(db, prev, orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        # Jane (harvested UUIDs) rescreens; the entity (no subscription, no
        # harvested entry) keeps the existing create-and-screen path.
        assert len(orch.rescreen_calls) == 1
        call = orch.rescreen_calls[0]
        assert not call["strict_customer_identifier"]
        assert call["strict_harvested_customer_identifier"] == "uuid-h-strict"
        assert call["relaxed_harvested_customer_identifier"] == "uuid-h-relaxed"
        assert len(orch.create_calls) == 1
        assert call["application_context"].screening_subject_person_key == "p1"
        assert report["rescreen_summary"]["rescreened"] == 1

    def test_adapter_without_subscription_or_harvest_keeps_existing_path(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CA_RESCREEN", "true")
        db = _subscriptions_db()
        orch = FakeOrchestrator()
        adapter = _adapter(db, _previous_report(), orch)
        report = adapter.run_full_screening(_app_data(), DIRECTORS, [])
        assert orch.rescreen_calls == []
        assert len(orch.create_calls) == 2  # company + Jane both create-and-screen
        assert "rescreen_summary" not in report

    def test_combine_reports_persists_harvested_maps_per_subject(self):
        from screening_complyadvantage.adapter import _combine_reports

        def _subject_report(kind, person_key, harvested):
            report = {
                "provider": "complyadvantage",
                "normalized_version": "2.0",
                "screened_at": "2026-07-19T00:00:00Z",
                "company_screening_coverage": "full" if kind == "entity" else "none",
                "has_company_screening_hit": None,
                "company_screening": {"api_status": "live", "matched": False, "results": []} if kind == "entity" else {},
                "director_screenings": [],
                "ubo_screenings": [],
                "intermediary_screenings": [],
                "overall_flags": [],
                "total_hits": 0,
                "degraded_sources": [],
                "provider_specific": {"complyadvantage": {
                    "screening_subject": {"kind": kind, "person_key": person_key},
                }},
            }
            if harvested:
                report["customer_identifier_conflict_existing_customers"] = harvested
            return report

        combined = _combine_reports([
            _subject_report("entity", None, {"strict": "uuid-h-ent"}),
            _subject_report("director", "p1", {"strict": "uuid-h-p1-s", "relaxed": "uuid-h-p1-r"}),
            _subject_report("director", "p2", None),
        ])
        assert combined["customer_identifier_conflict_existing_customers"] == {
            "entity": {"strict": "uuid-h-ent"},
            "p1": {"strict": "uuid-h-p1-s", "relaxed": "uuid-h-p1-r"},
        }
        # Round trip: the combined (stored) report answers the reader.
        assert harvested_conflict_customer_identifiers(combined, "director", "p1") == {
            "strict": "uuid-h-p1-s", "relaxed": "uuid-h-p1-r",
        }
        assert harvested_conflict_customer_identifiers(combined, "entity", None) == {
            "strict": "uuid-h-ent",
        }

    def test_combine_reports_without_conflicts_adds_no_key(self):
        from screening_complyadvantage.adapter import _combine_reports
        combined = _combine_reports([{
            "provider": "complyadvantage",
            "company_screening_coverage": "none",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "intermediary_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
            "provider_specific": {"complyadvantage": {}},
        }])
        assert "customer_identifier_conflict_existing_customers" not in combined


# ══════════════════════════════════════════════════════════
# Mockup-gap items (founder-flagged 2026-07-19) — static UI pins
# ══════════════════════════════════════════════════════════

class TestMockupGapStaticPins:
    @pytest.fixture(scope="class")
    def html(self):
        with open(BACKOFFICE_HTML, encoding="utf-8") as fh:
            return fh.read()

    # ── Item 1: header "⋯" menu ──

    def test_header_menu_exists_and_reuses_existing_export_action(self, html):
        menu = _extract_js_function(html, "screeningEntityHeaderMenuHtml")
        assert 'data-screening-entity-header-menu="true"' in menu
        assert "Export screening pack" in menu
        assert 'onclick="downloadScreeningReportPDF()"' in menu, \
            "the menu must reuse the page's existing screening-report export"
        # Native <details> menu idiom shared with screeningTriageHitActions.
        assert "<details" in menu and "<summary" in menu
        assert "event.key===\\'Escape\\'" in menu

        entity_card = _extract_js_function(html, "buildEntityScreeningReviewCard")
        assert "screeningEntityHeaderMenuHtml()" in entity_card

        # The existing export function is untouched and still wired.
        existing = _extract_js_function(html, "screeningReportDownloadButtonHtml")
        assert 'onclick="downloadScreeningReportPDF()"' in existing

    def test_header_menu_has_no_dead_history_button(self, html):
        menu = _extract_js_function(html, "screeningEntityHeaderMenuHtml")
        assert menu.count("onclick=") == 1, "exactly one wired action — no dead buttons"
        assert ">View screening history<" not in html, \
            "no dead history button until the archive viewer exists"

    # ── Item 2: freshness line ──

    def test_freshness_line_gated_on_data_presence(self, html):
        fn = _extract_js_function(html, "screeningFreshnessLineHtml")
        assert 'data-screening-freshness-line="true"' in fn
        assert "if (!screenedAt) return '';" in fn, "render only when freshness data exists"
        assert "'Screened ' + fmt(screenedAt)" in fn
        assert "screeningSummary.screening_valid_until" in fn
        assert "' · valid until ' + fmt(" in fn
        assert "escapeHtml(line)" in fn, "escapeHtml discipline"
        # Page-wide timestamp formatting idiom.
        assert "replace('T', ' ').slice(0, 19)" in fn

        entity_card = _extract_js_function(html, "buildEntityScreeningReviewCard")
        assert "screeningFreshnessLineHtml(screeningSummary)" in entity_card

    def test_freshness_fields_match_server_metadata(self, html):
        # Server writes report.screened_at + prescreening.last_screened_at /
        # screening_valid_until (populate_screening_freshness_metadata); the
        # summary builder maps them onto screened_at / screening_valid_until.
        with open(os.path.join(BACKEND_DIR, "screening_freshness_metadata.py"), encoding="utf-8") as fh:
            server_side = fh.read()
        assert 'prescreening["last_screened_at"]' in server_side
        assert 'prescreening["screening_valid_until"]' in server_side
        summary_fn = _extract_js_function(html, "getApplicationScreeningSummary")
        assert "last_screened_at" in summary_fn
        assert "screening_valid_until" in summary_fn

    # ── Item 3: per-bucket hit cap ──

    def test_bucket_cap_with_honest_expander(self, html):
        assert "var SCREENING_TRIAGE_BUCKET_VISIBLE_LIMIT = 5;" in html
        sections = _extract_js_function(html, "screeningTriageRankedHitSections")
        # Per-hit redesign: near-identical duplicate runs fold into a grouped
        # block first (one representative kept); the remaining non-grouped cards
        # (normalCards) are then capped at the visible limit with the same honest
        # overflow expander — nothing hidden permanently, nothing re-ranked.
        assert "normalCards.slice(0, SCREENING_TRIAGE_BUCKET_VISIBLE_LIMIT)" in sections
        assert "normalCards.slice(SCREENING_TRIAGE_BUCKET_VISIBLE_LIMIT)" in sections
        assert 'data-screening-triage-bucket-overflow="' in sections
        assert "' more — show all (ranked)'" in sections, "expander must state the exact overflow"
        # Native <details> idiom (same as the weak tail) — every hit stays
        # reviewable after expanding.
        overflow_start = sections.index("data-screening-triage-bucket-overflow")
        overflow_block = sections[sections.rindex("<details", 0, overflow_start):sections.index("</details>", overflow_start)]
        assert "screeningTriageHitCard(row, entry.item, entry.index, weakThreshold)" in overflow_block

    def test_bucket_header_counts_stay_full_server_numbers(self, html):
        sections = _extract_js_function(html, "screeningTriageRankedHitSections")
        assert "var serverCount = buckets[meta.key] != null ? Number(buckets[meta.key]) : 0;" in sections
        assert "String(serverCount) + ' matches'" in sections, \
            "header count must remain the full server-computed number, not the capped count"

    def test_weak_tail_section_unchanged(self, html):
        tail = _extract_js_function(html, "screeningTriageWeakTailSection")
        assert 'data-screening-triage-weak-tail="true"' in tail
        assert "weak name-only matches — below triage threshold" in tail
        assert "SCREENING_TRIAGE_BUCKET_VISIBLE_LIMIT" not in tail, "weak tail is not capped"

    # ── Shared: banned vocabulary in new officer-facing strings ──

    def test_no_banned_vocabulary_in_new_officer_strings(self, html):
        menu = _extract_js_function(html, "screeningEntityHeaderMenuHtml")
        freshness = _extract_js_function(html, "screeningFreshnessLineHtml")
        sections = _extract_js_function(html, "screeningTriageRankedHitSections")
        officer_strings = [
            "Export screening pack",
            "More screening actions",
            "'Screened ' + fmt(screenedAt)",
            "' · valid until ' + fmt(",
            "' more — show all (ranked)'",
        ]
        corpus = menu + freshness + sections
        for text in officer_strings:
            assert text in corpus
            lowered = text.lower()
            for banned in ("%", "percent", "confidence", "probability"):
                assert banned not in lowered, f"banned vocabulary {banned!r} in {text!r}"
        # The new functions introduce no probabilistic vocabulary anywhere.
        for banned in ("percent", "confidence", "probability"):
            assert banned not in (menu + freshness).lower()

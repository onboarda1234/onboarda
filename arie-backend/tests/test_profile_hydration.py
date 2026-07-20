"""Phase G — on-demand ComplyAdvantage profile hydration.

Pins:
* extract_profile_attributes: documented-shape person profile → lean attribute
  dict incl. watchlist_entries; empty/absent fields omitted; company profile
  handled (no invented person attributes).
* hydrate_alert_profiles: pages the risks endpoint and is best-effort (a raising
  client yields a partial dict, never throws).
* client.get_alert_risks: hits the documented Mesh path with paging params.
* flag: default False in every environment; env override honoured.
* endpoint: flag OFF → 409 no-op, client never called; flag ON → attributes
  merged ADDITIVELY onto stored hits, report persisted, audit event emitted,
  risk / triage fields untouched.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENVIRONMENT", "testing")

from screening_complyadvantage.profile_hydration import (
    extract_profile_attributes,
    hydrate_alert_profiles,
)


# ══════════════════════════════════════════════════════════
# Documented-shape sample (Jan Marsalek style — person + watchlists)
# ══════════════════════════════════════════════════════════

def _marsalek_risk():
    return {
        "detail": {
            "profile": {
                "identifier": "PROF-JM-1",
                "match_score": 0.98,
                "match_types": ["name_exact"],
                "matching_name": "Jan Marsalek",
                "person": {
                    "names": [
                        {"type": "PRIMARY", "name": "Jan Marsalek"},
                        {"type": "AKA", "name": "Jan Marsalik"},
                    ],
                    "dates_of_birth": [{"source": "BKA", "value": "1980-03-15"}],
                    "places_of_birth": [{"source": "BKA", "value": "Vienna, Austria"}],
                    "fields": [
                        {"name": "Nationality", "tag": "nationality", "value": "Austrian"},
                        {"name": "Country", "tag": "country", "value": "Austria"},
                        {"name": "Position", "tag": "position", "value": "Former COO Wirecard"},
                    ],
                    "images": [{"url": "https://example.test/jm.jpg"}],
                },
            }
        },
        "sources": [
            {"name": "Germany BKA Wanted Persons", "listed_date": "4 September 2020",
             "related_urls": ["https://bka.example/jm"]},
            {"list_name": "Interpol Red Notice", "date": "2020-08-01",
             "url": "https://interpol.example/jm"},
            {"title": "EU Most Wanted", "reference": "no-url-ref"},
        ],
    }


# ── extract_profile_attributes ──

def test_extract_person_profile_returns_lean_attributes():
    attrs = extract_profile_attributes(_marsalek_risk())
    assert attrs["date_of_birth"] == "1980-03-15"
    assert attrs["places_of_birth"] == ["Vienna, Austria"]
    assert attrs["nationality"] == "Austrian"
    assert "Austria" in attrs["countries"]
    assert attrs["positions"] == ["Former COO Wirecard"]
    assert attrs["aka_names"] == ["Jan Marsalik"]
    assert attrs["image_urls"] == ["https://example.test/jm.jpg"]


def test_extract_watchlist_entries_from_sources():
    attrs = extract_profile_attributes(_marsalek_risk())
    entries = attrs["watchlist_entries"]
    names = [e.get("list_name") for e in entries]
    assert "Germany BKA Wanted Persons" in names
    assert "Interpol Red Notice" in names
    assert "EU Most Wanted" in names
    bka = next(e for e in entries if e["list_name"] == "Germany BKA Wanted Persons")
    assert bka["listed_date"] == "4 September 2020"
    assert bka["related_urls"] == ["https://bka.example/jm"]
    # EU Most Wanted has only a non-http reference → no related_urls fabricated.
    eu = next(e for e in entries if e["list_name"] == "EU Most Wanted")
    assert "related_urls" not in eu


def test_extract_omits_absent_fields():
    risk = {"detail": {"profile": {"identifier": "P", "person": {
        "names": [{"type": "PRIMARY", "name": "Someone"}],
    }}}}
    attrs = extract_profile_attributes(risk)
    assert "date_of_birth" not in attrs
    assert "nationality" not in attrs
    assert "positions" not in attrs
    assert "watchlist_entries" not in attrs
    # A PRIMARY-only name yields no aka_names.
    assert "aka_names" not in attrs


def test_extract_company_profile_has_no_person_attributes():
    risk = {
        "detail": {"profile": {"identifier": "C1", "company": {"name": "Acme Ltd"}}},
        "sources": [{"name": "OFAC SDN", "listed": "2019"}],
    }
    attrs = extract_profile_attributes(risk)
    assert "date_of_birth" not in attrs
    assert "nationality" not in attrs
    assert "positions" not in attrs
    # Watchlist metadata still surfaces for a company.
    assert attrs["watchlist_entries"][0]["list_name"] == "OFAC SDN"


def test_extract_accepts_bare_profile():
    profile = _marsalek_risk()["detail"]["profile"]
    attrs = extract_profile_attributes(profile)
    assert attrs["date_of_birth"] == "1980-03-15"


# ── hydrate_alert_profiles ──

class _FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_alert_risks(self, alert_identifier, *, page_number=1, page_size=100):
        self.calls.append((alert_identifier, page_number, page_size))
        return self.pages[page_number - 1]


def test_hydrate_pages_until_no_next():
    pages = [
        {"risks": [_marsalek_risk()], "next": "tok"},
        {"risks": [], "next": None},
    ]
    client = _FakeClient(pages)
    result = hydrate_alert_profiles(client, "ALERT-1")
    assert list(result.keys()) == ["PROF-JM-1"]
    assert len(client.calls) == 2
    assert result["PROF-JM-1"]["date_of_birth"] == "1980-03-15"


def test_hydrate_filters_by_wanted_profile_ids():
    other = _marsalek_risk()
    other["detail"]["profile"]["identifier"] = "PROF-OTHER"
    pages = [{"risks": [_marsalek_risk(), other], "next": None}]
    client = _FakeClient(pages)
    result = hydrate_alert_profiles(client, "ALERT-1", wanted_profile_ids=["PROF-JM-1"])
    assert list(result.keys()) == ["PROF-JM-1"]


def test_hydrate_respects_max_pages():
    pages = [{"risks": [_marsalek_risk()], "next": "always"} for _ in range(10)]
    client = _FakeClient(pages)
    hydrate_alert_profiles(client, "ALERT-1", max_pages=2)
    assert len(client.calls) == 2


def test_hydrate_best_effort_on_client_error():
    class _RaisingClient:
        def get_alert_risks(self, *a, **k):
            raise RuntimeError("boom")

    result = hydrate_alert_profiles(_RaisingClient(), "ALERT-1")
    assert result == {}  # partial (empty) dict, no exception


def test_hydrate_partial_when_second_page_fails():
    class _PartialClient:
        def __init__(self):
            self.calls = 0

        def get_alert_risks(self, alert_identifier, *, page_number=1, page_size=100):
            self.calls += 1
            if page_number == 1:
                return {"risks": [_marsalek_risk()], "next": "tok"}
            raise RuntimeError("boom on page 2")

    result = hydrate_alert_profiles(_PartialClient(), "ALERT-1")
    assert "PROF-JM-1" in result  # page 1 kept despite page 2 failure


# ── client.get_alert_risks ──

def test_client_get_alert_risks_uses_documented_path_and_params():
    from screening_complyadvantage.client import ComplyAdvantageClient
    client = ComplyAdvantageClient.__new__(ComplyAdvantageClient)
    client.get = MagicMock(return_value={"risks": []})
    client.get_alert_risks("ALERT-XYZ", page_number=2, page_size=50)
    client.get.assert_called_once_with(
        "/v2/alerts/ALERT-XYZ/risks",
        params={"page_number": 2, "page_size": 50},
    )


# ── flag ──

def test_flag_defaults_false_all_environments(monkeypatch):
    from screening_config import is_ca_profile_hydration_enabled
    monkeypatch.delenv("ENABLE_CA_PROFILE_HYDRATION", raising=False)
    for env in ("development", "testing", "demo", "staging", "production"):
        monkeypatch.setenv("ENVIRONMENT", env)
        assert is_ca_profile_hydration_enabled() is False


def test_flag_env_override(monkeypatch):
    from screening_config import is_ca_profile_hydration_enabled
    monkeypatch.setenv("ENABLE_CA_PROFILE_HYDRATION", "true")
    assert is_ca_profile_hydration_enabled() is True
    monkeypatch.setenv("ENABLE_CA_PROFILE_HYDRATION", "false")
    assert is_ca_profile_hydration_enabled() is False


# ── merge helper (additive-only, never touches risk/triage) ──

def test_merge_is_additive_and_preserves_existing():
    import server
    report = {
        "director_screenings": [{
            "person_name": "Jan Marsalek",
            "screening": {"results": [{
                "provider_profile_identifier": "PROF-JM-1",
                "name": "Jan Marsalek",
                "triage_score": 42,
                "match_category": "Adverse Media",
                "nationality": "PRE-EXISTING",  # must NOT be overwritten
            }]},
        }],
    }
    attr_map = {"PROF-JM-1": {
        "date_of_birth": "1980-03-15",
        "nationality": "Austrian",  # additive attempt — existing wins
        "watchlist_entries": [{"list_name": "Interpol Red Notice"}],
    }}
    enriched = server._merge_hydrated_attributes_into_report(report, attr_map)
    result = report["director_screenings"][0]["screening"]["results"][0]
    assert enriched == 1
    assert result["date_of_birth"] == "1980-03-15"  # added
    assert result["nationality"] == "PRE-EXISTING"  # NOT overwritten
    assert result["watchlist_entries"][0]["list_name"] == "Interpol Red Notice"
    # Risk / triage / category untouched.
    assert result["triage_score"] == 42
    assert result["match_category"] == "Adverse Media"


def test_merge_ignores_unmatched_profile_ids():
    import server
    report = {"director_screenings": [{"screening": {"results": [
        {"provider_profile_identifier": "PROF-A", "name": "A"},
    ]}}]}
    enriched = server._merge_hydrated_attributes_into_report(
        report, {"PROF-Z": {"date_of_birth": "2000-01-01"}})
    assert enriched == 0
    assert "date_of_birth" not in report["director_screenings"][0]["screening"]["results"][0]


# ══════════════════════════════════════════════════════════
# Endpoint — flag gating, merge/persist, audit, risk untouched
# ══════════════════════════════════════════════════════════

def _make_hydration_handler(body):
    from server import ProfileHydrationHandler
    from tornado.web import Application
    from tornado.httputil import HTTPServerRequest, HTTPHeaders

    app = Application()
    mock_conn = MagicMock()
    mock_conn.context = MagicMock()
    mock_conn.context.remote_ip = "127.0.0.1"
    mock_conn.no_keep_alive = False
    req = HTTPServerRequest(
        method="POST", uri="/api/screening/hydrate-profiles", version="HTTP/1.1",
        headers=HTTPHeaders({}), body=b"", host="127.0.0.1", connection=mock_conn,
    )
    handler = ProfileHydrationHandler(app, req)
    handler.require_auth = MagicMock(return_value={"sub": "officer-1", "name": "Officer", "role": "admin"})
    handler.check_rate_limit = MagicMock(return_value=True)
    handler.get_json = MagicMock(return_value=body)
    handler.get_client_ip = MagicMock(return_value="127.0.0.1")
    captured = {}
    handler.success = lambda data, status=200: captured.__setitem__("success", data)
    handler.error = lambda message, status=400: captured.__setitem__("error", (message, status))
    handler._status_written = []
    handler.set_status = lambda code: handler._status_written.append(code)
    handler.write = lambda payload: captured.__setitem__("write", payload)
    return handler, captured


def _seed_app_with_hit(db, app_id="app-hydg-1"):
    report = {
        "screened_at": "2026-01-01T00:00:00",
        "total_hits": 1,
        "director_screenings": [{
            "person_name": "Jan Marsalek",
            "screening": {"provider": "complyadvantage", "results": [{
                "provider_profile_identifier": "PROF-JM-1",
                "provider_alert_identifier": "ALERT-1",
                "name": "Jan Marsalek",
                "match_category": "Watchlist",
                "triage_score": 55,
            }]},
        }],
        "ubo_screenings": [],
    }
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, "ARF-" + app_id, "client-hydg", "Wirecard Successor Ltd", "MU", "Fintech",
         "SME", "pricing_review", json.dumps({"screening_report": report})),
    )
    db.commit()
    return app_id


def test_endpoint_flag_off_is_noop_and_never_calls_client(db, temp_db, monkeypatch):
    import server
    _seed_app_with_hit(db, "app-hydg-off")
    monkeypatch.setattr(server, "safe_json_loads", server.safe_json_loads)
    from screening_config import is_ca_profile_hydration_enabled  # noqa: F401
    monkeypatch.setenv("ENABLE_CA_PROFILE_HYDRATION", "false")

    def _explode(*a, **k):
        raise AssertionError("client must not be constructed when flag is off")
    monkeypatch.setattr("screening_complyadvantage.profile_hydration.hydrate_alert_profiles", _explode)

    handler, captured = _make_hydration_handler({
        "application_id": "app-hydg-off",
        "subject": {"subject_type": "director", "subject_name": "Jan Marsalek"},
        "alert_identifiers": ["ALERT-1"],
        "profile_identifiers": ["PROF-JM-1"],
    })
    handler.post()
    assert 409 in handler._status_written
    assert json.loads(captured["write"])["hydration_enabled"] is False


def test_endpoint_flag_on_merges_persists_and_audits(db, temp_db, monkeypatch):
    import server
    app_id = _seed_app_with_hit(db, "app-hydg-on")
    monkeypatch.setenv("ENABLE_CA_PROFILE_HYDRATION", "true")
    monkeypatch.setattr(server, "_profile_hydration_ui_enabled", lambda: True)
    # Force both endpoint gate conditions on.
    import screening_config
    monkeypatch.setattr(screening_config, "is_complyadvantage_active", lambda: True)
    monkeypatch.setattr(server, "_ca_screening_audit_enabled", lambda: True)

    monkeypatch.setattr(
        "screening_complyadvantage.config.CAConfig.from_env",
        classmethod(lambda cls: MagicMock()),
    )
    monkeypatch.setattr(
        "screening_complyadvantage.client.ComplyAdvantageClient.__init__",
        lambda self, config: None,
    )
    hydrated = {"PROF-JM-1": {
        "date_of_birth": "1980-03-15",
        "nationality": "Austrian",
        "watchlist_entries": [{"list_name": "Interpol Red Notice", "listed_date": "2020-08-01",
                               "related_urls": ["https://interpol.example/jm"]}],
    }}
    monkeypatch.setattr(
        "screening_complyadvantage.profile_hydration.hydrate_alert_profiles",
        lambda client, alert_identifier, **k: dict(hydrated),
    )

    handler, captured = _make_hydration_handler({
        "application_id": app_id,
        "subject": {"subject_type": "director", "subject_name": "Jan Marsalek"},
        "alert_identifiers": ["ALERT-1"],
        "profile_identifiers": ["PROF-JM-1"],
    })
    handler.post()

    data = captured["success"]
    assert data["hydration_enabled"] is True
    assert data["hits_enriched"] == 1
    assert data["attributes"]["PROF-JM-1"]["date_of_birth"] == "1980-03-15"

    # Persisted onto the stored report, additively; risk/triage untouched.
    row = db.execute("SELECT prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
    stored = json.loads(row["prescreening_data"])
    hit = stored["screening_report"]["director_screenings"][0]["screening"]["results"][0]
    assert hit["date_of_birth"] == "1980-03-15"
    assert hit["watchlist_entries"][0]["list_name"] == "Interpol Red Notice"
    assert hit["triage_score"] == 55  # untouched
    assert hit["match_category"] == "Watchlist"  # untouched

    # Audit event emitted.
    audit = db.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action=?",
        ("ca_screening.profiles_hydrated",),
    ).fetchone()
    assert audit["n"] >= 1

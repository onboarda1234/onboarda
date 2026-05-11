import json
import sqlite3

import pytest

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME, SUMSUB_PROVIDER_NAME
from screening_shadow import (
    build_provider_comparison,
    maybe_schedule_shadow_screening,
    run_shadow_screening_now,
)


def _raw_sumsub_report(*, hit=False, category="pep", total_hits=None):
    report = {
        "company_screening": {
            "sanctions": {"matched": False, "results": []},
            "adverse_media": {"matched": False, "results": []},
        },
        "director_screenings": [],
        "ubo_screenings": [],
        "total_hits": total_hits if total_hits is not None else (1 if hit else 0),
    }
    if hit and category == "pep":
        report["director_screenings"].append(
            {
                "full_name": "Test Person",
                "declared_pep": "No",
                "screening": {
                    "matched": True,
                    "results": [{"is_pep": True, "name": "Test Person"}],
                },
            }
        )
    elif hit and category == "media":
        report["company_screening"]["adverse_media"] = {
            "matched": True,
            "results": [{"title": "Media hit"}],
        }
    return report


def _ca_report(*, hit=False, category="pep", total_hits=None):
    report = {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "normalized_version": "2.0",
        "source_screening_report_hash": f"ca-{category}-{hit}-{total_hits}",
        "company_screening": {
            "sanctions": {"matched": False, "results": []},
            "adverse_media": {"matched": False, "results": []},
        },
        "director_screenings": [],
        "ubo_screenings": [],
        "total_hits": total_hits if total_hits is not None else (1 if hit else 0),
    }
    if hit and category == "pep":
        report["any_pep_hits"] = True
        report["director_screenings"].append(
            {
                "full_name": "Test Person",
                "provider_detected_pep": True,
                "screening": {
                    "matched": True,
                    "results": [{"is_pep": True, "name": "Test Person"}],
                },
            }
        )
    elif hit and category == "media":
        report["has_adverse_media_hit"] = True
        report["company_screening"]["adverse_media"] = {
            "matched": True,
            "results": [{"title": "Media hit"}],
        }
    return report


def test_maybe_schedule_shadow_requires_sumsub_primary_and_ca_shadow(monkeypatch):
    monkeypatch.setattr("screening_shadow.get_active_provider_name", lambda: SUMSUB_PROVIDER_NAME)
    monkeypatch.setattr("screening_shadow.get_shadow_provider_name", lambda: COMPLYADVANTAGE_PROVIDER_NAME)
    scheduled = []

    def scheduler(fn, *args):
        scheduled.append((fn, args))
        return "future"

    result = maybe_schedule_shadow_screening(
        {"application_id": "app-1", "client_id": "client-1"},
        [{"full_name": "Director"}],
        [{"full_name": "Owner"}],
        {"total_hits": 0},
        client_ip="203.0.113.10",
        scheduler=scheduler,
    )

    assert result == "future"
    assert len(scheduled) == 1
    assert scheduled[0][1][0]["application_id"] == "app-1"
    assert scheduled[0][1][3] == {"total_hits": 0}


@pytest.mark.parametrize(
    ("active_provider", "shadow_provider"),
    [
        (SUMSUB_PROVIDER_NAME, None),
        (COMPLYADVANTAGE_PROVIDER_NAME, COMPLYADVANTAGE_PROVIDER_NAME),
    ],
)
def test_maybe_schedule_shadow_noops_when_not_d2(monkeypatch, active_provider, shadow_provider):
    monkeypatch.setattr("screening_shadow.get_active_provider_name", lambda: active_provider)
    monkeypatch.setattr("screening_shadow.get_shadow_provider_name", lambda: shadow_provider)

    result = maybe_schedule_shadow_screening(
        {"application_id": "app-1", "client_id": "client-1"},
        [],
        [],
        {"total_hits": 0},
        scheduler=lambda *args: pytest.fail("shadow should not be scheduled"),
    )

    assert result is None


def test_run_shadow_persists_ca_truth_comparison_and_skips_agent7(tmp_path, monkeypatch):
    db_path = tmp_path / "shadow.db"
    setup = sqlite3.connect(db_path)
    setup.execute("CREATE TABLE monitoring_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, case_identifier TEXT)")
    setup.commit()
    setup.close()
    events = []

    import screening_complyadvantage.observability as observability

    monkeypatch.setattr(observability, "emit_metric", lambda event_name, **kwargs: events.append((event_name, kwargs)))
    monkeypatch.setattr(observability, "emit_audit", lambda event_name, **kwargs: events.append((event_name, kwargs)))

    class FakeAdapter:
        def __init__(self):
            self.db = None
            self.monitoring_enabled = False

        def run_full_screening(self, application_data, directors, ubos, client_ip=None):
            assert client_ip == "203.0.113.20"
            return _ca_report(hit=True, category="pep")

    def db_factory():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    result = run_shadow_screening_now(
        {"application_id": "app-shadow", "client_id": "client-shadow"},
        [{"full_name": "Director"}],
        [],
        _raw_sumsub_report(hit=False),
        client_ip="203.0.113.20",
        db_factory=db_factory,
        adapter_factory=FakeAdapter,
    )

    assert result["mismatch_class"] == "ca_only"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, source FROM screening_reports_normalized ORDER BY provider"
    ).fetchall()
    assert [(row["provider"], row["source"]) for row in rows] == [
        (COMPLYADVANTAGE_PROVIDER_NAME, "d2_shadow"),
        (SUMSUB_PROVIDER_NAME, "d2_primary"),
    ]
    comparison = conn.execute(
        "SELECT * FROM screening_provider_comparisons WHERE application_id='app-shadow'"
    ).fetchone()
    assert comparison["primary_provider"] == SUMSUB_PROVIDER_NAME
    assert comparison["shadow_provider"] == COMPLYADVANTAGE_PROVIDER_NAME
    assert comparison["mismatch_class"] == "ca_only"
    payload = json.loads(comparison["comparison_json"])
    assert payload["shadow"]["categories"]["pep"] is True
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0
    conn.close()
    assert "shadow_agent7_skipped" in [event[0] for event in events]


def test_shadow_comparison_is_idempotent_for_same_application_pair(tmp_path):
    db_path = tmp_path / "shadow-idempotent.db"

    class FakeAdapter:
        def run_full_screening(self, application_data, directors, ubos, client_ip=None):
            return _ca_report(hit=True, category="pep")

    def db_factory():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    args = (
        {"application_id": "app-idem", "client_id": "client-idem"},
        [],
        [],
        _raw_sumsub_report(hit=False),
    )
    first = run_shadow_screening_now(*args, db_factory=db_factory, adapter_factory=FakeAdapter)
    second = run_shadow_screening_now(*args, db_factory=db_factory, adapter_factory=FakeAdapter)

    conn = sqlite3.connect(db_path)
    assert first["comparison_id"] == second["comparison_id"]
    assert conn.execute("SELECT COUNT(*) FROM screening_provider_comparisons").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 2
    conn.close()


def test_provider_comparison_exact_match():
    comparison = build_provider_comparison(_raw_sumsub_report(hit=False), _ca_report(hit=False))

    assert comparison["mismatch_class"] == "exact_match"


def test_provider_comparison_ca_only_hit():
    comparison = build_provider_comparison(_raw_sumsub_report(hit=False), _ca_report(hit=True, category="pep"))

    assert comparison["mismatch_class"] == "ca_only"


def test_provider_comparison_sumsub_only_hit():
    comparison = build_provider_comparison(_raw_sumsub_report(hit=True, category="pep"), _ca_report(hit=False))

    assert comparison["mismatch_class"] == "sumsub_only"


def test_provider_comparison_category_delta():
    comparison = build_provider_comparison(
        _raw_sumsub_report(hit=True, category="pep", total_hits=1),
        _ca_report(hit=True, category="media", total_hits=1),
    )

    assert comparison["mismatch_class"] == "category_delta"
    assert set(comparison["deltas"]["categories"]) == {"media", "pep"}


def test_provider_comparison_count_delta():
    comparison = build_provider_comparison(
        _raw_sumsub_report(hit=True, category="pep", total_hits=1),
        _ca_report(hit=True, category="pep", total_hits=2),
    )

    assert comparison["mismatch_class"] == "count_delta"
    assert comparison["deltas"]["total_hits"] == 1

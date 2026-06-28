from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "arie-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _cm_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            company_name TEXT,
            is_fixture INTEGER DEFAULT 0
        );
        CREATE TABLE change_requests (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            status TEXT,
            materiality TEXT,
            source TEXT,
            reason TEXT,
            created_at TEXT,
            screening_required INTEGER DEFAULT 0,
            risk_review_required INTEGER DEFAULT 0,
            edd_review_required INTEGER DEFAULT 0,
            memo_addendum_hook INTEGER DEFAULT 0,
            periodic_review_acceleration_hook INTEGER DEFAULT 0
        );
        CREATE TABLE change_request_items (
            id TEXT PRIMARY KEY,
            request_id TEXT,
            change_type TEXT,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            materiality TEXT,
            person_action TEXT,
            person_snapshot TEXT,
            created_at TEXT
        );
        CREATE TABLE change_alerts (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            status TEXT,
            alert_type TEXT,
            source_channel TEXT,
            summary TEXT,
            detected_changes TEXT,
            source_reference TEXT,
            source_payload TEXT,
            detected_by TEXT,
            reviewer_notes TEXT,
            created_at TEXT
        );
        """
    )
    db.executemany(
        "INSERT INTO applications (id, ref, company_name, is_fixture) VALUES (?,?,?,?)",
        [
            ("pilot00000000001", "ARF-PILOT-001", "Pilot Client Ltd", 0),
            ("f1xed00000000001", "ARF-E2E-001", "Smoke Fixture Ltd", 1),
            ("uuidfixture0001", "ARF-SMOKE-002", "Historical Smoke Ltd", 1),
            ("uuidsmoke0002", "ARF-SMOKE-003", "Historical Smoke Unflagged Ltd", 0),
        ],
    )
    db.executemany(
        "INSERT INTO change_requests (id, application_id, status, materiality, source, reason, created_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("CR-PILOT", "pilot00000000001", "draft", "tier3", "manual", "pilot", "2026-06-01T00:00:00Z"),
            ("CR-FIX-ID", "f1xed00000000001", "draft", "tier3", "manual", "fixture id", "2026-06-02T00:00:00Z"),
            ("CR-FIX-FLAG", "uuidfixture0001", "draft", "tier3", "manual", "fixture flag", "2026-06-03T00:00:00Z"),
            ("CR-FIX-TEXT", "uuidsmoke0002", "draft", "tier3", "manual", "fixture text", "2026-06-04T00:00:00Z"),
        ],
    )
    db.executemany(
        "INSERT INTO change_alerts (id, application_id, status, alert_type, source_channel, summary, detected_changes, source_reference, source_payload, detected_by, reviewer_notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("CA-PILOT", "pilot00000000001", "new", "profile_change", "system", "pilot", "{}", "registry-pilot", "{}", "registry", "", "2026-06-01T00:00:00Z"),
            ("CA-FIX-ID", "f1xed00000000001", "new", "fixture", "system", "fixture id", "{}", "fixture-id", "{}", "system", "", "2026-06-02T00:00:00Z"),
            ("CA-FIX-FLAG", "uuidfixture0001", "new", "fixture", "system", "fixture flag", "{}", "fixture-flag", "{}", "system", "", "2026-06-03T00:00:00Z"),
            ("CA-FIX-TEXT", "uuidsmoke0002", "new", "fixture", "system", "fixture text", "{}", "fixture-text", "{}", "system", "", "2026-06-04T00:00:00Z"),
            ("CA-FIX-SOURCE", "pilot00000000001", "new", "profile_change", "registry_api", "CME2E harness alert", "{}", "CME2E-20260626-smoke", "{\"fixture\": true}", "cm-e2e-harness", "", "2026-06-05T00:00:00Z"),
        ],
    )
    db.commit()
    return db


def test_cm_broad_lists_hide_fixture_records_by_default_filter():
    import change_management as cm
    from fixture_filter import fixture_app_id_exclude_clause, fixture_change_alert_exclude_clause

    db = _cm_db()
    try:
        fixture_sql, fixture_params = fixture_app_id_exclude_clause(
            "application_id",
            include_text_patterns=True,
        )
        alert_fixture_sql, alert_fixture_params = fixture_change_alert_exclude_clause("application_id")

        requests = cm.list_change_requests(
            db,
            fixture_filter_sql=fixture_sql,
            fixture_filter_params=fixture_params,
        )
        alerts = cm.list_change_alerts(
            db,
            fixture_filter_sql=alert_fixture_sql,
            fixture_filter_params=alert_fixture_params,
        )

        assert [row["id"] for row in requests] == ["CR-PILOT"]
        assert [row["id"] for row in alerts] == ["CA-PILOT"]
    finally:
        db.close()


def test_cm_toggle_on_lists_include_fixture_records_with_labels():
    import change_management as cm

    db = _cm_db()
    try:
        requests = cm.list_change_requests(db)
        alerts = cm.list_change_alerts(db)

        assert {row["id"] for row in requests} == {"CR-PILOT", "CR-FIX-ID", "CR-FIX-FLAG", "CR-FIX-TEXT"}
        assert {row["id"] for row in alerts} == {"CA-PILOT", "CA-FIX-ID", "CA-FIX-FLAG", "CA-FIX-TEXT", "CA-FIX-SOURCE"}
        assert {row["id"]: row["is_fixture"] for row in requests}["CR-FIX-ID"] is True
        assert {row["id"]: row["is_fixture"] for row in requests}["CR-FIX-FLAG"] is True
        assert {row["id"]: row["is_fixture"] for row in alerts}["CA-FIX-ID"] is True
        assert {row["id"]: row["is_fixture"] for row in alerts}["CA-FIX-FLAG"] is True
    finally:
        db.close()


def test_cm_exact_application_lookup_is_not_hidden_by_queue_filter_static():
    server = _read("arie-backend/server.py")
    assert "if not show_fx and not application_id:" in server


def test_backoffice_has_internal_toggle_and_no_portal_toggle():
    html = _read("arie-backoffice.html")
    portal = _read("arie-portal.html")

    assert "fixture-record-toggle-wrap" in html
    assert "Show test/smoke records" in html
    assert "function canToggleTestSmokeRecords()" in html
    assert "role === 'admin' || role === 'sco'" in html
    assert "Show test/smoke records" not in portal


def test_backoffice_classifier_is_conservative_and_labels_visible_records():
    html = _read("arie-backoffice.html")
    classifier = html.split("var TEST_SMOKE_RECORD_TOGGLE_STORAGE_KEY", 1)[1].split(
        "// ═══════════════════════════════════════════════════════════\n"
        "// LINE-BY-LINE SUB-CRITERIA RISK COMPUTATION ENGINE",
        1,
    )[0]

    assert "function fixtureRecordInfo(record)" in html
    assert "/^f1xed/i" in classifier
    assert "/\\be2e\\b/i" in classifier
    assert "/\\bsmoke\\b/i" in classifier
    assert "/\\bfixture\\b/i" in classifier
    assert "/\\btest\\b/i" not in classifier
    assert "fixtureRecordBadgeHtml" in html
    assert "Test / Smoke" in html


def test_backoffice_list_calls_use_fixture_visibility_param():
    html = _read("arie-backoffice.html")

    assert "appendFixtureVisibilityParam(path)" in html
    assert "appendFixtureVisibilityParam('/dashboard')" in html
    assert "appendFixtureVisibilityParam('/monitoring/reviews')" in html
    assert "appendFixtureVisibilityParam('/monitoring/reviews?status=completion_pending_memo')" in html
    assert "appendFixtureVisibilityParam('/monitoring/reviews?status=completed')" in html
    assert "appendFixtureVisibilityParam('/change-management/requests?')" in html
    assert "appendFixtureVisibilityParam('/change-management/alerts')" in html


def test_backoffice_direct_detail_label_and_exact_search_hint():
    html = _read("arie-backoffice.html")

    assert 'id="detail-fixture-record-label"' in html
    assert "fixtureDetailLabel.style.display = fixtureDetailBadge ? 'block' : 'none';" in html
    assert "fixtureBadge + renderIncompleteSubmissionBadge" in html
    assert "No pilot application matches. Test/smoke records are hidden by default" in html
    assert "Turn on \"Show test/smoke records\"" in html

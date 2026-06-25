import json
import secrets
from pathlib import Path


ADMIN_USER = {"sub": "admin-alert-triage", "name": "Admin Alert Triage", "role": "admin"}
CO_USER = {"sub": "co-alert-triage", "name": "CO Alert Triage", "role": "co"}


class _DBWrapper:
    """Wrap raw sqlite3 connection to match the CM module DB API."""

    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


def _get_cm():
    import change_management as cm
    return cm


def _setup_application(raw_db):
    app_id = f"alert-triage-app-{secrets.token_hex(4)}"
    client_id = f"alert-triage-client-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.test", "hash", "Alert Triage Client Ltd"),
    )
    raw_db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            f"ALERT-{secrets.token_hex(3).upper()}",
            client_id,
            "Alert Triage Client Ltd",
            "Mauritius",
            "Financial Services",
            "Private Company",
            "approved",
        ),
    )
    raw_db.commit()
    return app_id


def _audit_sink(events):
    def _audit(user, action, target, detail, **kwargs):
        events.append(
            {
                "user": user,
                "action": action,
                "target": target,
                "detail": detail,
                "before_state": kwargs.get("before_state"),
                "after_state": kwargs.get("after_state"),
            }
        )

    return _audit


def _create_alert(cm, wdb, app_id, *, user=CO_USER, log_audit_fn=None):
    return cm.create_change_alert(
        wdb,
        application_id=app_id,
        alert_type="legal_name_change",
        source_channel="registry_api",
        summary="Registry detected a legal name change",
        detected_changes={"company_name": {"old": "Alert Triage Client Ltd", "new": "Alert Triage Client Global Ltd"}},
        confidence=0.94,
        user=user,
        log_audit_fn=log_audit_fn,
    )


def test_fresh_new_alert_can_convert_directly_without_begin_review(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id = _setup_application(db)
    events = []
    alert = _create_alert(cm, wdb, app_id, log_audit_fn=_audit_sink(events))

    request, err = cm.convert_alert_to_request(wdb, alert["id"], CO_USER, log_audit_fn=_audit_sink(events))

    assert request is not None, err
    assert request["source"] == "external_alert_conversion"
    assert request["source_alert_id"] == alert["id"]

    updated = cm.get_change_alert_detail(wdb, alert["id"])
    assert updated["status"] == "converted_to_change_request"
    assert updated["converted_request_id"] == request["id"]
    assert any(event["action"] == "Change Alert Converted" for event in events)


def test_direct_conversion_carries_old_new_values_into_request_items(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id = _setup_application(db)
    alert = _create_alert(cm, wdb, app_id)

    request, err = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)

    assert request is not None, err
    items = db.execute(
        "SELECT field_name, old_value, new_value FROM change_request_items WHERE request_id = ?",
        (request["id"],),
    ).fetchall()
    assert len(items) == 1
    item = dict(items[0])
    assert item["field_name"] == "company_name"
    assert json.loads(item["old_value"]) == "Alert Triage Client Ltd"
    assert json.loads(item["new_value"]) == "Alert Triage Client Global Ltd"


def test_opening_alert_review_start_is_audited(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id = _setup_application(db)
    events = []
    alert = _create_alert(cm, wdb, app_id)

    ok, err = cm.update_change_alert_status(
        wdb,
        alert["id"],
        "under_review",
        CO_USER,
        notes="Review opened from alert detail",
        log_audit_fn=_audit_sink(events),
    )

    assert ok, err
    assert any(event["action"] == "Change Alert Review Started" for event in events)
    detail = cm.get_change_alert_detail(wdb, alert["id"])
    assert detail["status"] == "under_review"


def test_duplicate_conversion_reuses_existing_linked_request(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id = _setup_application(db)
    alert = _create_alert(cm, wdb, app_id)

    first, err = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)
    assert first is not None, err
    second, err = cm.convert_alert_to_request(wdb, alert["id"], ADMIN_USER)

    assert second is not None, err
    assert second["id"] == first["id"]
    assert second["already_converted"] is True
    count = db.execute(
        "SELECT COUNT(*) AS c FROM change_requests WHERE source_alert_id = ?",
        (alert["id"],),
    ).fetchone()["c"]
    assert count == 1


def test_dismiss_and_escalate_require_reason_and_audit_when_supplied(db):
    cm = _get_cm()
    wdb = _DBWrapper(db)
    app_id = _setup_application(db)
    dismiss_alert = _create_alert(cm, wdb, app_id)
    escalate_alert = _create_alert(cm, wdb, app_id)
    events = []

    ok, err = cm.update_change_alert_status(wdb, dismiss_alert["id"], "dismissed", CO_USER)
    assert not ok
    assert "Notes are required" in err

    ok, err = cm.update_change_alert_status(
        wdb,
        dismiss_alert["id"],
        "dismissed",
        CO_USER,
        notes="False positive registry signal",
        log_audit_fn=_audit_sink(events),
    )
    assert ok, err

    ok, err = cm.update_change_alert_status(wdb, escalate_alert["id"], "escalated", CO_USER)
    assert not ok
    assert "Notes are required" in err

    ok, err = cm.update_change_alert_status(
        wdb,
        escalate_alert["id"],
        "escalated",
        CO_USER,
        notes="Needs senior review",
        log_audit_fn=_audit_sink(events),
    )
    assert ok, err
    assert sum(1 for event in events if event["action"] == "Change Alert Status Updated") == 2


def test_backoffice_alert_detail_static_contract():
    html = Path(__file__).resolve().parents[2].joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    assert "Create Change Request" in html
    assert "Dismiss Alert" in html
    assert "Escalate" in html
    assert "Begin Review" not in html
    assert "maybeStartAlertReview(a)" in html
    assert "Technical details" in html
    assert "Detected Change" in html
    assert "openChangeRequestFromApplication(r.id)" in html

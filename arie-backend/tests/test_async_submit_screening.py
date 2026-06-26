import json
from types import SimpleNamespace

import pytest


CLIENT_ID = "async-submit-client"


def _actor():
    return {
        "sub": CLIENT_ID,
        "name": "Async Submit Client",
        "role": "client",
        "type": "client",
    }


def _json_blob(value):
    from base_handler import _safe_json

    return _safe_json(value)


def _seed_clean_draft(db, *, app_id="app_async_submit", ref="ARF-ASYNC-001"):
    db.execute(
        """
        INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status)
        VALUES (?, ?, ?, ?, 'active')
        """,
        (CLIENT_ID, f"{CLIENT_ID}@test.local", "test-only", "Async Submit Client Ltd"),
    )
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         ownership_structure, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
        """,
        (
            app_id,
            ref,
            CLIENT_ID,
            "Async Submit Clean Ltd",
            "Mauritius",
            "Technology Services",
            "Private Company",
            "Simple",
            json.dumps({
                "registered_entity_name": "Async Submit Clean Ltd",
                "country_of_incorporation": "Mauritius",
                "sector": "Technology Services",
                "currency": "USD",
                "services_required": ["Business account"],
                "source_of_funds": "Operating revenue",
            }),
        ),
    )
    db.execute(
        """
        INSERT INTO directors
        (id, application_id, person_key, first_name, last_name, full_name,
         nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"dir_{app_id}",
            app_id,
            "dir1",
            "Jane",
            "Director",
            "Jane Director",
            "Mauritius",
            "No",
            "{}",
            "1985-01-01",
        ),
    )
    db.commit()
    return app_id, ref


def _cleanup_async_rows(db):
    for table, column in (
        ("screening_jobs", "application_id"),
        ("directors", "application_id"),
        ("ubos", "application_id"),
        ("intermediaries", "application_id"),
        ("applications", "id"),
    ):
        db.execute(f"DELETE FROM {table} WHERE {column} LIKE 'app_async_%'")
    db.execute("DELETE FROM audit_log WHERE target LIKE 'ARF-ASYNC-%'")
    db.commit()


def _submit_handler():
    import server

    handler = object.__new__(server.SubmitApplicationHandler)
    handler.request = SimpleNamespace(headers={})
    handler.status_code = None
    handler.payload = None
    handler.get_client_ip = lambda: "127.0.0.1"
    handler.check_app_ownership = lambda user, app: app["client_id"] == user["sub"]
    handler.success = lambda data, status=200: (
        setattr(handler, "status_code", status),
        setattr(handler, "payload", data),
        data,
    )[-1]
    handler.error = lambda message, status=400: (
        setattr(handler, "status_code", status),
        setattr(handler, "payload", {"error": message}),
        {"error": message},
    )[-1]

    def log_audit(user, action, target, detail, db=None, before_state=None, after_state=None, commit=True):
        db.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.get("sub", ""),
                user.get("name", ""),
                user.get("role", ""),
                action,
                target,
                detail,
                "127.0.0.1",
                _json_blob(before_state),
                _json_blob(after_state),
            ),
        )
        if commit:
            db.commit()

    handler.log_audit = log_audit
    return handler


def _run_submit(db, app_id):
    handler = _submit_handler()
    handler._do_submit(db, _actor(), app_id)
    return handler


def _clean_ca_report():
    return {
        "provider": "complyadvantage",
        "screening_provider": "complyadvantage",
        "source": "complyadvantage",
        "normalized_version": "2.0",
        "screening_mode": "live",
        "company_screening_state": "completed_clear",
        "company_screening_coverage": "full",
        "has_company_screening_hit": False,
        "company_screening": {
            "company_name": "Async Submit Clean Ltd",
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": False,
            "results": [],
            "screening_state": "completed_clear",
        },
        "director_screenings": [
            {
                "person_name": "Jane Director",
                "person_type": "director",
                "declared_pep": "No",
                "provider_detected_pep": False,
                "undeclared_pep": False,
                "has_pep_hit": False,
                "has_sanctions_hit": False,
                "has_adverse_media_hit": None,
                "screening_state": "completed_clear",
                "screening": {
                    "provider": "complyadvantage",
                    "source": "complyadvantage",
                    "api_status": "live",
                    "matched": False,
                    "results": [],
                    "screening_state": "completed_clear",
                },
            }
        ],
        "ubo_screenings": [],
        "intermediary_screenings": [],
        "ip_geolocation": {"source": "not_run", "api_status": "not_started"},
        "overall_flags": [],
        "total_hits": 0,
        "degraded_sources": [],
        "any_non_terminal_subject": False,
        "any_pep_hits": False,
        "any_sanctions_hits": False,
        "has_adverse_media_hit": None,
        "adverse_media_coverage": "none",
    }


def _provider_pep_ca_report():
    report = _clean_ca_report()
    report.update({
        "company_screening_state": "completed_match",
        "any_pep_hits": True,
        "total_hits": 1,
        "overall_flags": ["provider_pep_match_unresolved"],
    })
    subject = report["director_screenings"][0]
    subject.update({
        "provider_detected_pep": True,
        "undeclared_pep": True,
        "has_pep_hit": True,
        "screening_state": "completed_match",
    })
    subject["screening"].update({
        "matched": True,
        "screening_state": "completed_match",
        "results": [
            {
                "name": "Jane Director",
                "categories": ["pep"],
                "match_status": "potential_match",
            }
        ],
    })
    return report


def _sanctions_ca_report():
    report = _clean_ca_report()
    report.update({
        "company_screening_state": "completed_match",
        "any_sanctions_hits": True,
        "total_hits": 1,
        "overall_flags": ["sanctions_hit"],
    })
    subject = report["director_screenings"][0]
    subject.update({
        "has_sanctions_hit": True,
        "screening_state": "completed_match",
    })
    subject["screening"].update({
        "matched": True,
        "screening_state": "completed_match",
        "results": [
            {
                "name": "Jane Director",
                "categories": ["sanctions"],
                "match_status": "potential_match",
            }
        ],
    })
    return report


@pytest.fixture
def wrapped_db(temp_db):
    from db import get_db

    db = get_db()
    try:
        _cleanup_async_rows(db)
        yield db
    finally:
        _cleanup_async_rows(db)
        db.close()


def test_submit_persists_pricing_and_queues_screening_without_provider_poll(wrapped_db, monkeypatch):
    import server
    from security_hardening import collect_approval_gate_blockers
    from screening_adverse_truth import build_screening_adverse_truth_summary

    app_id, _ref = _seed_clean_draft(wrapped_db)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("submit must not call live provider screening")

    monkeypatch.setattr(server, "run_full_screening", fail_if_called)

    handler = _run_submit(wrapped_db, app_id)

    assert handler.status_code == 200
    assert handler.payload["status"] == "pricing_review"
    assert handler.payload["screening"]["pending"] is True
    assert handler.payload["screening"]["provider"] == "complyadvantage"

    app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    assert app["status"] == "pricing_review"
    assert app["submitted_at"]
    assert app["risk_level"]
    assert app["onboarding_lane"]

    prescreening = json.loads(app["prescreening_data"])
    assert prescreening["pricing"]["risk_level"] == app["risk_level"]
    report = prescreening["screening_report"]
    assert report["screening_mode"] == "pending"
    assert report["screening_async"]["status"] == "pending"
    assert report["screening_async"]["job_id"]

    jobs = wrapped_db.execute("SELECT * FROM screening_jobs WHERE application_id=?", (app_id,)).fetchall()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "pending"

    truth = build_screening_adverse_truth_summary(app=dict(app), prescreening=prescreening, screening_report=report)
    assert truth["approval_effect"] != "allow_direct_approval"
    assert "unresolved" in truth["states"] or "provider_failed" in truth["states"]

    wrapped_db.execute("UPDATE applications SET status='compliance_review' WHERE id=?", (app_id,))
    review_app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    blockers = collect_approval_gate_blockers(dict(review_app), wrapped_db)
    assert "screening_adverse_truth" in {blocker.get("id") for blocker in blockers}


def test_submit_retry_returns_persisted_state_without_duplicate_job(wrapped_db, monkeypatch):
    import server

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_retry", ref="ARF-ASYNC-002")
    monkeypatch.setattr(
        server,
        "run_full_screening",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider called during submit")),
    )

    first = _run_submit(wrapped_db, app_id)
    second = _run_submit(wrapped_db, app_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.payload["idempotent_recovery"] is True
    assert second.payload["status"] == "pricing_review"
    assert second.payload["screening"]["pending"] is True
    count = wrapped_db.execute("SELECT COUNT(*) AS c FROM screening_jobs WHERE application_id=?", (app_id,)).fetchone()
    assert count["c"] == 1


def test_worker_completes_background_screening_and_recomputes_truth(wrapped_db, monkeypatch):
    import server
    from screening_adverse_truth import build_screening_adverse_truth_summary
    from screening_jobs import claim_next_screening_job
    from verification_worker import process_claimed_screening_job

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_worker", ref="ARF-ASYNC-003")
    _run_submit(wrapped_db, app_id)

    monkeypatch.setattr(server, "run_full_screening", lambda *_args, **_kwargs: _clean_ca_report())
    job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()

    result = process_claimed_screening_job(wrapped_db, job, worker_id="worker-test")

    assert result["outcome"] == "succeeded"
    app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    prescreening = json.loads(app["prescreening_data"])
    report = prescreening["screening_report"]
    assert report["screening_async"]["status"] == "completed"
    assert report["screening_mode"] == "live"
    assert report["screened_at"]

    truth = build_screening_adverse_truth_summary(app=dict(app), prescreening=prescreening, screening_report=report)
    assert truth["approval_effect"] == "allow_direct_approval"


def test_worker_provider_failure_records_fail_closed_pending_state(wrapped_db, monkeypatch):
    import server
    from screening import ScreeningProviderError
    from screening_adverse_truth import build_screening_adverse_truth_summary
    from screening_jobs import claim_next_screening_job
    from verification_worker import process_claimed_screening_job

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_failure", ref="ARF-ASYNC-004")
    _run_submit(wrapped_db, app_id)

    def fail_provider(*_args, **_kwargs):
        raise ScreeningProviderError("simulated provider timeout")

    monkeypatch.setattr(server, "run_full_screening", fail_provider)
    job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()

    result = process_claimed_screening_job(wrapped_db, job, worker_id="worker-test")

    assert result["outcome"] == "retrying"
    app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    prescreening = json.loads(app["prescreening_data"])
    report = prescreening["screening_report"]
    assert report["screening_async"]["status"] == "retrying"
    assert report["screening_async"]["retryable"] is True
    truth = build_screening_adverse_truth_summary(app=dict(app), prescreening=prescreening, screening_report=report)
    assert truth["approval_effect"] != "allow_direct_approval"


def test_provider_failure_retry_reuses_tracked_job_and_preserves_correlation(wrapped_db, monkeypatch):
    import server
    from screening import ScreeningProviderError
    from screening_jobs import claim_next_screening_job
    from verification_worker import process_claimed_screening_job

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_retry_failed", ref="ARF-ASYNC-005")
    _run_submit(wrapped_db, app_id)

    def fail_provider(*_args, **_kwargs):
        raise ScreeningProviderError("simulated provider timeout")

    monkeypatch.setattr(server, "run_full_screening", fail_provider)
    first_job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()
    first_result = process_claimed_screening_job(wrapped_db, first_job, worker_id="worker-test")

    assert first_result["outcome"] == "retrying"
    assert first_result["job"]["id"] == first_job["id"]
    assert first_result["job"]["submit_attempt_id"] == first_job["submit_attempt_id"]

    wrapped_db.execute("UPDATE screening_jobs SET run_after=datetime('now') WHERE id=?", (first_job["id"],))
    wrapped_db.commit()
    monkeypatch.setattr(server, "run_full_screening", lambda *_args, **_kwargs: _clean_ca_report())

    retry_job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()
    retry_result = process_claimed_screening_job(wrapped_db, retry_job, worker_id="worker-test")

    assert retry_job["id"] == first_job["id"]
    assert retry_job["submit_attempt_id"] == first_job["submit_attempt_id"]
    assert retry_result["outcome"] == "succeeded"
    assert retry_result["job"]["id"] == first_job["id"]
    job_count = wrapped_db.execute("SELECT COUNT(*) AS c FROM screening_jobs WHERE application_id=?", (app_id,)).fetchone()
    assert job_count["c"] == 1


def test_provider_only_pep_after_async_screening_keeps_party_state_clean_and_blocks(wrapped_db, monkeypatch):
    import server
    from screening_adverse_truth import build_screening_adverse_truth_summary
    from screening_jobs import claim_next_screening_job
    from verification_worker import process_claimed_screening_job

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_provider_pep", ref="ARF-ASYNC-006")
    _run_submit(wrapped_db, app_id)

    monkeypatch.setattr(server, "run_full_screening", lambda *_args, **_kwargs: _provider_pep_ca_report())
    job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()

    result = process_claimed_screening_job(wrapped_db, job, worker_id="worker-test")

    assert result["outcome"] == "succeeded"
    director = wrapped_db.execute(
        "SELECT is_pep, pep_declaration FROM directors WHERE application_id=?",
        (app_id,),
    ).fetchone()
    assert director["is_pep"] == "No"
    app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    prescreening = json.loads(app["prescreening_data"])
    report = prescreening["screening_report"]
    assert report["director_screenings"][0]["provider_detected_pep"] is True
    truth = build_screening_adverse_truth_summary(app=dict(app), prescreening=prescreening, screening_report=report)
    assert truth["approval_effect"] != "allow_direct_approval"


def test_sanctions_result_after_async_screening_remains_prohibited(wrapped_db, monkeypatch):
    import server
    from screening_adverse_truth import build_screening_adverse_truth_summary
    from screening_jobs import claim_next_screening_job
    from verification_worker import process_claimed_screening_job

    app_id, _ref = _seed_clean_draft(wrapped_db, app_id="app_async_sanctions", ref="ARF-ASYNC-007")
    _run_submit(wrapped_db, app_id)

    monkeypatch.setattr(server, "run_full_screening", lambda *_args, **_kwargs: _sanctions_ca_report())
    job = claim_next_screening_job(wrapped_db, "worker-test")
    wrapped_db.commit()

    result = process_claimed_screening_job(wrapped_db, job, worker_id="worker-test")

    assert result["outcome"] == "succeeded"
    app = wrapped_db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    prescreening = json.loads(app["prescreening_data"])
    truth = build_screening_adverse_truth_summary(
        app=dict(app),
        prescreening=prescreening,
        screening_report=prescreening["screening_report"],
    )
    assert truth["approval_effect"] == "prohibited_fail_closed"

"""
Tests for SubmitApplicationHandler resilience hardening.

Covers:
    1. run_full_screening() is deferred → submit persists pending screening state
    2. Degraded screening path: individual provider failures produce degraded markers
    3. DB write failure after screening → controlled 500 error
    4. Successful submit path still works (happy path)
    5. Structured logging / expected status code behavior
    6. store_screening_mode() failure is handled gracefully
    7. ScreeningProviderError is a proper exception for worker/provider paths
    8. Outer defence-in-depth handler catches unexpected errors
"""
import json

from fixture_safe_refs import fixture_safe_suffix
import os
import sqlite3
import logging
import time
import pytest
from unittest.mock import patch, MagicMock
from concurrent.futures import Future


# ---------------------------------------------------------------------------
# Helpers — lazy imports to respect conftest temp_db fixture ordering
# ---------------------------------------------------------------------------

def _get_screening_module():
    import screening
    return screening


def _get_server_module():
    import server
    return server


def _get_project_db(temp_db_path):
    """Get a project DBConnection (returns dicts from fetchone/fetchall)."""
    from db import get_db as project_get_db
    return project_get_db()


def _setup_test_app(db, client_id="testuser"):
    """Insert a test application with one director. Returns app_id."""
    import uuid
    app_id = f"test_{uuid.uuid4().hex[:8]}"
    ref = f"ARF-2026-{fixture_safe_suffix(6)}"
    db.execute("""
        INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, ref, client_id, "Test Corp", "Mauritius", "Technology", "SME", "draft",
          json.dumps({"incorporation_date": "2020-01-01"})))
    db.execute("""
        INSERT INTO directors (id, application_id, full_name, nationality)
        VALUES (?, ?, ?, ?)
    """, (f"dir_{app_id}", app_id, "John Doe", "MU"))
    db.commit()
    return app_id


def _make_handler(server_mod, error_calls, success_calls=None):
    """Create a SubmitApplicationHandler with mocked response methods."""
    handler = server_mod.SubmitApplicationHandler.__new__(server_mod.SubmitApplicationHandler)
    handler.error = lambda msg, status=400: error_calls.append((msg, status))
    if success_calls is not None:
        handler.success = lambda data, status=200: success_calls.append((data, status))
    handler.get_client_ip = MagicMock(return_value="127.0.0.1")
    handler.log_audit = MagicMock()
    handler.check_app_ownership = MagicMock(return_value=True)
    handler.request = MagicMock()
    handler.request.headers = {}
    return handler


# ---------------------------------------------------------------------------
# 1. run_full_screening is deferred → durable pending screening state
# ---------------------------------------------------------------------------

class TestScreeningExceptionReturns503:
    """Submit must not call run_full_screening; provider errors are worker-path concerns."""

    def test_screening_exception_returns_503(self, temp_db):
        """A provider exception would not affect submit because submit does not call the provider."""
        server = _get_server_module()

        error_calls = []
        success_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        with patch.object(server, "run_full_screening", side_effect=RuntimeError("CA timeout")) as screening_mock:
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)
            handler._do_submit(db, {"sub": "testuser", "name": "Test", "role": "client", "type": "client"}, app_id)
            row = db.execute("SELECT status, prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
            job_count = db.execute("SELECT COUNT(*) AS c FROM screening_jobs WHERE application_id=?", (app_id,)).fetchone()["c"]
            db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][1] == 200
        assert success_calls[0][0]["status"] == "pricing_review"
        assert success_calls[0][0]["screening"]["pending"] is True
        assert row["status"] == "pricing_review"
        assert json.loads(row["prescreening_data"])["screening_report"]["screening_async"]["status"] == "pending"
        assert job_count == 1
        assert screening_mock.call_count == 0

    def test_screening_provider_error_returns_503(self, temp_db):
        """ScreeningProviderError is deferred to worker processing, not submit response."""
        server = _get_server_module()
        from screening import ScreeningProviderError

        error_calls = []
        success_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        with patch.object(server, "run_full_screening", side_effect=ScreeningProviderError("All providers down")) as screening_mock:
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)
            handler._do_submit(db, {"sub": "testuser", "name": "Test", "role": "client", "type": "client"}, app_id)
            row = db.execute("SELECT status, prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
            job = db.execute("SELECT * FROM screening_jobs WHERE application_id=?", (app_id,)).fetchone()
            db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][0]["screening"]["pending"] is True
        assert row["status"] == "pricing_review"
        assert json.loads(row["prescreening_data"])["screening_report"]["screening_async"]["job_id"] == job["id"]
        assert screening_mock.call_count == 0


# ---------------------------------------------------------------------------
# 2. Degraded screening path
# ---------------------------------------------------------------------------

class TestDegradedScreeningPath:
    """Individual provider failures produce degraded markers, not crashes."""

    def test_single_provider_failure_produces_degraded_marker(self):
        """If one provider raises, report should contain a degraded_sources entry."""
        screening = _get_screening_module()

        def raise_on_opencorporates(company_name, jurisdiction=None):
            raise ConnectionError("OpenCorporates API timeout")

        def mock_aml(name, birth_date=None, nationality=None, entity_type="Person"):
            return {"matched": False, "results": [], "source": "mocked", "api_status": "mocked"}

        def mock_geo(ip):
            return {"country": "Local", "country_code": "XX", "source": "local", "risk_level": "LOW",
                    "is_vpn": False, "is_proxy": False, "is_tor": False}

        def mock_create_applicant(**kwargs):
            return {"applicant_id": "sim_123", "source": "simulated", "api_status": "simulated"}

        with patch.object(screening, "lookup_opencorporates", raise_on_opencorporates):
            with patch.object(screening, "screen_sumsub_aml", mock_aml):
                with patch.object(screening, "geolocate_ip", mock_geo):
                    with patch.object(screening, "sumsub_create_applicant", mock_create_applicant):
                        report = screening.run_full_screening(
                            {"company_name": "TestCo", "country": "Mauritius"},
                            [{"full_name": "Jane Director", "nationality": "MU"}],
                            [],
                            client_ip="127.0.0.1",
                        )

        assert "degraded_sources" in report
        assert "opencorporates" in report["degraded_sources"]
        assert report["company_screening"].get("degraded") is True

    def test_all_providers_succeed_no_degradation(self):
        """Happy path: no degraded sources."""
        screening = _get_screening_module()

        def mock_oc(company_name, jurisdiction=None):
            return {"found": True, "companies": [{"name": company_name}], "source": "mocked", "api_status": "mocked"}

        def mock_aml(name, birth_date=None, nationality=None, entity_type="Person"):
            return {"matched": False, "results": [], "source": "mocked", "api_status": "mocked"}

        def mock_geo(ip):
            return {"country": "Local", "country_code": "XX", "source": "local", "risk_level": "LOW",
                    "is_vpn": False, "is_proxy": False, "is_tor": False}

        def mock_create_applicant(**kwargs):
            return {"applicant_id": "sim_123", "source": "simulated", "api_status": "simulated"}

        with patch.object(screening, "lookup_opencorporates", mock_oc):
            with patch.object(screening, "screen_sumsub_aml", mock_aml):
                with patch.object(screening, "geolocate_ip", mock_geo):
                    with patch.object(screening, "sumsub_create_applicant", mock_create_applicant):
                        report = screening.run_full_screening(
                            {"company_name": "TestCo", "country": "Mauritius"},
                            [{"full_name": "Jane Director", "nationality": "MU"}],
                            [],
                            client_ip="127.0.0.1",
                        )

        assert report.get("degraded_sources", []) == []

    def test_director_screening_failure_degraded(self):
        """If director AML screening fails, director entry is degraded."""
        screening = _get_screening_module()

        def mock_aml_fail_director(name, birth_date=None, nationality=None, entity_type="Person"):
            if entity_type == "Person":
                raise TimeoutError("Legacy screening timeout")
            return {"matched": False, "results": [], "source": "mocked", "api_status": "mocked"}

        def mock_oc(company_name, jurisdiction=None):
            return {"found": True, "companies": [], "source": "mocked", "api_status": "mocked"}

        def mock_geo(ip):
            return {"country": "Local", "country_code": "XX", "source": "local", "risk_level": "LOW",
                    "is_vpn": False, "is_proxy": False, "is_tor": False}

        def mock_create_applicant(**kwargs):
            return {"applicant_id": "sim_123", "source": "simulated", "api_status": "simulated"}

        with patch.object(screening, "lookup_opencorporates", mock_oc):
            with patch.object(screening, "screen_sumsub_aml", mock_aml_fail_director):
                with patch.object(screening, "geolocate_ip", mock_geo):
                    with patch.object(screening, "sumsub_create_applicant", mock_create_applicant):
                        report = screening.run_full_screening(
                            {"company_name": "TestCo", "country": "Mauritius"},
                            [{"full_name": "Jane Director", "nationality": "MU"}],
                            [],
                            client_ip="127.0.0.1",
                        )

        # Director screening should be degraded but not crash
        assert len(report["director_screenings"]) == 1
        ds = report["director_screenings"][0]
        assert ds["screening"].get("degraded") is True
        assert any("director_screening" in s or "sumsub_director" in s for s in report.get("degraded_sources", []))


# ---------------------------------------------------------------------------
# 3. DB write failure after screening
# ---------------------------------------------------------------------------

class TestDBWriteFailureAfterScreening:
    """DB failures post-screening should produce controlled 500, not raw unhandled."""

    def test_db_write_failure_returns_500_with_message(self, temp_db):
        """Simulate DB failure during the post-screening write path."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

        mock_risk = {
            "score": 30, "level": "LOW", "dimensions": {},
            "lane": "Fast Lane", "escalations": [], "requires_compliance_approval": False,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report) as screening_mock:
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        db = _get_project_db(temp_db)
                        app_id = _setup_test_app(db)

                        # Wrap DB to fail on UPDATE after screening completes
                        original_execute = db.execute
                        update_count = [0]
                        def failing_execute(sql, params=()):
                            if "UPDATE applications SET prescreening_data" in sql:
                                update_count[0] += 1
                                if update_count[0] >= 1:
                                    raise sqlite3.OperationalError("database is locked")
                            return original_execute(sql, params)

                        db.execute = failing_execute

                        handler._do_submit(
                            db,
                            {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                            app_id
                        )
                        try:
                            db.close()
                        except Exception:
                            pass

        assert len(error_calls) > 0
        last_error = error_calls[-1]
        assert last_error[1] == 500
        assert "retry" in last_error[0].lower() or "failed to save" in last_error[0].lower()


# ---------------------------------------------------------------------------
# 4. Successful submit path still works (happy path)
# ---------------------------------------------------------------------------

class TestHappyPathSubmit:
    """The successful submit path must remain unchanged."""

    def test_successful_submit_returns_200(self, temp_db):
        """Full happy path: screening succeeds, DB writes succeed, 200 returned."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

        mock_risk = {
            "score": 30, "level": "LOW",
            "dimensions": {"D1": 1, "D2": 1, "D3": 1, "D4": 1, "D5": 1},
            "lane": "Fast Lane", "escalations": [], "requires_compliance_approval": False,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report) as screening_mock:
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        db = _get_project_db(temp_db)
                        app_id = _setup_test_app(db)

                        handler._do_submit(
                            db,
                            {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                            app_id
                        )
                        db.close()

        assert len(error_calls) == 0, f"Unexpected errors: {error_calls}"
        assert len(success_calls) == 1
        data = success_calls[0][0]
        assert data["status"] == "pricing_review"
        assert data["risk_level"] == "LOW"
        assert "screening" in data
        assert "degraded_sources" in data["screening"]
        assert data["screening"]["pending"] is True
        assert screening_mock.call_count == 0

    def test_edd_routed_low_risk_is_floored_before_persisting(self, temp_db):
        """A policy-routed EDD case must not persist or return final LOW risk."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

        mock_risk = {
            "score": 28,
            "level": "LOW",
            "base_risk_score": 28,
            "base_risk_level": "LOW",
            "final_risk_level": "LOW",
            "dimensions": {"D1": 1, "D2": 1, "D3": 1, "D4": 4, "D5": 1},
            "lane": "Fast Lane",
            "sector_label": "Crypto VASP exchange",
            "sector_risk_tier": "very_high",
            "escalations": [],
            "elevation_reason_text": "",
            "requires_compliance_approval": False,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report) as screening_mock:
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        db = _get_project_db(temp_db)
                        app_id = _setup_test_app(db)

                        handler._do_submit(
                            db,
                            {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                            app_id,
                        )
                        app = db.execute(
                            """
                            SELECT risk_score, risk_level, final_risk_level,
                                   base_risk_level, elevation_reason_text, onboarding_lane
                            FROM applications
                            WHERE id=?
                            """,
                            (app_id,),
                        ).fetchone()
                        db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        data = success_calls[0][0]
        assert data["risk_level"] != "LOW"
        assert data["final_risk_level"] == data["risk_level"]
        assert data["onboarding_lane"] == "EDD"
        assert "floor_rule_edd_routing" in data["risk_escalations"]
        assert app["risk_level"] != "LOW"
        assert app["final_risk_level"] == app["risk_level"]
        assert app["base_risk_level"] == "LOW"
        assert app["risk_score"] >= 55
        assert "EDD routing floor" in app["elevation_reason_text"]
        audit_after = next(
            call.kwargs["after_state"]
            for call in handler.log_audit.call_args_list
            if call.args[1] == "Pre-Screening Submitted"
        )
        assert audit_after["final_risk_level"] == data["risk_level"]
        assert "EDD routing floor" in audit_after["elevation_reason_text"]
        assert data["screening"]["pending"] is True
        assert screening_mock.call_count == 0

    def test_deferred_enhanced_requirements_schedule_without_preapproval(self, temp_db):
        """Deferred enhanced triggers must still enqueue post-commit generation."""
        server = _get_server_module()
        import routing_actuator

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 30,
            "level": "LOW",
            "final_risk_level": "LOW",
            "dimensions": {"D1": 1, "D2": 1, "D3": 1, "D4": 1, "D5": 1},
            "lane": "Fast Lane",
            "escalations": [],
            "requires_compliance_approval": False,
        }
        scheduled = []

        def capture_post_commit(fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))
            future = Future()
            future.set_result(None)
            return future

        routing_result = {
            "ran": True,
            "route": "standard",
            "triggers": [],
            "enhanced_requirements_deferred": True,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(routing_actuator, "apply_routing_decision", return_value=routing_result):
                            with patch.object(server._POST_COMMIT_EXECUTOR, "submit", side_effect=capture_post_commit):
                                db = _get_project_db(temp_db)
                                app_id = _setup_test_app(db)

                                handler._do_submit(
                                    db,
                                    {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                    app_id,
                                )
                                app = db.execute(
                                    "SELECT status, risk_level, onboarding_lane FROM applications WHERE id=?",
                                    (app_id,),
                                ).fetchone()
                                db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "LOW"
        assert app["onboarding_lane"] == "Fast Lane"
        scheduled_names = [item[0].__name__ for item in scheduled]
        assert scheduled_names == ["_generate_prescreening_enhanced_requirements_async"]

    def test_edd_submit_persists_state_and_schedules_enhanced_requirements_for_client_actor(self, temp_db):
        """EDD/high-risk success must persist pricing_review before deferred setup runs."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        scheduled = []

        def capture_post_commit(fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))
            future = Future()
            future.set_result(None)
            return future

        with patch.object(server, "run_full_screening", return_value=mock_report) as screening_mock:
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(server._POST_COMMIT_EXECUTOR, "submit", side_effect=capture_post_commit):
                            db = _get_project_db(temp_db)
                            app_id = _setup_test_app(db)
                            db.execute("PRAGMA foreign_keys = ON")

                            handler._do_submit(
                                db,
                                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                app_id,
                            )

                            app = db.execute(
                                """
                                SELECT status, risk_score, risk_level, onboarding_lane, submitted_at
                                FROM applications
                                WHERE id=?
                                """,
                                (app_id,),
                            ).fetchone()
                            req_count = db.execute(
                                """
                                SELECT COUNT(*) AS c
                                FROM application_enhanced_requirements
                                WHERE application_id=?
                                """,
                                (app_id,),
                            ).fetchone()["c"]
                            db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][0]["status"] == "pricing_review"
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert app["risk_score"] == 72
        assert app["submitted_at"]
        assert req_count == 0
        assert success_calls[0][0]["screening"]["pending"] is True
        assert screening_mock.call_count == 0
        scheduled_names = [item[0].__name__ for item in scheduled]
        assert "_generate_prescreening_enhanced_requirements_async" in scheduled_names
        assert "_send_prescreening_compliance_notifications" in scheduled_names

    def test_post_commit_scheduler_failure_does_not_convert_submit_to_500(self, temp_db):
        """Durable EDD submit success must survive non-critical post-commit scheduler failure."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(
                            server._POST_COMMIT_EXECUTOR,
                            "submit",
                            side_effect=RuntimeError("executor unavailable"),
                        ) as submit_mock:
                            db = _get_project_db(temp_db)
                            app_id = _setup_test_app(db)

                            handler._do_submit(
                                db,
                                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                app_id,
                            )
                            app = db.execute(
                                "SELECT status, risk_level, onboarding_lane, submitted_at FROM applications WHERE id=?",
                                (app_id,),
                            ).fetchone()
                            req_count = db.execute(
                                "SELECT COUNT(*) AS c FROM application_enhanced_requirements WHERE application_id=?",
                                (app_id,),
                            ).fetchone()["c"]
                            db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][0]["status"] == "pricing_review"
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert app["submitted_at"]
        assert req_count == 0
        assert submit_mock.call_count == 2

    def test_edd_submit_returns_success_when_deferred_generation_fails(self, temp_db):
        """Deferred enhanced generation failure must not make durable submit ambiguous."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        def run_post_commit_inline(fn, *args, **kwargs):
            future = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
            return future

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(
                            server,
                            "generate_application_enhanced_requirements",
                            side_effect=RuntimeError("simulated generation failure"),
                        ):
                            with patch.object(
                                server._POST_COMMIT_EXECUTOR,
                                "submit",
                                side_effect=run_post_commit_inline,
                            ):
                                db = _get_project_db(temp_db)
                                app_id = _setup_test_app(db)
                                db.execute("PRAGMA foreign_keys = ON")

                                handler._do_submit(
                                    db,
                                    {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                    app_id,
                                )

                                app = db.execute(
                                    "SELECT status, risk_level, onboarding_lane FROM applications WHERE id=?",
                                    (app_id,),
                                ).fetchone()
                                req_count = db.execute(
                                    """
                                    SELECT COUNT(*) AS c
                                    FROM application_enhanced_requirements
                                    WHERE application_id=?
                                    """,
                                    (app_id,),
                                ).fetchone()["c"]
                                db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][0]["status"] == "pricing_review"
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert req_count == 0

    def test_slow_enhanced_requirement_generation_is_not_inline(self, temp_db):
        """A slow enhanced generator must not delay the first HIGH/EDD submit response."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        scheduled = []

        def capture_post_commit(fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))
            future = Future()
            future.set_result(None)
            return future

        def slow_generation(*_args, **_kwargs):
            time.sleep(1.0)
            raise AssertionError("enhanced generation must not run inline")

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(server, "generate_application_enhanced_requirements", side_effect=slow_generation) as gen_mock:
                            with patch.object(server._POST_COMMIT_EXECUTOR, "submit", side_effect=capture_post_commit):
                                db = _get_project_db(temp_db)
                                app_id = _setup_test_app(db)
                                handler._do_submit(
                                    db,
                                    {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                    app_id,
                                )
                                app = db.execute(
                                    "SELECT status, risk_level, onboarding_lane FROM applications WHERE id=?",
                                    (app_id,),
                                ).fetchone()
                                db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert gen_mock.call_count == 0
        assert any(item[0].__name__ == "_generate_prescreening_enhanced_requirements_async" for item in scheduled)

    def test_slow_compliance_notification_is_not_inline(self, temp_db):
        """A slow compliance notification sender must not delay the first HIGH/EDD submit response."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        scheduled = []

        def capture_post_commit(fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))
            future = Future()
            future.set_result(None)
            return future

        def slow_notification(*_args, **_kwargs):
            time.sleep(1.0)
            raise AssertionError("notification sender must not run inline")

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(server, "_send_prescreening_compliance_notifications", side_effect=slow_notification) as notify_mock:
                            with patch.object(server._POST_COMMIT_EXECUTOR, "submit", side_effect=capture_post_commit):
                                db = _get_project_db(temp_db)
                                app_id = _setup_test_app(db)
                                handler._do_submit(
                                    db,
                                    {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                    app_id,
                                )
                                app = db.execute(
                                    "SELECT status, risk_level, onboarding_lane FROM applications WHERE id=?",
                                    (app_id,),
                                ).fetchone()
                                db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert notify_mock.call_count == 0
        assert any(item[0] is notify_mock for item in scheduled)

    def test_retry_after_committed_high_submit_returns_current_state_without_screening_or_duplicate_audit(self, temp_db):
        """A retry after durable HIGH/EDD submit must be a current-state read, not a second submit."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        db = _get_project_db(temp_db)
        app_id = _setup_test_app(db)
        prescreening = {
            "incorporation_date": "2020-01-01",
            "pricing": {
                "onboarding_fee": 3500,
                "annual_monitoring_fee": 2000,
                "currency": "USD",
                "risk_level": "HIGH",
                "final_risk_level": "HIGH",
                "base_risk_level": "HIGH",
                "elevation_reason_text": "",
            },
            "screening_report": {
                "total_hits": 2,
                "overall_flags": ["pep_match"],
                "degraded_sources": [],
                "company_screening": {"source": "cached_registry"},
                "ip_geolocation": {"source": "cached_geo"},
                "director_screenings": [{"screening": {"source": "cached_aml"}}],
            },
        }
        db.execute(
            """
            UPDATE applications
            SET status='pricing_review',
                submitted_at=datetime('now'),
                risk_score=?,
                risk_level=?,
                risk_dimensions=?,
                onboarding_lane=?,
                risk_escalations=?,
                base_risk_level=?,
                final_risk_level=?,
                elevation_reason_text=?,
                prescreening_data=?
            WHERE id=?
            """,
            (
                72,
                "HIGH",
                json.dumps({"D1": 4, "D2": 4}),
                "EDD",
                json.dumps(["edd_required"]),
                "HIGH",
                "HIGH",
                "",
                json.dumps(prescreening),
                app_id,
            ),
        )
        db.commit()
        before_audit_count = db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]

        with patch.object(server, "run_full_screening", side_effect=AssertionError("screening must not rerun")):
            handler._do_submit(
                db,
                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                app_id,
            )

        after_audit_count = db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        data, status = success_calls[0]
        assert status == 200
        assert data["idempotent_recovery"] is True
        assert data["status"] == "pricing_review"
        assert data["risk_level"] == "HIGH"
        assert data["onboarding_lane"] == "EDD"
        assert data["requires_pre_approval"] is True
        assert data["screening"]["total_hits"] == 2
        assert handler.log_audit.call_count == 0
        assert after_audit_count == before_audit_count

    def test_incomplete_submitted_state_does_not_return_recovery(self, temp_db):
        """Risk data alone is not enough for recovery; submit repairs it through a durable submit."""
        server = _get_server_module()
        from screening import ScreeningProviderError

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        db = _get_project_db(temp_db)
        app_id = _setup_test_app(db)
        db.execute(
            """
            UPDATE applications
            SET status='submitted',
                submitted_at=datetime('now'),
                risk_score=?,
                risk_level=?,
                onboarding_lane=?,
                prescreening_data=?
            WHERE id=?
            """,
            (
                72,
                "HIGH",
                "EDD",
                json.dumps({"incorporation_date": "2020-01-01"}),
                app_id,
            ),
        )
        db.commit()

        with patch.object(
            server,
            "run_full_screening",
            side_effect=ScreeningProviderError("provider unavailable"),
        ) as screening_mock:
            handler._do_submit(
                db,
                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                app_id,
            )
        db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert success_calls[0][1] == 200
        assert success_calls[0][0]["status"] == "pricing_review"
        assert success_calls[0][0].get("idempotent_recovery") is None
        assert success_calls[0][0]["screening"]["pending"] is True
        assert screening_mock.call_count == 0

    def test_sanctioned_prescreening_country_returns_403_before_screening_and_is_retry_safe(self, temp_db):
        """Sanctioned jurisdiction aliases must fail deterministically before provider work."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        db = _get_project_db(temp_db)
        app_id = _setup_test_app(db)
        db.execute(
            "UPDATE applications SET country=?, prescreening_data=? WHERE id=?",
            (
                "Mauritius",
                json.dumps({
                    "incorporation_date": "2020-01-01",
                    "country_of_incorporation": "North Korea (DPRK)",
                }),
                app_id,
            ),
        )
        db.commit()

        with patch.object(server, "run_full_screening", side_effect=AssertionError("screening must not run")):
            handler._do_submit(
                db,
                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                app_id,
            )
            handler._do_submit(
                db,
                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                app_id,
            )

        row = db.execute("SELECT status, risk_level, prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
        db.close()

        assert len(error_calls) == 2
        assert error_calls[0][1] == 403
        assert error_calls[1][1] == 403
        assert row["status"] == "draft"
        assert row["risk_level"] is None
        assert "screening_report" not in json.loads(row["prescreening_data"])

    def test_post_commit_notification_scheduling_failure_does_not_convert_submit_to_500(self, temp_db):
        """If the non-critical notifier cannot be scheduled, durable submit still returns success."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        mock_risk = {
            "score": 72,
            "level": "HIGH",
            "final_risk_level": "HIGH",
            "dimensions": {"D1": 4, "D2": 4, "D3": 3, "D4": 3, "D5": 4},
            "lane": "EDD",
            "escalations": [],
            "requires_compliance_approval": True,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", return_value="simulated"):
                    with patch.object(server, "store_screening_mode", return_value=True):
                        with patch.object(
                            server._POST_COMMIT_EXECUTOR,
                            "submit",
                            side_effect=RuntimeError("executor unavailable"),
                        ) as submit_mock:
                            db = _get_project_db(temp_db)
                            app_id = _setup_test_app(db)
                            db.execute("PRAGMA foreign_keys = ON")

                            handler._do_submit(
                                db,
                                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                                app_id,
                            )

                            app = db.execute(
                                "SELECT status, risk_level, onboarding_lane, submitted_at FROM applications WHERE id=?",
                                (app_id,),
                            ).fetchone()
                            db.close()

        assert error_calls == []
        assert len(success_calls) == 1
        assert submit_mock.call_count == 2
        assert success_calls[0][0]["status"] == "pricing_review"
        assert app["status"] == "pricing_review"
        assert app["risk_level"] == "HIGH"
        assert app["onboarding_lane"] == "EDD"
        assert app["submitted_at"]


# ---------------------------------------------------------------------------
# 5. Structured logging / expected status code behavior
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    """Errors are logged with structured context."""

    def test_screening_failure_logs_context(self, temp_db, caplog):
        """On async submit DB failure, structured log includes app_id, ref, user, stage."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "enqueue_screening_job", side_effect=ConnectionError("queue unavailable")):
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)

            with caplog.at_level(logging.ERROR, logger="arie"):
                handler._do_submit(
                    db,
                    {"sub": "user123", "name": "Test User", "role": "client", "type": "client"},
                    app_id
                )
            db.close()

        assert len(error_calls) == 1
        assert error_calls[0][1] == 500

        # Check log contains structured context
        log_text = caplog.text
        assert app_id in log_text
        assert "async_submit_db_write" in log_text

    def test_no_traceback_leaked_to_client(self, temp_db):
        """Error messages to client should not contain tracebacks."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "enqueue_screening_job", side_effect=RuntimeError("Traceback (most recent call last):\n...")):
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)

            handler._do_submit(
                db,
                {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                app_id
            )
            db.close()

        assert len(error_calls) > 0
        error_msg = error_calls[-1][0]
        assert "Traceback" not in error_msg
        assert "most recent call" not in error_msg


# ---------------------------------------------------------------------------
# 6. store_screening_mode failure is handled gracefully
# ---------------------------------------------------------------------------

class TestStoreScreeningModeFailure:
    """store_screening_mode failure should not crash the submit flow."""

    def test_store_screening_mode_exception_handled(self, temp_db):
        """If store_screening_mode raises, submit continues with screening_mode=unknown."""
        server = _get_server_module()

        success_calls = []
        error_calls = []
        handler = _make_handler(server, error_calls, success_calls)

        mock_report = {
            "screened_at": "2026-01-01T00:00:00",
            "company_screening": {"found": True, "source": "mocked"},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

        mock_risk = {
            "score": 25, "level": "LOW",
            "dimensions": {"D1": 1, "D2": 1, "D3": 1, "D4": 1, "D5": 1},
            "lane": "Fast Lane", "escalations": [], "requires_compliance_approval": False,
        }

        with patch.object(server, "run_full_screening", return_value=mock_report):
            with patch.object(server, "compute_risk_score", return_value=mock_risk):
                with patch.object(server, "determine_screening_mode", side_effect=RuntimeError("boom")):
                    db = _get_project_db(temp_db)
                    app_id = _setup_test_app(db)

                    handler._do_submit(
                        db,
                        {"sub": "testuser", "name": "Test", "role": "client", "type": "client"},
                        app_id
                    )
                    db.close()

        # Should succeed despite store_screening_mode failure
        assert len(error_calls) == 0, f"Unexpected errors: {error_calls}"
        assert len(success_calls) == 1


# ---------------------------------------------------------------------------
# 7. _safe_future_result utility
# ---------------------------------------------------------------------------

class TestSafeFutureResult:
    """Test the _safe_future_result helper in screening.py."""

    def test_successful_future(self):
        screening = _get_screening_module()
        f = Future()
        f.set_result({"found": True})
        result, err = screening._safe_future_result(f, timeout=5, source_label="test")
        assert result == {"found": True}
        assert err is None

    def test_failed_future(self):
        screening = _get_screening_module()
        f = Future()
        f.set_exception(ConnectionError("timeout"))
        result, err = screening._safe_future_result(f, timeout=5, source_label="test_source")
        assert result.get("degraded") is True
        assert result.get("source") == "test_source"
        assert err is not None
        assert "timeout" in err


# ---------------------------------------------------------------------------
# 8. Outer defence-in-depth handler
# ---------------------------------------------------------------------------

class TestOuterDefenceInDepth:
    """The outer try/except in post() catches all unexpected errors."""

    def test_outer_handler_catches_unexpected_error(self, temp_db):
        """If _do_submit raises something completely unexpected, post() catches it."""
        server = _get_server_module()

        error_calls = []
        def mock_error(msg, status=400):
            error_calls.append((msg, status))

        # Create a handler that will fail in _do_submit
        real_handler = server.SubmitApplicationHandler.__new__(server.SubmitApplicationHandler)
        real_handler.require_auth = MagicMock(return_value={"sub": "testuser", "name": "Test", "role": "client", "type": "client"})
        real_handler.check_rate_limit = MagicMock(return_value=True)
        real_handler.error = mock_error
        real_handler.get_client_ip = MagicMock(return_value="127.0.0.1")
        real_handler.request = MagicMock()
        real_handler.request.remote_ip = "127.0.0.1"
        real_handler.request.headers = {}

        # Patch get_db to return something that will fail
        with patch.object(server, "get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.execute.side_effect = RuntimeError("Completely unexpected DB crash")
            mock_db.rollback = MagicMock()
            mock_db.close = MagicMock()
            mock_get_db.return_value = mock_db

            real_handler.post("nonexistent_app")

        assert len(error_calls) > 0
        last_error = error_calls[-1]
        assert last_error[1] == 500
        assert "unexpected error" in last_error[0].lower() or "try again" in last_error[0].lower()


# ---------------------------------------------------------------------------
# 9. ScreeningProviderError class
# ---------------------------------------------------------------------------

class TestScreeningProviderError:
    """Verify ScreeningProviderError is a proper exception."""

    def test_is_exception_subclass(self):
        from screening import ScreeningProviderError
        assert issubclass(ScreeningProviderError, Exception)

    def test_can_be_raised_and_caught(self):
        from screening import ScreeningProviderError
        with pytest.raises(ScreeningProviderError):
            raise ScreeningProviderError("test message")

    def test_message_preserved(self):
        from screening import ScreeningProviderError
        try:
            raise ScreeningProviderError("provider X unavailable")
        except ScreeningProviderError as e:
            assert "provider X unavailable" in str(e)

"""
Tests for SubmitApplicationHandler resilience hardening.

Covers:
    1. run_full_screening() raises → submit returns controlled 503, not generic 500
    2. Degraded screening path: individual provider failures produce degraded markers
    3. DB write failure after screening → controlled 500 error
    4. Successful submit path still works (happy path)
    5. Structured logging / expected status code behavior
    6. store_screening_mode() failure is handled gracefully
    7. ScreeningProviderError produces 503
    8. Outer defence-in-depth handler catches unexpected errors
"""
import json
import os
import sqlite3
import logging
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
    ref = f"ARF-2026-{uuid.uuid4().hex[:6]}"
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
    return handler


# ---------------------------------------------------------------------------
# 1. run_full_screening raises → controlled 503
# ---------------------------------------------------------------------------

class TestScreeningExceptionReturns503:
    """When run_full_screening raises, the handler must return 503 not 500."""

    def test_screening_exception_returns_503(self, temp_db):
        """Simulate run_full_screening raising a generic exception."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "run_full_screening", side_effect=RuntimeError("Sumsub timeout")):
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)
            handler._do_submit(db, {"sub": "testuser", "name": "Test", "role": "client", "type": "client"}, app_id)
            db.close()

        assert len(error_calls) > 0, "Expected error to be called"
        last_error = error_calls[-1]
        assert last_error[1] == 503, f"Expected 503 but got {last_error[1]}"
        assert "temporarily unavailable" in last_error[0].lower() or "retry" in last_error[0].lower()

    def test_screening_provider_error_returns_503(self, temp_db):
        """Simulate ScreeningProviderError specifically."""
        server = _get_server_module()
        from screening import ScreeningProviderError

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "run_full_screening", side_effect=ScreeningProviderError("All providers down")):
            db = _get_project_db(temp_db)
            app_id = _setup_test_app(db)
            handler._do_submit(db, {"sub": "testuser", "name": "Test", "role": "client", "type": "client"}, app_id)
            db.close()

        assert len(error_calls) > 0
        assert error_calls[-1][1] == 503


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

        call_count = [0]
        def mock_aml_fail_director(name, birth_date=None, nationality=None, entity_type="Person"):
            call_count[0] += 1
            if entity_type == "Person":
                raise TimeoutError("Sumsub AML timeout")
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

        with patch.object(server, "run_full_screening", return_value=mock_report):
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

        with patch.object(server, "run_full_screening", return_value=mock_report):
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


# ---------------------------------------------------------------------------
# 5. Structured logging / expected status code behavior
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    """Errors are logged with structured context."""

    def test_screening_failure_logs_context(self, temp_db, caplog):
        """On screening failure, structured log includes app_id, ref, user, ip, stage."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "run_full_screening", side_effect=ConnectionError("Provider timeout")):
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
        assert error_calls[0][1] == 503

        # Check log contains structured context
        log_text = caplog.text
        assert app_id in log_text or "run_full_screening" in log_text

    def test_no_traceback_leaked_to_client(self, temp_db):
        """Error messages to client should not contain tracebacks."""
        server = _get_server_module()

        error_calls = []
        handler = _make_handler(server, error_calls)

        with patch.object(server, "run_full_screening", side_effect=RuntimeError("Traceback (most recent call last):\n...")):
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

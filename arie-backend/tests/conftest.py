"""
ARIE Finance — Test Fixtures & Infrastructure
Sprint 1: Stability foundation
"""
import os
import sys
import json
import tempfile
import sqlite3
import pytest
import re
import uuid
from datetime import datetime, timedelta, timezone

# Add parent directory to path so we can import server modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# tests/ itself, so shared test helpers (fixture_safe_refs) import everywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["ENVIRONMENT"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
# Tornado AsyncHTTPTestCase default timeout is 5s, which loaded CI runners can
# exceed while the handler still succeeds (deploy run 29673660015: HTTP 200 in
# 5,646ms → test "failed"). 15s keeps genuine hangs detectable without letting
# runner load fail healthy handlers. Overridable via the same env var.
os.environ.setdefault("ASYNC_TEST_TIMEOUT", "15")
# Most legacy AML adapter tests for Sumsub exercise the old mechanics.  Sprint
# 1 keeps runtime defaulting to IDV-only unless entitlement is explicitly
# proven, so the test harness opts in while dedicated entitlement tests opt out.
os.environ.setdefault("SUMSUB_AML_ENTITLEMENT_PROVEN", "true")

_OFFICER_ROLES = {"admin", "sco", "co", "analyst"}


_db_initialized = False


def clean_ca_screening_report(*, screened_at=None, company_name="Clean Screening Ltd"):
    """Return a deterministic ComplyAdvantage-clean screening report for approval fixtures."""
    screened_at = screened_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "provider": "complyadvantage",
        "screening_provider": "complyadvantage",
        "screening_mode": "live",
        "screened_at": screened_at,
        "company_screening": {
            "company_name": company_name,
            "provider": "complyadvantage",
            "source": "complyadvantage",
            "api_status": "live",
            "matched": False,
            "results": [],
        },
        "director_screenings": [],
        "ubo_screenings": [],
        "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
    }


def clean_ca_prescreening(*, screened_at=None, valid_days=90, company_name="Clean Screening Ltd"):
    now = datetime.now(timezone.utc)
    report = clean_ca_screening_report(
        screened_at=screened_at or now.strftime("%Y-%m-%dT%H:%M:%S"),
        company_name=company_name,
    )
    return {
        "screening_report": report,
        "screening_valid_until": (now + timedelta(days=valid_days)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": valid_days,
    }


def clean_ca_prescreening_json(*, screened_at=None, valid_days=90, company_name="Clean Screening Ltd"):
    return json.dumps(
        clean_ca_prescreening(
            screened_at=screened_at,
            valid_days=valid_days,
            company_name=company_name,
        )
    )


def _sync_test_db_path(path):
    """Keep already-imported config/db/server modules pointed at the test DB.

    Several tests import runtime modules during collection, before ``temp_db``
    has a chance to set ``DB_PATH``. Those modules cache DB_PATH at import time,
    so updating only ``os.environ`` is not enough for same-process test runs.
    """
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        # Live-PostgreSQL tests reload config/db while DATABASE_URL points at a
        # throwaway database.  Restoring DB_PATH alone leaves the reloaded db
        # module's engine selector on PostgreSQL, so later SQLite fixtures can
        # connect to a dropped database or bypass their verified temp path.
        if module_name == "config" and module is not None:
            if hasattr(module, "DATABASE_URL"):
                setattr(module, "DATABASE_URL", "")
            if hasattr(module, "USE_POSTGRES"):
                setattr(module, "USE_POSTGRES", False)
        if module_name == "db" and module is not None:
            close_pool = getattr(module, "close_pg_pool", None)
            if callable(close_pool) and getattr(module, "_pg_pool", None) is not None:
                close_pool()
            if hasattr(module, "DATABASE_URL"):
                setattr(module, "DATABASE_URL", "")
            if hasattr(module, "USE_POSTGRESQL"):
                setattr(module, "USE_POSTGRESQL", False)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _sqlite_test_schema_present(path):
    """Return whether the shared disposable SQLite DB still has core schema."""
    if not os.path.exists(path):
        return False
    try:
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error:
        return False


def _candidate_test_db_paths():
    paths = []
    env_path = os.environ.get("DB_PATH")
    if env_path:
        paths.append(env_path)

    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        module_path = getattr(module, "DB_PATH", None) if module is not None else None
        if module_path:
            paths.append(module_path)

    deduped = []
    for path in paths:
        if path and path not in deduped:
            deduped.append(path)
    return deduped


def _ensure_active_actor_in_path(path, user_id, role, name, actor_type):
    conn = sqlite3.connect(path)
    try:
        if actor_type == "client":
            conn.execute(
                """
                INSERT OR IGNORE INTO clients
                    (id, email, password_hash, company_name, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (
                    user_id,
                    f"{user_id}@test.local",
                    "test-token-only",
                    name or user_id,
                ),
            )
        else:
            db_role = role if role in _OFFICER_ROLES else "analyst"
            conn.execute(
                """
                INSERT OR IGNORE INTO users
                    (id, email, password_hash, full_name, role, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (
                    user_id,
                    f"{user_id}@test.local",
                    "test-token-only",
                    name or user_id,
                    db_role,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _ensure_active_actor_for_token(user_id, role, name, token_type):
    """Backfill a real active DB actor for tests that mint JWTs directly.

    Production auth now revalidates every token against current DB actor state.
    This helper keeps old tests honest by creating the actor they claim to
    authenticate as; it intentionally does not alter existing rows, so tests can
    still model stale roles or inactive users explicitly.
    """
    paths = _candidate_test_db_paths()
    if not paths:
        return

    actor_type = "client" if token_type == "client" or role == "client" else "officer"
    for path in paths:
        try:
            _ensure_active_actor_in_path(path, user_id, role, name, actor_type)
        except sqlite3.Error:
            # Some narrow unit tests mint tokens without an initialized app DB.
            # The protected endpoints still enforce DB-backed identity.
            continue


def pytest_configure(config):
    """Patch test token creation so direct JWTs map to active DB actors."""
    try:
        import auth
    except Exception:
        return

    original_create_token = getattr(auth, "_original_create_token_for_tests", None)
    if original_create_token is None:
        original_create_token = auth.create_token
        auth._original_create_token_for_tests = original_create_token

    def create_token_with_actor(user_id, role, name, token_type="officer"):
        _ensure_active_actor_for_token(user_id, role, name, token_type)
        return original_create_token(user_id, role, name, token_type)

    auth.create_token = create_token_with_actor


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    global _db_initialized
    path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    _sync_test_db_path(path)

    if not _db_initialized or not _sqlite_test_schema_present(path):
        # Remove stale DB from previous run
        try:
            os.unlink(path)
        except OSError:
            pass

        from db import init_db, seed_initial_data, get_db
        init_db()
        # Seed admin users so auth tests can find them
        try:
            conn = get_db()
            seed_initial_data(conn)
            conn.commit()
            conn.close()
        except Exception:
            pass  # Already seeded or non-critical
        _db_initialized = True

    yield path


@pytest.fixture
def db(temp_db):
    """Get a database connection to the temp database."""
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def insert_verified_required_documents(db, app_or_id):
    """Insert verified Agent 1 evidence for every current KYC document slot.

    Approval and memo tests that are not about document readiness should call
    this helper so their fixtures satisfy the same canonical policy production
    code enforces. Existing current documents are left untouched.
    """
    from document_reliance_gate import build_required_document_expectations

    if isinstance(app_or_id, dict):
        app = app_or_id
        app_id = app.get("id")
    else:
        app_id = app_or_id
        row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        app = dict(row) if row else {"id": app_id}
    if not app_id:
        return []

    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    inserted = []
    for expectation in build_required_document_expectations(db, app):
        slot_key = expectation["slot_key"]
        existing = db.execute(
            """
            SELECT id
              FROM documents
             WHERE application_id=?
               AND slot_key=?
               AND COALESCE(is_current, 1)=1
             LIMIT 1
            """,
            (app_id, slot_key),
        ).fetchone()
        if existing:
            continue

        doc_type = expectation["doc_type"]
        safe_slot = re.sub(r"[^a-zA-Z0-9_-]+", "-", slot_key).strip("-")[:80]
        doc_id = f"fixture-doc-{uuid.uuid4().hex[:10]}-{safe_slot}"
        db.execute(
            """
            INSERT INTO documents
            (id, application_id, person_id, doc_type, doc_name, file_path, slot_key,
             verification_status, verification_results, verified_at, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?, ?, 'pending')
            """,
            (
                doc_id,
                app_id,
                expectation.get("person_id"),
                doc_type,
                f"{doc_type}.pdf",
                f"/tmp/{app_id}/{safe_slot or doc_type}.pdf",
                slot_key,
                json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": verified_at}),
                verified_at,
            ),
        )
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
            """,
            (app_id, doc_id, json.dumps([{"result": "pass"}])),
        )
        inserted.append(doc_id)

    db.commit()
    return inserted


@pytest.fixture
def app(temp_db):
    """Create a Tornado application for testing."""
    from server import make_app
    return make_app()


@pytest.fixture
def auth_token():
    """Generate a valid officer auth token."""
    from server import create_token
    return create_token("admin001", "admin", "Test Admin", "officer")


@pytest.fixture
def client_token(db):
    """Generate a valid client auth token and ensure client exists."""
    import bcrypt
    from server import create_token

    pw = bcrypt.hashpw("TestPass123!".encode(), bcrypt.gensalt()).decode()
    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        ("testclient001", "test@example.com", pw, "Test Company")
    )
    db.commit()
    return create_token("testclient001", "client", "Test Client", "client")


@pytest.fixture
def sample_application(db, client_token, request):
    """Create a sample application for testing (unique per test)."""
    from server import decode_token
    import uuid
    user = decode_token(client_token)

    from fixture_safe_refs import fixture_safe_suffix

    uid = fixture_safe_suffix(8, prefix="ARF-2026-")
    app_id = f"testapp_{uid}"
    ref = f"ARF-2026-{uid}"
    db.execute("""
        INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, ref, user["sub"], "Test Corp Ltd", "Mauritius", "Technology", "SME", "draft", "MEDIUM", 50))
    db.commit()
    return app_id


@pytest.fixture
def mock_screening(monkeypatch):
    """Mock external screening APIs to avoid real HTTP calls."""
    def mock_sanctions(name, birth_date=None, nationality=None, entity_type="Person"):
        return {
            "matched": False, "results": [], "total_checked": 1,
            "source": "mocked", "api_status": "mocked",
            "screened_at": "2026-01-01T00:00:00"
        }

    def mock_company(company_name, jurisdiction=None):
        return {
            "found": True, "companies": [{
                "name": company_name, "company_number": "C12345",
                "jurisdiction": jurisdiction or "mu", "status": "Active"
            }],
            "total_results": 1, "source": "mocked", "api_status": "mocked",
            "searched_at": "2026-01-01T00:00:00"
        }

    def mock_geolocate(ip):
        return {
            "ip": ip, "country": "Mauritius", "country_code": "MU",
            "risk_level": "LOW", "source": "mocked",
            "is_vpn": False, "is_proxy": False, "is_tor": False
        }

    import server
    monkeypatch.setattr(server, "screen_sumsub_aml", mock_sanctions)
    monkeypatch.setattr(server, "lookup_opencorporates", mock_company)
    monkeypatch.setattr(server, "geolocate_ip", mock_geolocate)

    # Sprint 3.5: Also patch the screening module directly — run_full_screening
    # calls these via its own module namespace, not server's re-exports.
    # Without this, _simulate_aml_screen's random.random() < 0.08 causes flakes.
    import screening
    monkeypatch.setattr(screening, "screen_sumsub_aml", mock_sanctions)
    monkeypatch.setattr(screening, "lookup_opencorporates", mock_company)
    monkeypatch.setattr(screening, "geolocate_ip", mock_geolocate)


# ═══ MEMO FIXTURES ═══

def make_base_memo(overrides=None):
    """Build a valid baseline memo for testing. Override any section/metadata."""
    memo = {
        "sections": {
            "executive_summary": {"content": "Low-risk Technology company domiciled in Mauritius. Clean screening."},
            "client_overview": {"content": "Test Corp Ltd, SME, Technology sector."},
            "ownership_and_control": {
                "content": "UBO1 holds 80% ownership.",
                "structure_complexity": "Simple",
                "control_statement": "John Doe exercises effective control via 80% direct shareholding."
            },
            "risk_assessment": {
                "content": "Overall risk: MEDIUM",
                "sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM", "content": "Mauritius — offshore jurisdiction"},
                    "business_risk": {"rating": "LOW", "content": "Technology sector"},
                    "transaction_risk": {"rating": "MEDIUM", "content": "Standard volume"},
                    "ownership_risk": {"rating": "MEDIUM", "content": "Clear ownership"},
                    "financial_crime_risk": {"rating": "LOW", "content": "No PEP or sanctions exposure"}
                }
            },
            "screening_results": {"content": "Screening completed via Onboarda screening engine. No sanctions matches across UN, EU, OFAC, HMT."},
            "document_verification": {"content": "All documents verified. Consistent with submitted data. No discrepancies."},
            "ai_explainability": {
                "content": "Risk assessed via multi-agent pipeline with weighted factor analysis.",
                "risk_increasing_factors": ["Limited trading history", "Offshore jurisdiction"],
                "risk_decreasing_factors": ["Clean sanctions screening", "Verified ownership", "Low sector risk"]
            },
            "red_flags_and_mitigants": {
                "red_flags": ["Limited trading history in this jurisdiction", "Offshore domiciliation increases monitoring burden"],
                "mitigants": ["Clean screening across all consolidated lists", "Transparent single-tier ownership structure"]
            },
            "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS", "content": "Approved with enhanced monitoring conditions."},
            "ongoing_monitoring": {"content": "Enhanced monitoring tier. Quarterly review. Transaction monitoring active."},
            "audit_and_governance": {"content": "Full audit trail maintained. 10-agent pipeline."}
        },
        "metadata": {
            "risk_rating": "MEDIUM",
            "risk_score": 45,
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
            "confidence_level": 0.78,
            "original_risk_level": "MEDIUM",
            "aggregated_risk": "MEDIUM",
            "rule_engine": {
                "violations": [],
                "enforcements": [],
                "engine_status": "CLEAN"
            }
        }
    }
    if overrides:
        _deep_merge(memo, overrides)
    return memo


def _deep_merge(base, override):
    """Recursively merge override into base dict."""
    for k, v in override.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def shutdown_test_http_server(thread, server_ref, timeout=15.0):
    """Stop a background test HTTP server thread and FAIL LOUDLY if it leaks.

    The historical copy-pasted teardown used ``thread.join(timeout=2)`` with no
    liveness check: on a slow runner the join expired and a live Tornado server
    (whose handlers resolve db.DB_PATH at call time) silently survived into
    later test files, racing their databases — the mechanism behind the
    order-dependent "seeded rows came back empty" flakes
    (test_directors_ubos_report, test_rmi_requests).
    """
    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise AssertionError(
            "Background test HTTP server thread failed to stop within "
            f"{timeout}s — a leaked server poisons every later test file "
            "(it reads/writes whatever DB_PATH the current test has bound). "
            "Fix the hang instead of lowering this timeout."
        )


@pytest.fixture(autouse=True)
def _reset_shared_rate_limiter():
    """base_handler.rate_limiter is a process-global singleton keyed by client
    IP + endpoint — and every in-process test server sees 127.0.0.1. Any suite
    that drives real HTTP (test_api, the monitoring/API suites) consumes later
    suites' budget for the same endpoint, producing wall-clock-dependent 429
    flakes (e.g. test_rmi_requests' decision calls failing after test_api).
    Clearing between tests keeps within-test rate-limit assertions intact while
    removing cross-file coupling."""
    try:
        import base_handler
        base_handler.rate_limiter._attempts.clear()
    except Exception:
        pass
    # BSA-002 / R2-BSA-016: endpoints now route through the DB-backed shared
    # limiter (shared_rate_limits) — doc_upload, ai_verify, document_verify,
    # supervisor pipeline triggers, enhanced-requirement uploads, forgot/reset
    # password. The in-memory clear above no longer covers them, so 127.0.0.1's
    # cumulative budget would carry across files and reproduce the exact 429
    # flake this fixture exists to prevent. Clear the DB-backed store too.
    try:
        from db import get_db
        db = get_db()
        try:
            db.execute("DELETE FROM shared_rate_limits")
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
    yield

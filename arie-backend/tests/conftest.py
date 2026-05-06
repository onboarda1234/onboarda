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

# Add parent directory to path so we can import server modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENVIRONMENT"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"


_db_initialized = False


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
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    global _db_initialized
    path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    _sync_test_db_path(path)

    if not _db_initialized:
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

    uid = uuid.uuid4().hex[:8]
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

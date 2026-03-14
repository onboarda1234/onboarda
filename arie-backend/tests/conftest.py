"""
ARIE Finance — Test Fixtures
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


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    # Import after setting env vars
    from server import init_db, get_db
    init_db()

    yield path

    os.unlink(path)


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
def sample_application(db, client_token):
    """Create a sample application for testing."""
    from server import decode_token
    user = decode_token(client_token)

    app_id = "testapp001"
    db.execute("""
        INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, "ARF-2026-TEST001", user["sub"], "Test Corp Ltd", "Mauritius", "Technology", "SME", "draft"))
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
    monkeypatch.setattr(server, "screen_opensanctions", mock_sanctions)
    monkeypatch.setattr(server, "lookup_opencorporates", mock_company)
    monkeypatch.setattr(server, "geolocate_ip", mock_geolocate)

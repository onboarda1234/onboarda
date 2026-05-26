"""Regression tests for demo auth hardening in frontend assets."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = ROOT / "arie-portal.html"
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backoffice_no_hardcoded_admin123_password():
    content = _read(BACKOFFICE_HTML)
    assert "admin123" not in content


def test_frontend_no_public_demo_credentials_field_usage():
    portal = _read(PORTAL_HTML)
    backoffice = _read(BACKOFFICE_HTML)
    assert "demo_credentials" not in portal
    assert "demo_credentials" not in backoffice


def test_frontend_no_client_side_demo_token_minting():
    portal = _read(PORTAL_HTML)
    backoffice = _read(BACKOFFICE_HTML)
    assert "token: 'demo_'" not in portal
    assert "token: 'demo_'" not in backoffice

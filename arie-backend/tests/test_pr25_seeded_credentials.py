"""PR-25 (M14) — unique seeded passwords, never logged.

seed_initial_data historically derived ONE password + ONE bcrypt hash and
inserted that identical hash into all four seeded officer/admin accounts (a
shared privileged bootstrap credential), and printed the secret to stdout on
the fallback path (CloudWatch would retain it). These tests lock: each account
gets a distinct secret/hash, the admin honours ADMIN_INITIAL_PASSWORD, no
account reuses the admin password, secrets are never printed, and out-of-band
delivery has no side effects under pytest.
"""
import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── secret-selection logic (unit) ────────────────────────────────────────────

def test_admin_honours_env_password():
    import db as db_module
    secret, generated = db_module._seed_account_secret("admin", "operator-set-pw")
    assert secret == "operator-set-pw"
    assert generated is False


def test_non_admin_never_uses_env_password():
    import db as db_module
    secret, generated = db_module._seed_account_secret("sco", "operator-set-pw")
    assert generated is True
    assert secret != "operator-set-pw"
    assert secret  # non-empty random secret


def test_admin_random_when_env_unset():
    import db as db_module
    for empty in ("", None):
        secret, generated = db_module._seed_account_secret("admin", empty)
        assert generated is True
        assert secret


def test_generated_secrets_are_distinct():
    import db as db_module
    seen = {db_module._seed_account_secret("sco", "")[0] for _ in range(50)}
    assert len(seen) == 50, "each generated secret must be unique"


# ── seeded DB: distinct hashes (the core defect) ─────────────────────────────

def test_seeded_accounts_have_distinct_hashes(db):
    rows = db.execute(
        "SELECT id, password_hash FROM users "
        "WHERE id IN ('admin001','sco001','co001','analyst001')"
    ).fetchall()
    hashes = [r["password_hash"] for r in rows]
    assert len(hashes) == 4, "all four seeded accounts must exist"
    assert len(set(hashes)) == 4, "seeded accounts must have DISTINCT password hashes (PR-25 M14)"


# ── secret never printed to stdout ───────────────────────────────────────────

def test_seed_does_not_print_the_secret():
    src = open(os.path.join(BACKEND, "db.py"), encoding="utf-8").read()
    assert "INITIAL ADMIN PASSWORD (save this now)" not in src, \
        "seed still prints the initial password to stdout (PR-25 regressed)"


# ── delivery: no side effects under pytest ───────────────────────────────────

def test_delivery_has_no_side_effects_under_pytest():
    import db as db_module
    path = os.path.join(BACKEND, "uploads", ".seeded_credentials")
    existed = os.path.exists(path)
    db_module._deliver_seeded_credentials({"x@example.com": "super-secret"})
    assert "pytest" in sys.modules
    if not existed:
        assert not os.path.exists(path), "delivery must be a no-op under pytest"


# ── live PostgreSQL: seeding produces distinct hashes there too ───────────────

def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def fresh_pg(monkeypatch):
    base_dsn = _pg_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN available")
    import psycopg2
    from urllib.parse import urlsplit, urlunsplit
    db_name = f"pr25_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(base_dsn)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    except Exception:
        admin.close()
        raise
    fresh_dsn = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))
    orig = os.environ.get("DATABASE_URL")
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "development")
        import config as config_module
        import db as db_module
        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module.init_db()
        conn = db_module.get_db()
        db_module.seed_initial_data(conn)
        conn.commit()
        yield db_module
    finally:
        if orig is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig
        try:
            import config as config_module
            import db as db_module
            importlib.reload(config_module)
            importlib.reload(db_module)
        except Exception:
            pass
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        except Exception:
            pass
        admin.close()


def test_pg_seeded_accounts_have_distinct_hashes(fresh_pg):
    db = fresh_pg.get_db()
    try:
        rows = db.execute(
            "SELECT password_hash FROM users "
            "WHERE id IN ('admin001','sco001','co001','analyst001')"
        ).fetchall()
        hashes = [r["password_hash"] for r in rows]
        assert len(hashes) == 4
        assert len(set(hashes)) == 4, "seeded accounts must have DISTINCT hashes on PostgreSQL too"
    finally:
        db.close()

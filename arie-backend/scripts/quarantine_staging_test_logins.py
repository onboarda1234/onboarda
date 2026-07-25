#!/usr/bin/env python3
"""Quarantine synthetic/test logins on staging (staging-SHA gate row, ops half).

Origin: ADMIN-AUDIT-006 ("several test-like users in staging"; recommended fix
"remove or quarantine stale test accounts"). The before/after-state audit half
shipped with the user handlers; this is the account half.

**Quarantine, not delete — deliberately.** `users` and `clients` are not in
REGULATED_TABLES, so a hard DELETE would not be refused by the deletion
interceptor, but ~20 tables carry `REFERENCES users(id)`: deleting an officer
who ever acted either fails on the FK or orphans referential history that AML
recordkeeping depends on. Setting `status='inactive'` blocks login while
preserving every audit and decision linkage. Hard deletion is offered ONLY for
accounts with no activity anywhere (`--delete-unused`), and even then it is
opt-in and reports what it would remove first.

Default mode is DRY-RUN. Never targets production: the same identity guard the
role-matrix harness uses is applied here.

Usage:
    python scripts/quarantine_staging_test_logins.py                  # dry-run
    python scripts/quarantine_staging_test_logins.py --apply
    python scripts/quarantine_staging_test_logins.py --apply --delete-unused
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRODUCTION_MARKERS = ("production", "prod-db", "prod_", "-prod", ".prod.")

# Seeded demo personas. These are fictional and must not survive a pilot
# window; the founder account is protected below and never appears here.
TEST_OFFICER_EMAILS = (
    "raj.patel@onboarda.com",
    "m.dubois@onboarda.com",
    "l.wei@onboarda.com",
)

TEST_CLIENT_EMAILS = (
    "demo@onboarda.com",
)

# Email suffixes that only ever belong to synthetic fixtures.
SYNTHETIC_SUFFIXES = (
    "@example.test",
    "@fixture.invalid",
    "@p13.test",
)

# NEVER quarantine. The founder/owner account is a real operator login, and
# the CI smoke user is re-created AND force-reactivated on every boot by
# db.ensure_qa_smoke_user() — quarantining it would be undone by the next
# deploy, so it is excluded rather than fought with.
PROTECTED_EMAILS = (
    "asudally@onboarda.com",
    "github-actions-day6-staging-smoke@onboarda.internal",
)

# Tables whose presence proves an officer acted (so the row must be kept).
ACTIVITY_TABLES = (
    ("audit_log", "user_id"),
    ("applications", "assigned_to"),
    ("applications", "decision_by"),
)


def assert_not_production(database_url: str, environment: str) -> None:
    """Fail closed unless this is clearly a non-production database."""
    if environment.lower() in ("production", "prod"):
        raise RuntimeError("REFUSED: this script never runs against production.")
    parsed = urlparse(database_url or "")
    identity = f"{(parsed.hostname or '').lower()}/{parsed.path.lstrip('/').lower()}"
    if any(marker in identity for marker in PRODUCTION_MARKERS):
        raise RuntimeError(
            f"REFUSED: DATABASE_URL identity {identity!r} contains a production marker.")


def _matches_synthetic(email: str) -> bool:
    email = (email or "").lower()
    return any(email.endswith(suffix) for suffix in SYNTHETIC_SUFFIXES)


def classify_officer(email: str) -> str:
    """Return 'protected', 'test-persona', 'synthetic' or 'keep'."""
    email = (email or "").lower()
    if email in {value.lower() for value in PROTECTED_EMAILS}:
        return "protected"
    if email in {value.lower() for value in TEST_OFFICER_EMAILS}:
        return "test-persona"
    if _matches_synthetic(email):
        return "synthetic"
    return "keep"


def classify_client(email: str) -> str:
    email = (email or "").lower()
    if email in {value.lower() for value in PROTECTED_EMAILS}:
        return "protected"
    if email in {value.lower() for value in TEST_CLIENT_EMAILS}:
        return "test-persona"
    if _matches_synthetic(email):
        return "synthetic"
    return "keep"


def _has_activity(db, user_id: str) -> bool:
    for table, column in ACTIVITY_TABLES:
        try:
            row = db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?", (user_id,)
            ).fetchone()
        except Exception:
            # A missing table must never be read as "no activity".
            return True
        if row and row["c"]:
            return True
    return False


def plan_quarantine(db) -> Dict[str, Any]:
    """Build the plan without mutating anything."""
    officers: List[Dict[str, Any]] = []
    for row in db.execute(
            "SELECT id, email, full_name, role, status FROM users").fetchall():
        verdict = classify_officer(row["email"])
        if verdict in ("protected", "keep"):
            continue
        officers.append({
            "id": row["id"], "email": row["email"], "role": row["role"],
            "status": row["status"], "reason": verdict,
            "has_activity": _has_activity(db, row["id"]),
        })

    clients: List[Dict[str, Any]] = []
    for row in db.execute(
            "SELECT id, email, company_name, status FROM clients").fetchall():
        verdict = classify_client(row["email"])
        if verdict in ("protected", "keep"):
            continue
        clients.append({
            "id": row["id"], "email": row["email"],
            "status": row["status"], "reason": verdict,
        })

    return {"officers": officers, "clients": clients}


def apply_quarantine(db, plan: Dict[str, Any], *, delete_unused: bool = False) -> Dict[str, Any]:
    """Deactivate every planned account; optionally hard-delete inactive ones
    that have no activity anywhere."""
    deactivated_officers, deleted_officers = [], []
    for officer in plan["officers"]:
        if delete_unused and not officer["has_activity"]:
            db.execute("DELETE FROM users WHERE id = ?", (officer["id"],))
            deleted_officers.append(officer["id"])
            continue
        db.execute("UPDATE users SET status='inactive' WHERE id = ?", (officer["id"],))
        deactivated_officers.append(officer["id"])

    deactivated_clients = []
    for client in plan["clients"]:
        db.execute("UPDATE clients SET status='inactive' WHERE id = ?", (client["id"],))
        deactivated_clients.append(client["id"])

    db.commit()
    return {
        "deactivated_officers": deactivated_officers,
        "deleted_officers": deleted_officers,
        "deactivated_clients": deactivated_clients,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Perform the quarantine (default: dry-run)")
    parser.add_argument("--delete-unused", action="store_true",
                        help="Hard-delete accounts with no activity anywhere "
                             "(requires --apply)")
    args = parser.parse_args(argv)

    assert_not_production(os.environ.get("DATABASE_URL", ""),
                          os.environ.get("ENVIRONMENT", ""))

    from db import get_db

    db = get_db()
    try:
        plan = plan_quarantine(db)
        print(json.dumps(plan, indent=2))
        print(f"\n{len(plan['officers'])} officer account(s) and "
              f"{len(plan['clients'])} client account(s) match.")
        for officer in plan["officers"]:
            if officer["has_activity"]:
                print(f"  KEEP ROW (has activity): {officer['email']} — "
                      "deactivate only, never delete")
        if not args.apply:
            print("\nDRY-RUN — pass --apply to deactivate. "
                  "Nothing was changed.")
            return 0
        result = apply_quarantine(db, plan, delete_unused=args.delete_unused)
        print("\n" + json.dumps(result, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

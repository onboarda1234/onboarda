#!/usr/bin/env python3
"""Quarantine synthetic/test logins on staging (staging-SHA gate row, ops half).

Origin: ADMIN-AUDIT-006 ("several test-like users in staging"; recommended fix
"remove or quarantine stale test accounts"). The before/after-state audit half
shipped with the user handlers; this is the account half.

**Quarantine ONLY — this script never deletes.** `users` and `clients` are not
in REGULATED_TABLES, so a hard DELETE would NOT be refused by the deletion
interceptor and (under SQLite's `PRAGMA foreign_keys=0`) would silently
succeed. But 22 tables carry 48 `REFERENCES users(id)` columns — memo
approver, SAR reviewer, screening second reviewer, EDD decider, periodic-review
decider, `applications.pre_approval_officer_id` — and orphaning any of them
destroys the attribution AML recordkeeping depends on. An earlier revision of
this script offered `--delete-unused` behind a three-table activity check;
adversarial review proved it would hard-delete an officer who had approved
compliance memos. There is no delete path here at all. Deactivation satisfies
ADMIN-AUDIT-006 ("remove **or quarantine**").

Every status change writes an `audit_log` row with before/after state, matching
what the application's own user-status control records.

Default mode is DRY-RUN, and `--apply` requires the same POSITIVE guard set the
role-matrix harness uses: explicit staging environment + allowlist var +
confirm token + an EXACT host match between the resolved connection and
QUARANTINE_ALLOWED_DB_HOST. Codex validation 2026-07-26 proved the earlier
denylist-only identity check accepted the live demo database and an arbitrary
PostgreSQL host; a denylist proves "not obviously production", never "the
staging database".

Usage:
    python scripts/quarantine_staging_test_logins.py     # dry-run
    ENVIRONMENT=staging ALLOW_TEST_LOGIN_QUARANTINE=1 \
    QUARANTINE_ALLOWED_DB_HOST=<staging-rds-hostname> \
    python scripts/quarantine_staging_test_logins.py \
        --apply --confirm I-UNDERSTAND-STAGING-LOGIN-QUARANTINE
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
CONFIRM_TOKEN = "I-UNDERSTAND-STAGING-LOGIN-QUARANTINE"
ALLOW_ENV = "ALLOW_TEST_LOGIN_QUARANTINE"
# Positive identity allowlist (Codex validation 2026-07-26): the previous
# guard proved only "PostgreSQL and not obviously production" — it accepted
# the live demo database and an arbitrary attacker host. The operator must
# name the exact staging DB host, mirroring the role-matrix harness's
# ROLE_AUDIT_ALLOWED_DB_HOST.
ALLOWED_HOST_ENV = "QUARANTINE_ALLOWED_DB_HOST"

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

def assert_quarantine_allowed(database_url: str, environment: str,
                              allow_value: str = None, confirm: str = None,
                              allowed_host: str = None) -> None:
    """Fail CLOSED — positive checks only, mirroring enforce_staging_seed_guard.

    An earlier revision checked only "environment is not production" plus a
    denylist, which passed with BOTH variables unset and passed for the live
    demo environment (whose `demo@onboarda.com` client is a real login behind
    demo.regmind.co). Absence of evidence is not evidence of staging.
    """
    environment = str(environment or "").lower()
    if environment != "staging":
        raise RuntimeError(
            f"REFUSED: ENVIRONMENT must be exactly 'staging' (got {environment or 'unset'}).")
    if (allow_value if allow_value is not None else os.environ.get(ALLOW_ENV, "")) != "1":
        raise RuntimeError(f"REFUSED: {ALLOW_ENV}=1 is required.")
    if confirm != CONFIRM_TOKEN:
        raise RuntimeError(f"REFUSED: --confirm must equal {CONFIRM_TOKEN!r}.")
    if not database_url:
        raise RuntimeError("REFUSED: no database identity resolved — refusing to guess.")
    parsed = urlparse(database_url)
    if parsed.scheme.lower() not in ("postgres", "postgresql"):
        raise RuntimeError(
            "REFUSED: the staging database must be PostgreSQL; refusing to run "
            f"against {parsed.scheme or 'an unidentified engine'}.")
    host = (parsed.hostname or "").lower()
    identity = f"{host}/{parsed.path.lstrip('/').lower()}"
    # POSITIVE allowlist first — a denylist proves "not obviously production",
    # not "the staging database". The demo DB and an arbitrary PG host both
    # sailed through the denylist-only revision.
    allowed_host = (allowed_host if allowed_host is not None
                    else os.environ.get(ALLOWED_HOST_ENV, "")).strip().lower()
    if not allowed_host:
        raise RuntimeError(
            f"REFUSED: {ALLOWED_HOST_ENV} must name the exact staging DB host "
            "(compare the harness's ROLE_AUDIT_ALLOWED_DB_HOST).")
    if host != allowed_host:
        raise RuntimeError(
            f"REFUSED: resolved DB host {host!r} does not match "
            f"{ALLOWED_HOST_ENV}={allowed_host!r}.")
    # Denylist retained as belt-and-braces beneath the allowlist.
    if any(marker in identity for marker in PRODUCTION_MARKERS):
        raise RuntimeError(
            f"REFUSED: database identity {identity!r} contains a production marker.")


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


def plan_quarantine(db) -> Dict[str, Any]:
    """Build the plan without mutating anything."""
    officers: List[Dict[str, Any]] = []
    for row in db.execute(
            "SELECT id, email, full_name, role, status FROM users").fetchall():
        verdict = classify_officer(row["email"])
        if verdict in ("protected", "keep"):
            continue
        # Already-quarantined accounts are NOT re-planned: the runbook tells
        # the operator to re-run the dry-run and expect an empty list, and a
        # status-blind plan would report "failed" after a successful run.
        if str(row["status"]).lower() != "active":
            continue
        officers.append({
            "id": row["id"], "email": row["email"], "role": row["role"],
            "status": row["status"], "reason": verdict,
        })

    clients: List[Dict[str, Any]] = []
    for row in db.execute(
            "SELECT id, email, company_name, status FROM clients").fetchall():
        verdict = classify_client(row["email"])
        if verdict in ("protected", "keep"):
            continue
        if str(row["status"]).lower() != "active":
            continue
        clients.append({
            "id": row["id"], "email": row["email"],
            "status": row["status"], "reason": verdict,
        })

    return {"officers": officers, "clients": clients}


def apply_quarantine(db, plan: Dict[str, Any], *, actor: str = "ops-quarantine-script") -> Dict[str, Any]:
    """Deactivate every planned account, writing an audit row for each.

    B5 (adversarial review): a bulk status change on a regulated system with no
    audit trail is itself a finding — the application's own control for this
    exact change records before/after state, so this must too. The audit write
    shares the transaction with the status change: no silent status flip.
    """
    deactivated_officers, deactivated_clients = [], []

    for officer in plan["officers"]:
        db.execute("UPDATE users SET status='inactive' WHERE id = ?", (officer["id"],))
        _write_audit(db, actor, "User Status Change", f"user:{officer['id']}", {
            "target_email": officer["email"],
            "target_role": officer["role"],
            "before_state": {"status": officer["status"]},
            "after_state": {"status": "inactive"},
            "reason": f"staging test-login quarantine ({officer['reason']})",
            "source": "scripts/quarantine_staging_test_logins.py",
        })
        deactivated_officers.append(officer["id"])

    for client in plan["clients"]:
        db.execute("UPDATE clients SET status='inactive' WHERE id = ?", (client["id"],))
        _write_audit(db, actor, "Client Status Change", f"client:{client['id']}", {
            "target_email": client["email"],
            "before_state": {"status": client["status"]},
            "after_state": {"status": "inactive"},
            "reason": f"staging test-login quarantine ({client['reason']})",
            "source": "scripts/quarantine_staging_test_logins.py",
        })
        deactivated_clients.append(client["id"])

    db.commit()
    return {
        "deactivated_officers": deactivated_officers,
        "deactivated_clients": deactivated_clients,
    }


def _write_audit(db, actor: str, action: str, target: str, detail: Dict[str, Any]) -> None:
    db.execute(
        """
        INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address, request_id)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (actor, "Test Login Quarantine (ops script)", "system", action, target,
         json.dumps(detail, sort_keys=True), "local-operator", f"quarantine:{actor}"),
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Perform the quarantine (default: dry-run)")
    parser.add_argument("--confirm", default="",
                        help=f"Must equal {CONFIRM_TOKEN!r} when --apply is used")
    parser.add_argument("--actor", default="ops-quarantine-script",
                        help="Attribution recorded on the audit rows")
    args = parser.parse_args(argv)

    from db import get_db
    import db as db_module

    # Guard the identity ACTUALLY connected to, not whatever DATABASE_URL
    # happens to say — db resolves its target at import time.
    resolved = getattr(db_module, "DATABASE_URL", "") or ""

    if args.apply:
        assert_quarantine_allowed(
            resolved, os.environ.get("ENVIRONMENT", ""), confirm=args.confirm)

    db = get_db()
    try:
        plan = plan_quarantine(db)
        print(json.dumps(plan, indent=2))
        print(f"\n{len(plan['officers'])} officer account(s) and "
              f"{len(plan['clients'])} client account(s) will be DEACTIVATED "
              "(no account is ever deleted by this script).")
        if not args.apply:
            print("\nDRY-RUN — nothing was changed. Re-run with --apply "
                  f"--confirm {CONFIRM_TOKEN} and {ALLOW_ENV}=1 to deactivate.")
            return 0
        result = apply_quarantine(db, plan, actor=args.actor)
        print("\n" + json.dumps(result, indent=2))
        print("\nEach change wrote an audit_log row with before/after state.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

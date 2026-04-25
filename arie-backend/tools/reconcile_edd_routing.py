#!/usr/bin/env python3
"""
reconcile_edd_routing
=====================

Priority E reconciliation utility.

Scans every non-fixture, non-terminal application and detects any of:

* Policy says EDD but ``onboarding_lane`` is not "EDD"
* Policy says EDD but no active ``edd_cases`` row exists
* Application has an active ``edd_cases`` row but policy says standard
* Multiple active ``edd_cases`` rows for the same application

Reports to stdout as a structured table by default. With ``--apply``
it actuates the policy decision via ``apply_routing_decision`` so
``edd_cases`` rows are upserted (idempotent) and ``onboarding_lane``
is corrected for the EDD route.

This utility is intentionally a CLI script -- not a public HTTP
endpoint -- so it cannot be triggered by web clients. It is the
backstop for the fact that the live wiring is fail-soft (errors
during prescreening / recompute don't break onboarding, but they
also don't surface; this scanner finds them later).

Usage::

    python -m tools.reconcile_edd_routing                # report only
    python -m tools.reconcile_edd_routing --apply        # heal drift
    python -m tools.reconcile_edd_routing --ref ARF-...  # single case
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

# Make sibling backend modules importable when running as a script
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


TERMINAL_STATUSES = ("approved", "rejected", "withdrawn", "cancelled")
TERMINAL_EDD_STAGES = ("edd_approved", "edd_rejected")


def _scan(db, ref: Optional[str] = None) -> List[Dict[str, Any]]:
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD
    from routing_actuator import build_routing_facts

    where = (
        "WHERE COALESCE(is_fixture, 0) = 0 "
        "AND status NOT IN ('approved','rejected','withdrawn','cancelled')"
    )
    params: tuple = ()
    if ref:
        where = "WHERE ref = ?"
        params = (ref,)

    rows = db.execute(
        f"SELECT * FROM applications {where} ORDER BY id ASC", params
    ).fetchall()

    findings: List[Dict[str, Any]] = []
    for row in rows:
        ar = dict(row)
        facts = build_routing_facts(app_row=ar)
        try:
            decision = evaluate_edd_routing(facts)
        except Exception as e:
            findings.append(
                {
                    "ref": ar.get("ref"),
                    "id": ar.get("id"),
                    "kind": "policy_error",
                    "detail": str(e),
                }
            )
            continue

        route = decision.get("route")
        triggers = decision.get("triggers") or []
        lane = ar.get("onboarding_lane") or ""

        active_edd = db.execute(
            "SELECT id, stage FROM edd_cases "
            "WHERE application_id = ? "
            "AND stage NOT IN ('edd_approved','edd_rejected') "
            "ORDER BY id ASC",
            (ar.get("id"),),
        ).fetchall()
        active_count = len(active_edd or [])

        # Drift type 1: policy=edd, lane!=EDD
        if route == ROUTE_EDD and lane != "EDD":
            findings.append(
                {
                    "ref": ar.get("ref"),
                    "id": ar.get("id"),
                    "kind": "lane_drift_to_edd",
                    "policy_route": route,
                    "lane": lane,
                    "triggers": triggers,
                }
            )

        # Drift type 2: policy=edd, no active edd_cases row
        if route == ROUTE_EDD and active_count == 0:
            findings.append(
                {
                    "ref": ar.get("ref"),
                    "id": ar.get("id"),
                    "kind": "missing_edd_case",
                    "policy_route": route,
                    "triggers": triggers,
                }
            )

        # Drift type 3: not EDD by policy but has an active EDD case
        # (treat as informational; we do NOT auto-close cases here, an
        # officer must review)
        if route != ROUTE_EDD and active_count > 0:
            findings.append(
                {
                    "ref": ar.get("ref"),
                    "id": ar.get("id"),
                    "kind": "edd_case_without_policy_trigger",
                    "policy_route": route,
                    "active_edd_case_count": active_count,
                }
            )

        # Drift type 4: duplicate active edd_cases
        if active_count > 1:
            findings.append(
                {
                    "ref": ar.get("ref"),
                    "id": ar.get("id"),
                    "kind": "duplicate_active_edd_cases",
                    "active_edd_case_count": active_count,
                }
            )

    return findings


def _heal(db, finding: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the policy decision idempotently for a single finding."""
    from routing_actuator import (
        apply_routing_decision,
        SOURCE_MANUAL_RECONCILIATION,
    )

    if finding.get("kind") not in (
        "lane_drift_to_edd",
        "missing_edd_case",
    ):
        return {"healed": False, "reason": "non_healable_kind"}

    row = db.execute(
        "SELECT * FROM applications WHERE id = ?", (finding.get("id"),)
    ).fetchone()
    if not row:
        return {"healed": False, "reason": "row_disappeared"}

    outcome = apply_routing_decision(
        db=db,
        app_row=dict(row),
        risk_dict=None,
        user={"sub": "reconciliation", "name": "reconciliation",
              "role": "system"},
        client_ip="",
        source=SOURCE_MANUAL_RECONCILIATION,
    )
    return {"healed": True, "outcome": outcome}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Heal drift instead of just reporting.")
    parser.add_argument("--ref", default=None,
                        help="Restrict to a single application ref.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON.")
    args = parser.parse_args()

    from db import get_db  # type: ignore

    db = get_db()
    try:
        findings = _scan(db, ref=args.ref)
        report: Dict[str, Any] = {
            "scanned_at": None,
            "ref_filter": args.ref,
            "applied": False,
            "drift_count": len(findings),
            "findings": findings,
        }

        if args.apply and findings:
            healed: List[Dict[str, Any]] = []
            for f in findings:
                healed.append({"finding": f, "result": _heal(db, f)})
            try:
                db.commit()
            except Exception:
                pass
            report["applied"] = True
            report["healed"] = healed

        if args.json:
            print(json.dumps(report, default=str, indent=2))
        else:
            print(f"Scanned. drift_count={report['drift_count']}"
                  f" applied={report['applied']}")
            for f in findings:
                print(f"  - {f.get('ref')}: {f.get('kind')}"
                      f" route={f.get('policy_route')}"
                      f" lane={f.get('lane')}"
                      f" triggers={f.get('triggers')}")
        return 0
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

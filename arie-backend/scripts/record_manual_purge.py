#!/usr/bin/env python3
"""Record evidence for an operator-performed manual retention purge.

P12-8 / DCI-020+021 — step 5 of docs/compliance/MANUAL_PURGE_PROCEDURE.md.
Writes the enriched data_purge_log evidence row (batch id, per-table counts,
subject/application scoping, approver) via gdpr.record_manual_purge.

Usage:
  python scripts/record_manual_purge.py \
    --category client_pii \
    --counts '{"clients": 3, "applications": 3}' \
    --reason "Q3 retention review: relationships ended 2019-06" \
    --purged-by ops-user-id --approved-by sco-user-id \
    [--subject-id CID] [--application-id APPID] \
    [--oldest 2018-01-01T00:00:00] [--newest 2019-06-30T23:59:59] \
    [--evidence '{"change_ticket": "OPS-123"}']

Requires DATABASE_URL (or the local SQLite dev DB) exactly like the server.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True)
    parser.add_argument("--counts", required=True,
                        help='JSON object {"table": deleted_count, ...}')
    parser.add_argument("--reason", required=True)
    parser.add_argument("--purged-by", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--application-id", default=None)
    parser.add_argument("--oldest", default=None,
                        help="oldest purged record date (ISO)")
    parser.add_argument("--newest", default=None,
                        help="newest purged record date (ISO)")
    parser.add_argument("--evidence", default=None,
                        help="JSON object with operator evidence "
                             "(change ticket, artefact counts, ...)")
    args = parser.parse_args()

    try:
        counts = json.loads(args.counts)
    except json.JSONDecodeError as e:
        print(f"--counts is not valid JSON: {e}", file=sys.stderr)
        return 2
    evidence = None
    if args.evidence:
        try:
            evidence = json.loads(args.evidence)
        except json.JSONDecodeError as e:
            print(f"--evidence is not valid JSON: {e}", file=sys.stderr)
            return 2

    from db import get_db
    from gdpr import record_manual_purge

    db = get_db()
    try:
        result = record_manual_purge(
            db,
            category=args.category,
            per_table_counts=counts,
            purge_reason=args.reason,
            purged_by=args.purged_by,
            approved_by=args.approved_by,
            subject_id=args.subject_id,
            application_id=args.application_id,
            oldest_record_date=args.oldest,
            newest_record_date=args.newest,
            evidence=evidence,
        )
    finally:
        db.close()

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") == "recorded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

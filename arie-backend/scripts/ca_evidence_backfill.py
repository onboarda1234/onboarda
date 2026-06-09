#!/usr/bin/env python3
"""Backfill structured evidence for historical ComplyAdvantage monitoring alerts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from db import get_db  # noqa: E402
from screening_complyadvantage.client import ComplyAdvantageClient  # noqa: E402
from screening_complyadvantage.config import CAConfig  # noqa: E402
from screening_complyadvantage.evidence_backfill import backfill_monitoring_alert_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist evidence rows. Defaults to dry-run.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum alerts to inspect.")
    parser.add_argument("--alert-id", action="append", dest="alert_ids", help="Specific alert id to reprocess. Repeatable.")
    parser.add_argument(
        "--fetch-live-details",
        action="store_true",
        help="Call CA detail APIs when stored normalized provider truth has no extractable evidence.",
    )
    parser.add_argument("--trace-id", help="Optional trace id for logs.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db = get_db()
    ca_client = None
    if args.fetch_live_details:
        ca_client = ComplyAdvantageClient(CAConfig.from_env())
    try:
        result = backfill_monitoring_alert_evidence(
            db,
            ca_client=ca_client,
            dry_run=not args.apply,
            limit=args.limit,
            alert_ids=args.alert_ids,
            fetch_live_details=args.fetch_live_details,
            trace_id=args.trace_id,
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

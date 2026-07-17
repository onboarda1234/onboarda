#!/usr/bin/env python3
"""SRP-2 — governed refresh of stale (pre-enrichment) screening reports.

Applications screened before the enriched ComplyAdvantage normalizer carry
stored reports whose hit rows have no matched names, match types, or media
evidence (SRP-0 fleet scan: 132 non-fixture hit-bearing candidates). This
harness re-screens them through the REAL /api/screening/run endpoint — so
memo staleness, risk recompute, CA audit events, and normalized dual-writes
happen exactly as they would for an officer-triggered re-screen — with the
governance the endpoint alone does not provide:

* the outgoing report is archived to ``screening_report_archive`` BEFORE the
  fresh report replaces it (screening evidence is regulated; never destroyed);
* applications with ANY officer adjudication (screening_reviews rows) are
  skipped and listed — replacing evidence under an existing disposition is an
  officer decision, not a batch job's;
* fixtures are excluded; production is refused; batches are capped and paced;
* every refresh writes an audit_log entry (old report hash -> new);
* apps already archived by this tool recently are skipped (re-run safety).

Dry-run is the default. Execution requires --execute and the confirmation
token I-UNDERSTAND-SRP2-CONTROLLED-RESCREEN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

CONFIRM_TOKEN = "I-UNDERSTAND-SRP2-CONTROLLED-RESCREEN"
ARCHIVE_REASON = "srp2_stale_report_refresh"
ARCHIVE_ACTOR = "refresh_stale_screening_reports"
DEFAULT_BATCH_LIMIT = 10
MAX_BATCH_LIMIT = 25
RECENT_ARCHIVE_SKIP_DAYS = 7

_UUIDISH_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)

_HIT_CONTAINER_KEYS = ("results",)
_SUB_RECORD_KEYS = ("sanctions", "adverse_media", "watchlists", "pep")


def _forbid_production():
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    if env in ("production", "prod", "live"):
        raise RuntimeError("refresh_stale_screening_reports refuses to run in production")


def _iter_screening_entries(report):
    """Yield every per-subject screening entry in a stored report."""
    if not isinstance(report, dict):
        return
    company = report.get("company_screening")
    if isinstance(company, dict):
        yield company
    for key in ("director_screenings", "ubo_screenings", "intermediary_screenings"):
        for entry in report.get(key) or []:
            if isinstance(entry, dict):
                yield entry


def flatten_hit_rows(report):
    """Flatten every provider hit row from a stored screening report."""
    rows = []
    for entry in _iter_screening_entries(report):
        for key in _HIT_CONTAINER_KEYS:
            for row in entry.get(key) or []:
                if isinstance(row, dict):
                    rows.append(row)
        for sub_key in _SUB_RECORD_KEYS:
            sub = entry.get(sub_key)
            if isinstance(sub, dict):
                for row in sub.get("results") or []:
                    if isinstance(row, dict):
                        rows.append(row)
    return rows


def _row_has_real_name(row):
    name = str(row.get("name") or row.get("matched_name") or "").strip()
    if not name:
        return False
    return not _UUIDISH_RE.match(name)


def classify_report(report):
    """Classify a stored report for refresh candidacy.

    Returns a dict with: hit_rows, rows_with_real_name, rows_with_match_types,
    and clazz in {"no_hits", "pre_enrichment", "enriched"}.

    A report is PRE-ENRICHMENT (blind) when it has hit rows but NONE of them
    carry a real matched name and NONE carry provider match types — the exact
    signature SRP-0 measured on ARF-2026-920016 (298 rows, 0 names, 0 match
    types). Partially-enriched reports are deliberately NOT auto-selected;
    they can be refreshed explicitly via --refs.
    """
    rows = flatten_hit_rows(report)
    named = sum(1 for r in rows if _row_has_real_name(r))
    typed = sum(1 for r in rows if r.get("provider_match_types"))
    if not rows:
        clazz = "no_hits"
    elif named == 0 and typed == 0:
        clazz = "pre_enrichment"
    else:
        clazz = "enriched"
    return {
        "hit_rows": len(rows),
        "rows_with_real_name": named,
        "rows_with_match_types": typed,
        "clazz": clazz,
    }


def adjudication_block_reason(db, application_id):
    """Any officer adjudication on the application blocks batch refresh."""
    row = db.execute(
        "SELECT COUNT(*) AS n FROM screening_reviews WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    count = row["n"] if row is not None else 0
    if count:
        return f"{count} screening review(s) recorded — officer-driven refresh required"
    return None


def recently_archived(db, application_id, *, days=RECENT_ARCHIVE_SKIP_DAYS):
    # Cutoff computed in Python: dialect-neutral (the wrapper only translates
    # the exact literal datetime('now'), not modifier forms).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    row = db.execute(
        """
        SELECT COUNT(*) AS n FROM screening_report_archive
         WHERE application_id = ? AND archived_by = ? AND archived_at >= ?
        """,
        (application_id, ARCHIVE_ACTOR, cutoff),
    ).fetchone()
    return bool(row and row["n"])


def report_hash(report):
    return hashlib.sha256(
        json.dumps(report, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def archive_current_report(db, app, report, *, reason=ARCHIVE_REASON):
    """Persist the outgoing report snapshot. Returns its hash."""
    digest = report_hash(report)
    db.execute(
        """
        INSERT INTO screening_report_archive
            (application_id, application_ref, archived_by, reason, report_hash, report_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            app["id"],
            app.get("ref"),
            ARCHIVE_ACTOR,
            reason,
            digest,
            json.dumps(report, default=str),
        ),
    )
    return digest


def select_candidates(db, *, refs=None, limit, force_refresh=False):
    """Select refresh candidates: non-fixture, hit-bearing, blind reports."""
    from fixture_filter import fixture_app_exclude_clause
    from prescreening.normalize import safe_json_loads

    query = "SELECT id, ref, company_name, prescreening_data, is_fixture FROM applications WHERE 1=1"
    params = []
    if refs:
        placeholders = ",".join("?" for _ in refs)
        query += f" AND ref IN ({placeholders})"
        params.extend(refs)
    else:
        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="", include_text_patterns=True)
        query += f" AND {fx_excl}"
        params.extend(fx_params)
    query += " ORDER BY created_at DESC"

    candidates, skipped = [], []
    for app in db.execute(query, params).fetchall():
        app = dict(app)
        prescreening = safe_json_loads(app.get("prescreening_data") or "{}")
        report = prescreening.get("screening_report")
        if not isinstance(report, dict):
            continue
        info = classify_report(report)
        explicitly_requested = bool(refs)
        if info["clazz"] != "pre_enrichment" and not explicitly_requested:
            continue
        entry = {
            "id": app["id"],
            "ref": app.get("ref"),
            "company_name": app.get("company_name"),
            "screened_at": report.get("screened_at"),
            **info,
        }
        block = adjudication_block_reason(db, app["id"])
        if block:
            entry["skip_reason"] = block
            skipped.append(entry)
            continue
        if not force_refresh and recently_archived(db, app["id"]):
            entry["skip_reason"] = (
                f"already refreshed by this tool within {RECENT_ARCHIVE_SKIP_DAYS} days"
            )
            skipped.append(entry)
            continue
        candidates.append(entry)
        if len(candidates) >= limit:
            break
    return candidates, skipped


def _mint_officer_token(actor_id, actor_name):
    import auth

    return auth.create_token(actor_id, "admin", actor_name, "officer")


def run_rescreen_via_endpoint(base_url, token, application_id, *, timeout=180):
    import requests

    response = requests.post(
        f"{base_url.rstrip('/')}/api/screening/run",
        json={"application_id": application_id},
        headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data") if isinstance(payload.get("data"), dict) else payload


def refresh_stale_screening_reports(
    db,
    *,
    execute=False,
    limit=DEFAULT_BATCH_LIMIT,
    refs=None,
    force_refresh=False,
    pace_seconds=3.0,
    base_url=None,
    actor_id="admin001",
    actor_name="SRP-2 Stale Report Refresh",
    rescreen_fn=None,
    sleep_fn=time.sleep,
):
    """Run one governed refresh batch. Returns the run summary dict."""
    _forbid_production()
    from prescreening.normalize import safe_json_loads

    if base_url is None:
        base_url = f"http://127.0.0.1:{os.environ.get('PORT', '10000')}"
    limit = max(1, min(int(limit), MAX_BATCH_LIMIT))
    candidates, skipped = select_candidates(db, refs=refs, limit=limit, force_refresh=force_refresh)
    summary = {
        "mode": "execute" if execute else "dry_run",
        "batch_limit": limit,
        "candidates": candidates,
        "skipped": skipped,
        "refreshed": [],
        "failed": [],
    }
    if not execute:
        return summary

    token = None
    if rescreen_fn is None:
        token = _mint_officer_token(actor_id, actor_name)

        def rescreen_fn(application_id):
            return run_rescreen_via_endpoint(base_url, token, application_id)

    for index, entry in enumerate(candidates):
        app_row = db.execute(
            "SELECT id, ref, prescreening_data FROM applications WHERE id = ?",
            (entry["id"],),
        ).fetchone()
        if app_row is None:
            summary["failed"].append({**entry, "error": "application disappeared"})
            continue
        app = dict(app_row)
        old_report = safe_json_loads(app.get("prescreening_data") or "{}").get("screening_report") or {}

        # Archive FIRST, commit BEFORE the provider call: if the re-screen
        # fails midway the archive row is a harmless extra snapshot, whereas
        # the reverse order could replace evidence without a preserved copy.
        old_hash = archive_current_report(db, app, old_report)
        db.commit()

        try:
            fresh = rescreen_fn(app["id"])
        except Exception as exc:
            summary["failed"].append({**entry, "error": f"{type(exc).__name__}: {exc}"})
            continue

        refreshed_row = db.execute(
            "SELECT prescreening_data FROM applications WHERE id = ?", (app["id"],)
        ).fetchone()
        new_report = safe_json_loads(
            (dict(refreshed_row).get("prescreening_data") if refreshed_row else "") or "{}"
        ).get("screening_report") or {}
        new_info = classify_report(new_report)
        new_hash = report_hash(new_report)

        # Hash-chained audit entry via the canonical writer (a raw INSERT
        # would fork the tamper-evidence chain); committed immediately per
        # the append_audit_log contract.
        from db import append_audit_log

        append_audit_log(
            db,
            action="srp2_screening_report_refreshed",
            user_id=actor_id,
            user_name=actor_name,
            user_role="system",
            target=app.get("ref") or app["id"],
            application_id=app["id"],
            detail=json.dumps({
                "reason": ARCHIVE_REASON,
                "old_report_hash": old_hash,
                "new_report_hash": new_hash,
                "old_total_hits": entry["hit_rows"],
                "new_total_hits": new_info["hit_rows"],
                "new_rows_with_real_name": new_info["rows_with_real_name"],
                "new_rows_with_match_types": new_info["rows_with_match_types"],
            }),
        )
        db.commit()

        summary["refreshed"].append({
            **entry,
            "old_report_hash": old_hash,
            "new_report_hash": new_hash,
            "endpoint_total_hits": (fresh or {}).get("total_hits"),
            "new_class": new_info["clazz"],
            "new_hit_rows": new_info["hit_rows"],
            "new_rows_with_real_name": new_info["rows_with_real_name"],
            "new_rows_with_match_types": new_info["rows_with_match_types"],
        })
        if pace_seconds and index < len(candidates) - 1:
            sleep_fn(pace_seconds)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Actually re-screen. Default is dry-run (list candidates only).")
    parser.add_argument("--confirm", default="",
                        help=f"Required with --execute: {CONFIRM_TOKEN}")
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT,
                        help=f"Batch cap (default {DEFAULT_BATCH_LIMIT}, max {MAX_BATCH_LIMIT}).")
    parser.add_argument("--refs", default="",
                        help="Comma-separated application refs to target explicitly "
                             "(bypasses auto-selection; adjudication guard still applies).")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Bypass the recently-archived re-run guard (e.g. to retry a failed batch).")
    parser.add_argument("--pace-seconds", type=float, default=3.0)
    parser.add_argument("--base-url", default=None,
                        help="Backend base URL; defaults to http://127.0.0.1:$PORT (PORT env, else 10000).")
    parser.add_argument("--actor-id", default="admin001")
    args = parser.parse_args()

    _forbid_production()
    if args.execute and args.confirm != CONFIRM_TOKEN:
        print(f"--execute requires --confirm {CONFIRM_TOKEN}", file=sys.stderr)
        return 2

    from db import get_db

    db = get_db()
    try:
        summary = refresh_stale_screening_reports(
            db,
            execute=args.execute,
            limit=args.limit,
            refs=[r.strip() for r in args.refs.split(",") if r.strip()] or None,
            force_refresh=args.force_refresh,
            pace_seconds=args.pace_seconds,
            base_url=args.base_url,
            actor_id=args.actor_id,
        )
    finally:
        db.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())

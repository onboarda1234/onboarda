"""Staging fixture seeder package (Path A: real-schema-adapted).

This package is staging-only. It is double-gated by:
- ENVIRONMENT == "staging"
- ALLOW_FIXTURE_SEED == "1"

It performs no schema changes. All writes go to columns that already
exist in both the SQLite (dev) and PostgreSQL (production/staging)
schemas declared in arie-backend/db.py.

Idempotency is achieved via deterministic markers embedded in
existing free-text columns:
- applications.id (reserved "f1xed..." hex namespace)
- applications.ref ("ARF-2026-9xxxxx" reserved range)
- applications.company_name ("FIX-SCENxx ..." prefix)
- monitoring_alerts.source_reference ("FIX_SCENxx_ALERT")
- periodic_reviews.trigger_reason (starts with "FIX_SCENxx_REVIEW")
- edd_cases.trigger_notes (starts with "FIX_SCENxx_EDD")
- compliance_memos.memo_data (JSON containing reference="FIX_<SCEN>_COMPLIANCE_MEMO")
- documents.file_path ("fixture://FIX_SCENxx_DOC_<purpose>")

See README.md for the runbook and the full real-schema mapping table.
"""

__all__ = ["registry", "seeder", "audit", "cli"]

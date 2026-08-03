#!/usr/bin/env python3
"""Read-only inventory and migration-readiness audit for Monitoring Alerts.

This diagnostic deliberately avoids the application database helpers because
their startup paths may initialise schema.  It opens a PostgreSQL connection
directly, forces a READ ONLY / REPEATABLE READ transaction, executes only the
SELECT statements declared below, verifies a stable source fingerprint, and
rolls the transaction back.

The generated evidence is intentionally sanitised:

* no credentials, client email addresses, or database connection strings;
* no free-form officer notes, audit details, or provider payloads;
* provider source references are reduced to identifiers and subject scope;
* evidence records are represented by counts and linkage identifiers.

No database write statement or commit path exists in this module.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from monitoring_status import (  # noqa: E402
    CANONICAL_ALERT_STATUSES,
    EXTENDED_ALERT_STATUSES,
    TERMINAL_STATUSES,
    canonical_filter_status,
    token,
)


AUDIT_ID = "PR-MON-M1-STATUS-AUDIT-1"
ALLOWED_SQL_PREFIXES = ("select", "with", "show")
FORBIDDEN_SQL_TOKENS = frozenset(
    {
        "alter",
        "call",
        "commit",
        "copy",
        "create",
        "delete",
        "drop",
        "grant",
        "insert",
        "merge",
        "revoke",
        "truncate",
        "update",
    }
)

SCREENING_TYPES = frozenset(
    {
        "adverse_media",
        "media",
        "pep",
        "sanctions",
        "sanctions_match",
    }
)
DOCUMENT_TYPES = frozenset(
    {
        "document_expired",
        "document_expiry",
        "document_expiry_missing",
        "document_health",
        "document_missing",
    }
)
GOVERNED_MACHINE_DETECTORS = frozenset(
    {
        "complyadvantage",
        "document health monitor",
        "document_health_monitor",
        "fixture_seed",
        "provider webhook",
        "risk drift agent",
        "sanctions/pep agent",
        "system",
    }
)
ALERT_INVENTORY_SQL = """
SELECT
    ma.id,
    ma.application_id,
    a.ref AS application_ref,
    a.is_fixture AS application_is_fixture,
    (
        a.is_fixture IS FALSE
        AND a.company_name ~* '(^|[[:space:]])(audit test|pr attestation test|qa fixture|test)([[:space:]]|$)'
    ) AS application_has_test_like_name,
    a.client_id,
    (c.id IS NOT NULL) AS client_record_exists,
    ma.alert_type,
    ma.severity,
    ma.status,
    ma.created_at,
    GREATEST(
        ma.created_at,
        ma.reviewed_at,
        ma.triaged_at,
        ma.assigned_at,
        ma.resolved_at,
        ma.discovered_at
    ) AS effective_updated_at,
    ma.detected_by,
    ma.source_reference,
    ma.linked_periodic_review_id,
    ma.linked_edd_case_id,
    ma.reviewed_by AS assigned_officer_id,
    u.role AS assigned_officer_role,
    ma.reviewed_at,
    ma.triaged_at,
    ma.assigned_at,
    ma.resolved_at,
    ma.officer_action,
    (NULLIF(BTRIM(COALESCE(ma.officer_notes, '')), '') IS NOT NULL)
        AS has_officer_notes,
    (NULLIF(BTRIM(COALESCE(ma.ai_recommendation, '')), '') IS NOT NULL)
        AS has_ai_recommendation,
    ma.provider,
    ma.case_identifier,
    ma.discovered_via,
    ma.discovered_at
FROM monitoring_alerts ma
LEFT JOIN applications a ON a.id = ma.application_id
LEFT JOIN clients c ON c.id = a.client_id
LEFT JOIN users u ON u.id = ma.reviewed_by
ORDER BY ma.id
"""


DOCUMENT_LINK_SQL = """
SELECT
    ma.id AS alert_id,
    d.id AS document_id,
    d.application_id AS document_application_id,
    d.doc_type,
    d.is_current,
    d.review_status,
    d.verification_status
FROM monitoring_alerts ma
LEFT JOIN documents d
  ON d.id = CASE
        WHEN ma.source_reference LIKE 'document:%'
        THEN SUBSTRING(ma.source_reference FROM 10)
        ELSE NULL
    END
ORDER BY ma.id, d.id
"""


EVIDENCE_LINK_SQL = """
SELECT
    monitoring_alert_id AS alert_id,
    COUNT(*) AS evidence_count,
    COUNT(DISTINCT provider) AS provider_count,
    COUNT(DISTINCT case_identifier) AS case_count,
    ARRAY_AGG(DISTINCT application_id ORDER BY application_id)
        FILTER (WHERE application_id IS NOT NULL) AS application_ids,
    ARRAY_AGG(DISTINCT provider ORDER BY provider)
        FILTER (WHERE provider IS NOT NULL) AS providers,
    ARRAY_AGG(DISTINCT case_identifier ORDER BY case_identifier)
        FILTER (WHERE case_identifier IS NOT NULL) AS case_identifiers
FROM monitoring_alert_evidence
GROUP BY monitoring_alert_id
ORDER BY monitoring_alert_id
"""


DOCUMENT_REQUEST_LINK_SQL = """
SELECT
    aer.monitoring_alert_id AS alert_id,
    aer.id AS request_id,
    aer.application_id,
    aer.status,
    aer.linked_document_id,
    aer.monitoring_document_id,
    aer.linked_periodic_review_id,
    linked_document.id AS linked_document_exists,
    linked_document.application_id AS linked_document_application_id,
    monitoring_document.id AS monitoring_document_exists,
    monitoring_document.application_id AS monitoring_document_application_id,
    linked_periodic.id AS linked_periodic_review_exists,
    linked_periodic.application_id AS linked_periodic_review_application_id
FROM application_enhanced_requirements aer
LEFT JOIN documents linked_document ON linked_document.id = aer.linked_document_id
LEFT JOIN documents monitoring_document
  ON monitoring_document.id = aer.monitoring_document_id
LEFT JOIN periodic_reviews linked_periodic
  ON linked_periodic.id = aer.linked_periodic_review_id
WHERE aer.monitoring_alert_id IS NOT NULL
ORDER BY aer.monitoring_alert_id, aer.id
"""


EDD_LINK_SQL = """
WITH links AS (
    SELECT
        ma.id AS alert_id,
        ec.id AS edd_case_id,
        ec.application_id,
        ec.stage AS status,
        ec.linked_monitoring_alert_id,
        ma.linked_edd_case_id AS alert_direct_edd_case_id,
        'direct'::text AS direction
    FROM monitoring_alerts ma
    LEFT JOIN edd_cases ec ON ec.id = ma.linked_edd_case_id
    WHERE ma.linked_edd_case_id IS NOT NULL
    UNION ALL
    SELECT
        ma.id AS alert_id,
        ec.id AS edd_case_id,
        ec.application_id,
        ec.stage AS status,
        ec.linked_monitoring_alert_id,
        ma.linked_edd_case_id AS alert_direct_edd_case_id,
        'reverse'::text AS direction
    FROM monitoring_alerts ma
    JOIN edd_cases ec ON ec.linked_monitoring_alert_id = ma.id
)
SELECT * FROM links ORDER BY alert_id, edd_case_id, direction
"""


PERIODIC_REVIEW_LINK_SQL = """
WITH links AS (
    SELECT
        ma.id AS alert_id,
        pr.id AS periodic_review_id,
        pr.application_id,
        pr.status,
        pr.linked_monitoring_alert_id,
        ma.linked_periodic_review_id AS alert_direct_periodic_review_id,
        'direct'::text AS direction
    FROM monitoring_alerts ma
    LEFT JOIN periodic_reviews pr ON pr.id = ma.linked_periodic_review_id
    WHERE ma.linked_periodic_review_id IS NOT NULL
    UNION ALL
    SELECT
        ma.id AS alert_id,
        pr.id AS periodic_review_id,
        pr.application_id,
        pr.status,
        pr.linked_monitoring_alert_id,
        ma.linked_periodic_review_id AS alert_direct_periodic_review_id,
        'reverse'::text AS direction
    FROM monitoring_alerts ma
    JOIN periodic_reviews pr ON pr.linked_monitoring_alert_id = ma.id
)
SELECT * FROM links ORDER BY alert_id, periodic_review_id, direction
"""


CHANGE_REQUEST_LINK_SQL = """
SELECT
    ma.id AS alert_id,
    ca.id AS change_alert_id,
    cr.id AS change_request_id,
    cr.application_id,
    cr.status,
    'explicit_change_alert_source_reference'::text AS link_method
FROM monitoring_alerts ma
JOIN change_alerts ca
  ON ca.source_reference = ('monitoring_alert:' || ma.id::text)
JOIN change_requests cr ON cr.source_alert_id = ca.id
ORDER BY ma.id, cr.id
"""


SCREENING_REVIEW_CONTEXT_SQL = """
SELECT
    ma.id AS alert_id,
    sr.id AS screening_review_id,
    sr.application_id,
    sr.subject_type,
    sr.disposition,
    sr.requires_four_eyes,
    (sr.second_reviewed_at IS NOT NULL) AS has_second_review
FROM monitoring_alerts ma
JOIN screening_reviews sr ON sr.application_id = ma.application_id
ORDER BY ma.id, sr.id
"""


AUDIT_TRAIL_SQL = """
SELECT
    ma.id AS alert_id,
    al.timestamp,
    al.action,
    al.user_role,
    al.request_id,
    (NULLIF(BTRIM(COALESCE(al.before_state, '')), '') IS NOT NULL)
        AS has_before_state,
    (NULLIF(BTRIM(COALESCE(al.after_state, '')), '') IS NOT NULL)
        AS has_after_state
FROM monitoring_alerts ma
JOIN audit_log al
  ON al.target = ('monitoring_alert:' || ma.id::text)
  OR al.target = ('Monitoring Alert ' || ma.id::text)
ORDER BY ma.id, al.timestamp, al.id
"""


SOURCE_FINGERPRINT_SQL = """
WITH fingerprints AS (
    SELECT
        'monitoring_alerts'::text AS relation,
        COUNT(*)::bigint AS row_count,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        )) AS md5_digest
    FROM monitoring_alerts t
    UNION ALL
    SELECT
        'monitoring_alert_evidence',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM monitoring_alert_evidence t
    UNION ALL
    SELECT
        'application_enhanced_requirements',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM application_enhanced_requirements t
    WHERE t.monitoring_alert_id IS NOT NULL
    UNION ALL
    SELECT
        'edd_cases',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM edd_cases t
    WHERE t.linked_monitoring_alert_id IS NOT NULL
    UNION ALL
    SELECT
        'periodic_reviews',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM periodic_reviews t
    WHERE t.linked_monitoring_alert_id IS NOT NULL
    UNION ALL
    SELECT
        'change_requests',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM change_requests t
    WHERE t.source_alert_id IS NOT NULL
    UNION ALL
    SELECT
        'monitoring_alert_audit_log',
        COUNT(*)::bigint,
        MD5(COALESCE(
            STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text),
            ''
        ))
    FROM audit_log t
    WHERE t.target LIKE 'monitoring_alert:%'
       OR t.target LIKE 'Monitoring Alert %'
)
SELECT relation, row_count, md5_digest
FROM fingerprints
ORDER BY relation
"""


QUERY_SET = {
    "alerts": ALERT_INVENTORY_SQL,
    "documents": DOCUMENT_LINK_SQL,
    "evidence": EVIDENCE_LINK_SQL,
    "document_requests": DOCUMENT_REQUEST_LINK_SQL,
    "edd": EDD_LINK_SQL,
    "periodic_reviews": PERIODIC_REVIEW_LINK_SQL,
    "change_requests": CHANGE_REQUEST_LINK_SQL,
    "screening_contexts": SCREENING_REVIEW_CONTEXT_SQL,
    "audit_trail": AUDIT_TRAIL_SQL,
    "fingerprint": SOURCE_FINGERPRINT_SQL,
}


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    columns = [item[0] for item in cursor.description]
    return dict(zip(columns, row))


def assert_select_only(sql: str) -> None:
    """Reject anything outside the small read-only SQL subset used here."""
    normalised = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL).strip()
    first = normalised.split(None, 1)[0].lower() if normalised else ""
    if first not in ALLOWED_SQL_PREFIXES:
        raise ValueError(f"Audit SQL must start with SELECT/WITH/SHOW, got {first!r}")
    tokens = set(re.findall(r"[a-z_]+", normalised.lower()))
    forbidden = sorted(tokens & FORBIDDEN_SQL_TOKENS)
    if forbidden:
        raise ValueError(f"Audit SQL contains forbidden token(s): {', '.join(forbidden)}")


def validate_query_set() -> None:
    for sql in QUERY_SET.values():
        assert_select_only(sql)


def _fetch(cursor: Any, sql: str) -> list[dict[str, Any]]:
    assert_select_only(sql)
    cursor.execute(sql)
    return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _group(rows: Iterable[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["alert_id"])].append(dict(row))
    return grouped


def _keyed_digest(key: bytes, value: Any) -> str:
    """Return a per-run HMAC without exposing its uncommitted secret key."""
    return hmac.new(
        key,
        str(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _source_reference(
    value: Any,
    pseudonymization_key: bytes,
) -> dict[str, Any] | None:
    """Return a structured identifier or keyed pseudonym, never raw free text."""
    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        text = str(value)
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, Mapping):
            payload = dict(parsed)
        else:
            if re.fullmatch(
                r"(?:document:[A-Za-z0-9_-]+|[A-Z0-9-]+:MONITORING)",
                text,
            ):
                return {"kind": "identifier", "value": text}
            return {
                "kind": "opaque_text",
                "hmac_sha256": _keyed_digest(pseudonymization_key, text),
            }
    subject = payload.get("screening_subject")
    subject = subject if isinstance(subject, Mapping) else {}
    return {
        key: item
        for key, item in {
            "provider": payload.get("provider"),
            "alert_identifier": payload.get("alert_identifier"),
            "case_identifier": payload.get("case_identifier"),
            "subject_scope": payload.get("subject_scope") or subject.get("scope"),
            "subject_kind": subject.get("kind"),
        }.items()
        if item not in (None, "")
    }


def _source_identity(alert: Mapping[str, Any]) -> str:
    source = alert.get("source_reference")
    if isinstance(source, Mapping):
        for key in ("alert_identifier", "case_identifier"):
            if source.get(key):
                return f"{key}:{source[key]}"
        return json.dumps(source, sort_keys=True, default=_json_default)
    return str(source or alert.get("case_identifier") or "").strip().lower()


def _detected_by(
    value: Any,
    pseudonymization_key: bytes,
) -> dict[str, str] | None:
    """Retain system labels while pseudonymising human/free-form actor text."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    lowered = text.lower()
    if lowered in GOVERNED_MACHINE_DETECTORS:
        return {"kind": "system", "label": text}
    return {
        "kind": "pseudonymous",
        "hmac_sha256": _keyed_digest(pseudonymization_key, text),
    }


def _is_document_source(alert: Mapping[str, Any]) -> bool:
    source = alert.get("source_reference")
    return bool(
        isinstance(source, Mapping)
        and source.get("kind") == "identifier"
        and str(source.get("value") or "").startswith("document:")
    )


def _is_unresolved(alert: Mapping[str, Any]) -> bool:
    return bool(
        not alert.get("resolution_evidence", {}).get("resolved_at")
        and canonical_filter_status(alert.get("status")) not in TERMINAL_STATUSES
    )


def _canonical_type(value: Any) -> str:
    return token(value)


def _status_validity(value: Any) -> str:
    status = token(value)
    if status in CANONICAL_ALERT_STATUSES:
        return "Valid"
    if status in set(EXTENDED_ALERT_STATUSES) - set(CANONICAL_ALERT_STATUSES):
        return "Legacy"
    if not status:
        return "Invalid"
    if status in {
        "document_requested",
        "client_uploaded",
        "under_review",
        "awaiting_review",
        "notification_failed",
        "cancelled",
    }:
        return "Legacy"
    if re.fullmatch(r"[a-z][a-z0-9_]*", status):
        return "Unknown"
    return "Invalid"


def _owner(alert: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Infer the workflow owner from explicit links, then source semantics."""
    reasons: list[str] = []
    explicit: list[str] = []
    if any(item.get("edd_case_id") for item in alert["edd_links"]):
        explicit.append("EDD")
    if any(
        item.get("periodic_review_id") for item in alert["periodic_review_links"]
    ):
        explicit.append("Periodic Review")
    if alert["change_request_links"]:
        explicit.append("Change Management")
    if alert["document_request_links"]:
        explicit.append("Documents")
    if len(set(explicit)) > 1:
        reasons.append("multiple_explicit_workflow_owners")
        return "Manual", reasons
    if explicit:
        return explicit[0], reasons

    alert_type = _canonical_type(alert.get("alert_type"))
    if alert_type in DOCUMENT_TYPES or _is_document_source(alert):
        return "Documents", reasons
    if (
        alert_type in SCREENING_TYPES
        or alert.get("evidence", {}).get("evidence_count", 0)
        or alert.get("provider")
        or alert.get("case_identifier")
    ):
        return "Screening Review", reasons
    return "Manual", reasons


def _linkage_findings(alert: Mapping[str, Any], owner: str) -> list[str]:
    findings: list[str] = []
    application_id = alert.get("application_id")
    if not application_id:
        findings.append("missing_application_link")
    elif not alert.get("application_ref"):
        findings.append("broken_application_link")
    if alert.get("client_id") and not alert.get("client_record_exists"):
        findings.append("broken_client_link")

    documents = alert["document_links"]
    if owner == "Documents":
        if not documents or not any(item.get("document_id") for item in documents):
            findings.append("missing_document_link")
        for document in documents:
            if not document.get("document_id"):
                continue
            if application_id and document.get("document_application_id") != application_id:
                findings.append("impossible_cross_application_document_link")

    for relation, key in (
        ("change_request_links", "application_id"),
        ("document_request_links", "application_id"),
    ):
        for linked in alert[relation]:
            if application_id and linked.get(key) != application_id:
                findings.append(f"impossible_cross_application_{relation[:-6]}_link")

    evidence = alert.get("evidence", {})
    for evidence_application_id in evidence.get("application_ids") or []:
        if application_id and evidence_application_id != application_id:
            findings.append("impossible_cross_application_screening_evidence_link")

    for request in alert["document_request_links"]:
        for id_key, exists_key, app_key, label in (
            (
                "linked_document_id",
                "linked_document_exists",
                "linked_document_application_id",
                "linked_document",
            ),
            (
                "monitoring_document_id",
                "monitoring_document_exists",
                "monitoring_document_application_id",
                "monitoring_document",
            ),
            (
                "linked_periodic_review_id",
                "linked_periodic_review_exists",
                "linked_periodic_review_application_id",
                "document_request_periodic_review",
            ),
        ):
            if request.get(id_key) and not request.get(exists_key):
                findings.append(f"broken_{label}_link")
            if (
                application_id
                and request.get(exists_key)
                and request.get(app_key) != application_id
            ):
                findings.append(f"impossible_cross_application_{label}_link")

    edd_ids = {
        item.get("edd_case_id") for item in alert["edd_links"] if item.get("edd_case_id")
    }
    for linked in alert["edd_links"]:
        if linked.get("direction") == "direct" and not linked.get("edd_case_id"):
            findings.append("broken_edd_link")
            continue
        if application_id and linked.get("application_id") != application_id:
            findings.append("impossible_cross_application_edd_link")
        if (
            linked.get("direction") == "direct"
            and linked.get("linked_monitoring_alert_id") not in (None, alert["id"])
        ):
            findings.append("impossible_conflicting_edd_reverse_link")
        if (
            linked.get("direction") == "reverse"
            and linked.get("alert_direct_edd_case_id") not in (
                None,
                linked.get("edd_case_id"),
            )
        ):
            findings.append("impossible_conflicting_edd_direct_link")
    if len(edd_ids) > 1:
        findings.append("impossible_multiple_edd_links")

    periodic_ids = {
        item.get("periodic_review_id")
        for item in alert["periodic_review_links"]
        if item.get("periodic_review_id")
    }
    for linked in alert["periodic_review_links"]:
        if linked.get("direction") == "direct" and not linked.get(
            "periodic_review_id"
        ):
            findings.append("broken_periodic_review_link")
            continue
        if application_id and linked.get("application_id") != application_id:
            findings.append("impossible_cross_application_periodic_review_link")
        if (
            linked.get("direction") == "direct"
            and linked.get("linked_monitoring_alert_id") not in (None, alert["id"])
        ):
            findings.append("impossible_conflicting_periodic_review_reverse_link")
        if (
            linked.get("direction") == "reverse"
            and linked.get("alert_direct_periodic_review_id")
            not in (None, linked.get("periodic_review_id"))
        ):
            findings.append("impossible_conflicting_periodic_review_direct_link")
    if len(periodic_ids) > 1:
        findings.append("impossible_multiple_periodic_review_links")

    if owner == "Screening Review":
        if not (evidence.get("evidence_count") or alert.get("case_identifier")):
            findings.append("missing_screening_case_link")
    return sorted(set(findings))


def _future_status(alert: Mapping[str, Any]) -> tuple[str | None, str]:
    """Recommend only mappings supported by current row evidence."""
    raw_status = token(alert.get("status"))
    status = canonical_filter_status(alert.get("status"))
    has_edd = any(item.get("edd_case_id") for item in alert["edd_links"])
    has_periodic_review = any(
        item.get("periodic_review_id")
        for item in alert["periodic_review_links"]
    )
    if has_edd and status not in TERMINAL_STATUSES:
        return "routed_to_edd", "Candidate for future migration"
    if has_periodic_review and status not in TERMINAL_STATUSES:
        return "routed_to_review", "Candidate for future migration"
    if raw_status in CANONICAL_ALERT_STATUSES:
        return raw_status, "Candidate for future migration"
    if status == "in_review":
        return None, "Manual review required"
    if status == "escalated":
        return None, "Manual review required"
    if status in {"document_requested", "client_uploaded", "under_review"}:
        if alert["document_request_links"]:
            return "open", "Candidate for future migration"
        return None, "Manual review required"
    return None, "Manual review required"


def _primary_classification(
    *,
    status_validity: str,
    linkage_findings: Sequence[str],
    duplicate: bool,
) -> str:
    if duplicate:
        return "Duplicate"
    if any(
        finding.startswith(("missing_", "broken_", "impossible_"))
        for finding in linkage_findings
    ):
        return "Orphaned"
    return status_validity


def _build_duplicate_groups(
    alerts: Sequence[dict[str, Any]],
    pseudonymization_key: bytes,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for alert in alerts:
        if not _is_unresolved(alert):
            continue
        fingerprint = (
            str(alert.get("application_id") or ""),
            alert["workflow_owner"],
            _canonical_type(alert.get("alert_type")),
            _source_identity(alert),
        )
        buckets[fingerprint].append(int(alert["id"]))
    return [
        {
            "alert_ids": ids,
            "application_id": fingerprint[0] or None,
            "workflow_owner": fingerprint[1],
            "alert_type": fingerprint[2],
            "source_identity_hmac_sha256": _keyed_digest(
                pseudonymization_key,
                fingerprint[3],
            ),
            "reason": "same unresolved application, owner, type, and source identity",
        }
        for fingerprint, ids in sorted(buckets.items())
        if len(ids) > 1
    ]


def _build_duplicate_source_groups(
    alerts: Sequence[dict[str, Any]],
    pseudonymization_key: bytes,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for alert in alerts:
        identity = _source_identity(alert)
        if identity:
            buckets[identity].append(int(alert["id"]))
    return [
        {
            "alert_ids": ids,
            "source_identity_hmac_sha256": _keyed_digest(
                pseudonymization_key,
                identity,
            ),
        }
        for identity, ids in sorted(buckets.items())
        if len(ids) > 1
    ]


def _build_unresolved_similarity_groups(
    alerts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for alert in alerts:
        if not _is_unresolved(alert):
            continue
        key = (
            str(alert.get("application_id") or ""),
            alert["workflow_owner"],
            _canonical_type(alert.get("alert_type")),
        )
        buckets[key].append(alert)
    groups = []
    for key, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        identities = {_source_identity(item) for item in members}
        groups.append(
            {
                "alert_ids": [int(item["id"]) for item in members],
                "application_id": key[0] or None,
                "workflow_owner": key[1],
                "alert_type": key[2],
                "source_identity_count": len(identities),
                "assessment": (
                    "exact duplicate candidate"
                    if len(identities) == 1
                    else "parallel unresolved alerts with distinct source identities"
                ),
            }
        )
    return groups


def _test_like_non_fixture(alert: Mapping[str, Any]) -> bool:
    return bool(
        alert.get("application_id")
        and alert.get("application_is_fixture") is False
        and alert.get("application_has_test_like_name")
    )


def analyse_snapshot(
    snapshot: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    pseudonymization_key: bytes | None = None,
) -> dict[str, Any]:
    run_key = pseudonymization_key or secrets.token_bytes(32)
    grouped = {
        key: _group(snapshot.get(key, []))
        for key in (
            "documents",
            "document_requests",
            "edd",
            "periodic_reviews",
            "change_requests",
            "screening_contexts",
            "audit_trail",
        )
    }
    evidence = {
        int(row["alert_id"]): dict(row) for row in snapshot.get("evidence", [])
    }

    alerts: list[dict[str, Any]] = []
    for raw in snapshot.get("alerts", []):
        row = dict(raw)
        alert_id = int(row["id"])
        alert = {
            "id": alert_id,
            "application_id": row.get("application_id"),
            "application_ref": row.get("application_ref"),
            "application_is_fixture": row.get("application_is_fixture"),
            "application_has_test_like_name": bool(
                row.get("application_has_test_like_name")
            ),
            "client_id": row.get("client_id"),
            "client": {
                "id": row.get("client_id"),
            },
            "client_record_exists": bool(row.get("client_record_exists")),
            "alert_type": row.get("alert_type"),
            "severity": row.get("severity"),
            "status": row.get("status"),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("effective_updated_at")),
            "detected_by": _detected_by(row.get("detected_by"), run_key),
            "source_reference": _source_reference(
                row.get("source_reference"),
                run_key,
            ),
            "provider": row.get("provider"),
            "case_identifier": row.get("case_identifier"),
            "discovered_via": row.get("discovered_via"),
            "discovered_at": _iso(row.get("discovered_at")),
            "linked_periodic_review_id": row.get("linked_periodic_review_id"),
            "linked_edd_case_id": row.get("linked_edd_case_id"),
            "assigned_officer": {
                "id": row.get("assigned_officer_id"),
                "role": row.get("assigned_officer_role"),
                "assigned_at": _iso(row.get("assigned_at")),
            },
            "resolution_evidence": {
                "officer_action": row.get("officer_action"),
                "has_officer_notes": bool(row.get("has_officer_notes")),
                "reviewed_at": _iso(row.get("reviewed_at")),
                "resolved_at": _iso(row.get("resolved_at")),
            },
            "document_links": [
                item
                for item in grouped["documents"].get(alert_id, [])
                if item.get("document_id")
            ],
            "document_request_links": grouped["document_requests"].get(alert_id, []),
            "screening_review_context": grouped["screening_contexts"].get(
                alert_id, []
            ),
            "edd_links": grouped["edd"].get(alert_id, []),
            "periodic_review_links": grouped["periodic_reviews"].get(alert_id, []),
            "change_request_links": grouped["change_requests"].get(alert_id, []),
            "evidence": evidence.get(
                alert_id,
                {
                    "alert_id": alert_id,
                    "evidence_count": 0,
                    "provider_count": 0,
                    "case_count": 0,
                    "application_ids": [],
                    "providers": [],
                    "case_identifiers": [],
                },
            ),
            "audit_trail": grouped["audit_trail"].get(alert_id, []),
        }
        owner, owner_findings = _owner(alert)
        alert["workflow_owner"] = owner
        alert["ownership_findings"] = owner_findings
        alert["linkage_findings"] = _linkage_findings(alert, owner)
        alert["status_validity"] = _status_validity(alert["status"])
        alert["test_like_non_fixture"] = _test_like_non_fixture(alert)
        future, readiness_label = _future_status(alert)
        alert["future_status"] = future
        alert["migration_disposition"] = readiness_label
        alerts.append(alert)

    duplicate_groups = _build_duplicate_groups(alerts, run_key)
    duplicate_ids = {
        alert_id
        for group in duplicate_groups
        for alert_id in group["alert_ids"]
    }
    for alert in alerts:
        alert["classification"] = _primary_classification(
            status_validity=alert["status_validity"],
            linkage_findings=alert["linkage_findings"],
            duplicate=alert["id"] in duplicate_ids,
        )
        terminal_missing_resolution = (
            canonical_filter_status(alert["status"]) in TERMINAL_STATUSES
            and not alert["resolution_evidence"]["resolved_at"]
        )
        owner_status_drift = bool(
            alert["workflow_owner"] in {"EDD", "Periodic Review"}
            and alert["status_validity"] == "Valid"
            and _is_unresolved(alert)
            and token(alert["status"])
            != (
                "routed_to_edd"
                if alert["workflow_owner"] == "EDD"
                else "routed_to_review"
            )
        )
        alert["migration_findings"] = sorted(
            set(
                alert["linkage_findings"]
                + alert["ownership_findings"]
                + (
                    ["test_like_non_fixture_requires_confirmation"]
                    if alert["test_like_non_fixture"]
                    else []
                )
                + (
                    ["terminal_status_without_resolved_at"]
                    if terminal_missing_resolution
                    else []
                )
                + (
                    [
                        "active_status_conflicts_with_"
                        + (
                            "edd"
                            if alert["workflow_owner"] == "EDD"
                            else "periodic_review"
                        )
                        + "_owner_link"
                    ]
                    if owner_status_drift
                    else []
                )
            )
        )
        if alert["migration_findings"] or alert["classification"] not in {
            "Valid",
            "Legacy",
        }:
            alert["migration_disposition"] = "Manual review required"
        alert["migration_ready"] = (
            alert["classification"] == "Valid"
            and not alert["migration_findings"]
            and alert["future_status"] is not None
        )
        if alert["migration_ready"]:
            alert["migration_disposition"] = "Migration ready"

    status_counts = Counter(str(item["status"] or "<null>") for item in alerts)
    classification_counts = Counter(item["classification"] for item in alerts)
    status_validity_counts = Counter(item["status_validity"] for item in alerts)
    type_counts = Counter(str(item["alert_type"] or "<null>") for item in alerts)
    owner_counts = Counter(item["workflow_owner"] for item in alerts)
    severity_counts = Counter(
        str(item["severity"] or "<null>").strip().lower() for item in alerts
    )
    duplicate_source_groups = _build_duplicate_source_groups(alerts, run_key)
    unresolved_similarity_groups = _build_unresolved_similarity_groups(alerts)
    multiple_owner_alerts = [
        item["id"]
        for item in alerts
        if "multiple_explicit_workflow_owners" in item["ownership_findings"]
    ]
    broken_or_impossible_alerts = [
        item["id"]
        for item in alerts
        if any(
            finding.startswith(("broken_", "impossible_"))
            for finding in item["linkage_findings"]
        )
    ]
    missing_link_alerts = [
        item["id"]
        for item in alerts
        if any(
            finding.startswith("missing_") for finding in item["linkage_findings"]
        )
    ]
    statistics = {
        "total_alerts": len(alerts),
        "open_exact": status_counts.get("open", 0),
        "resolved_exact": status_counts.get("resolved", 0),
        "terminal_total": sum(
            canonical_filter_status(item["status"]) in TERMINAL_STATUSES
            for item in alerts
        ),
        "legacy": status_validity_counts.get("Legacy", 0),
        "unknown": status_validity_counts.get("Unknown", 0),
        "invalid": status_validity_counts.get("Invalid", 0),
        "duplicates": len(duplicate_ids),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_source_reference_groups": len(duplicate_source_groups),
        "multiple_workflow_owner_alerts": len(multiple_owner_alerts),
        "unresolved_similarity_groups": len(unresolved_similarity_groups),
        "orphaned": classification_counts.get("Orphaned", 0),
        "missing_linkage": sum(bool(item["linkage_findings"]) for item in alerts),
        "missing_link_alerts": len(missing_link_alerts),
        "broken_or_impossible_link_alerts": len(broken_or_impossible_alerts),
        "migration_ready": sum(item["migration_ready"] for item in alerts),
        "future_migration_candidates": sum(
            item["migration_disposition"] == "Candidate for future migration"
            and not item["migration_ready"]
            for item in alerts
        ),
        "manual_review_required": sum(
            item["migration_disposition"] == "Manual review required"
            for item in alerts
        ),
        "per_status": dict(sorted(status_counts.items())),
        "per_classification": dict(sorted(classification_counts.items())),
        "per_status_validity": dict(sorted(status_validity_counts.items())),
        "per_alert_type": dict(sorted(type_counts.items())),
        "per_owner_workflow": dict(sorted(owner_counts.items())),
        "per_severity": dict(sorted(severity_counts.items())),
    }
    return {
        "audit_id": AUDIT_ID,
        "statistics": statistics,
        "alerts": alerts,
        "duplicate_groups": duplicate_groups,
        "duplicate_source_reference_groups": duplicate_source_groups,
        "unresolved_similarity_groups": unresolved_similarity_groups,
        "multiple_workflow_owner_alerts": multiple_owner_alerts,
    }


def _execute_snapshot(connection: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validate_query_set()
    cursor = connection.cursor()
    before = _fetch(cursor, SOURCE_FINGERPRINT_SQL)
    snapshot = {
        key: _fetch(cursor, sql)
        for key, sql in QUERY_SET.items()
        if key != "fingerprint"
    }
    after = _fetch(cursor, SOURCE_FINGERPRINT_SQL)
    if before != after:
        raise RuntimeError("Monitoring audit source changed during the read-only snapshot")
    return snapshot, before


def _runtime_collection_metadata() -> dict[str, Any]:
    """Resolve safe runtime provenance without importing application DB helpers."""
    from environment import ENV, get_monitoring_feature_state

    deployed_git_sha = str(os.environ.get("GIT_SHA") or "").strip()
    if not deployed_git_sha:
        try:
            deployed_git_sha = subprocess.run(
                ("git", "rev-parse", "HEAD"),
                cwd=BACKEND_ROOT.parent,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            deployed_git_sha = "not recorded"
    return {
        "environment": ENV,
        "deployed_git_sha": deployed_git_sha,
        "monitoring_feature_flags": get_monitoring_feature_state(),
    }


def collect_from_database(database_url: str) -> dict[str, Any]:
    """Collect and classify the audit in a rollback-only DB transaction."""
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("psycopg2 is required for live audit collection") from exc

    connection = psycopg2.connect(database_url)
    try:
        connection.set_session(
            readonly=True,
            autocommit=False,
            isolation_level="REPEATABLE READ",
        )
        cursor = connection.cursor()
        cursor.execute("SHOW transaction_read_only")
        read_only = str(cursor.fetchone()[0]).lower() == "on"
        if not read_only:
            raise RuntimeError("Database did not confirm transaction_read_only=on")
        snapshot, fingerprint = _execute_snapshot(connection)
        result = analyse_snapshot(snapshot)
        result["collection"] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "transaction_read_only": read_only,
            "isolation_level": "REPEATABLE READ",
            "completion": "ROLLBACK",
            "sql_statement_policy": "SELECT/WITH/SHOW only",
            "source_fingerprint": fingerprint,
            **_runtime_collection_metadata(),
        }
        return result
    finally:
        connection.rollback()
        connection.close()


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                str(value if value not in (None, "") else "—").replace("|", "\\|")
                for value in row
            )
            + " |"
        )
    return "\n".join(lines)


def render_reports(result: Mapping[str, Any]) -> dict[str, str]:
    alerts = list(result["alerts"])
    stats = result["statistics"]
    collection = result.get("collection", {})
    generated = collection.get("captured_at") or datetime.now(timezone.utc).isoformat()
    read_only_statement = (
        f"`transaction_read_only={str(collection.get('transaction_read_only')).lower()}`; "
        f"{collection.get('isolation_level', 'REPEATABLE READ')}; "
        f"{collection.get('completion', 'ROLLBACK')} only."
    )
    deployed_sha = collection.get("deployed_git_sha") or "not recorded"
    feature_states = collection.get("monitoring_feature_flags") or {}
    feature_state_summary = ", ".join(
        f"`{key}={'ON' if value else 'OFF'}`"
        for key, value in sorted(feature_states.items())
    ) or "not recorded"
    inventory_rows = [
        (
            item["id"],
            item["application_ref"],
            item["client"].get("id"),
            item["alert_type"],
            item["severity"],
            item["status"],
            item["classification"],
            item["workflow_owner"],
            item["migration_disposition"],
        )
        for item in alerts
    ]
    orphan_ids = [
        str(item["id"]) for item in alerts if item["classification"] == "Orphaned"
    ]
    legacy_ids = [
        str(item["id"]) for item in alerts if item["status_validity"] == "Legacy"
    ]
    terminal_timestamp_ids = [
        str(item["id"])
        for item in alerts
        if "terminal_status_without_resolved_at" in item["migration_findings"]
    ]
    provenance_ids = [
        str(item["id"]) for item in alerts if item["test_like_non_fixture"]
    ]
    summary = f"""# {AUDIT_ID} — Monitoring Alert Runtime Audit

Captured: `{generated}`

Environment: `{collection.get("environment", "staging")}`

Deployed source SHA: `{deployed_sha}`

## Safety and methodology

This is a diagnostic, read-only inventory. The collector used
{read_only_statement} Every SQL statement is statically restricted to
`SELECT`, `WITH`, or `SHOW`; the collector has no commit path. It did not update,
delete, backfill, route, resolve, or otherwise mutate any alert or linked object.

The evidence output excludes client email addresses, credentials, free-form
officer notes, raw provider payloads, and audit-log detail text. Application and
client names never leave the database query. Client labels are omitted, staff
are represented by pseudonymous ID/role, free-form source references and human
detector labels use HMAC-SHA-256 with a fresh per-run secret that is never
persisted, and provider payloads are reduced to identifier/scope fields.

## Statistics

{_table(
    ("Metric", "Count"),
    (
        ("Total alerts", stats["total_alerts"]),
        ("Open (exact status)", stats["open_exact"]),
        ("Resolved (exact status)", stats["resolved_exact"]),
        ("Terminal (all terminal statuses)", stats["terminal_total"]),
        ("Legacy status", stats["legacy"]),
        ("Invalid status", stats["invalid"]),
        ("Unknown status", stats["unknown"]),
        ("Duplicate alerts", stats["duplicates"]),
        ("Orphaned alerts", stats["orphaned"]),
        ("Alerts with missing/broken/impossible linkage", stats["missing_linkage"]),
        ("Alerts with missing linkage", stats["missing_link_alerts"]),
        (
            "Alerts with broken/impossible linkage",
            stats["broken_or_impossible_link_alerts"],
        ),
        ("Migration ready", stats["migration_ready"]),
        ("Future migration candidate", stats["future_migration_candidates"]),
        ("Manual review required", stats["manual_review_required"]),
    ),
)}

## Findings

* Orphaned alerts: **{", ".join(orphan_ids) or "none"}**.
* Legacy stored status: **{", ".join(legacy_ids) or "none"}**. An orphan
  classification takes precedence in the single primary classification, while
  `status_validity` preserves the legacy finding.
* Terminal status without `resolved_at`: **{", ".join(terminal_timestamp_ids) or "none"}**.
* Alerts on non-fixture applications with test-like names requiring provenance
  confirmation: **{", ".join(provenance_ids) or "none"}**.
* Exact duplicate alert groups: **{stats["duplicate_groups"]}**.
* Repeated source-reference groups: **{stats["duplicate_source_reference_groups"]}**.
* Alerts with multiple explicit workflow owners:
  **{stats["multiple_workflow_owner_alerts"]}**.
* Alerts with broken or impossible links:
  **{stats["broken_or_impossible_link_alerts"]}**.
* Alerts with missing required links: **{stats["missing_link_alerts"]}**.

## Complete alert inventory

{_table(
    (
        "ID",
        "Application",
        "Client",
        "Type",
        "Severity",
        "Current status",
        "Classification",
        "Owner",
        "Migration disposition",
    ),
    inventory_rows,
)}

The machine-readable inventory records created/updated dates, detection source,
sanitised source reference, assigned officer, resolution-evidence presence,
document/screening/EDD/periodic-review/change-request linkage, and sanitised
audit trail for every row.

## Distribution

### By alert type

{_table(("Alert type", "Count"), stats["per_alert_type"].items())}

### By owner workflow

{_table(("Owner", "Count"), stats["per_owner_workflow"].items())}

### By severity

{_table(("Severity", "Count"), stats["per_severity"].items())}

## Guardrail conclusion

Monitoring is not assigned as the owner of any row. The inferred owner is always
Documents, Screening Review, EDD, Change Management, Periodic Review, or Manual.
Runtime flag evidence: {feature_state_summary}. No Monitoring workflow or
feature flag was activated by this audit.
"""

    mapping_rows = []
    for status in sorted({str(item["status"]) for item in alerts}):
        matching = [item for item in alerts if str(item["status"]) == status]
        futures = sorted({item["future_status"] or "Manual review required" for item in matching})
        status_validity = matching[0]["status_validity"]
        supported = all(
            item["future_status"] is not None
            and item["migration_disposition"] != "Manual review required"
            for item in matching
        )
        mapping_rows.append(
            (
                status,
                status_validity,
                ", ".join(futures),
                (
                    "Candidate for future migration"
                    if supported
                    else "Manual review required"
                ),
                sum(
                    item["migration_disposition"] == "Manual review required"
                    for item in matching
                ),
            )
        )
    mapping = f"""# Status Mapping Report

This is a recommendation only. It does not create a state machine or update any
stored status. A mapping is proposed only where the row has supporting workflow
evidence; uncertainty is explicitly left for manual review.

{_table(
    (
        "Current status",
        "Validity",
        "Recommended future status",
        "Mapping disposition",
        "Rows needing manual review",
    ),
    mapping_rows,
)}

Row-level recommendations and evidence are in `runtime_inventory.json`.
"""

    duplicate_groups = result["duplicate_groups"]
    duplicate_source_groups = result["duplicate_source_reference_groups"]
    similarity_groups = result["unresolved_similarity_groups"]
    multiple_owner_alerts = result["multiple_workflow_owner_alerts"]
    duplicates = f"""# Duplicate Report

Exact unresolved duplicates require the same application, owner workflow,
normalised alert type, and source identity. Distinct provider case/alert
identifiers and distinct document IDs are not treated as duplicates merely
because they share an application and type.

Detected duplicate groups: **{len(duplicate_groups)}**.

{
    _table(
        ("Alert IDs", "Application", "Owner", "Type", "Reason"),
        (
            (
                ", ".join(map(str, group["alert_ids"])),
                group["application_id"],
                group["workflow_owner"],
                group["alert_type"],
                group["reason"],
            )
            for group in duplicate_groups
        ),
    )
    if duplicate_groups
    else "No exact duplicate group was found. Similar parallel provider alerts retain distinct case identities."
}

Future reconciliation should remain a separate, reviewed migration. This audit
does not merge, dismiss, or delete any alert.

## Duplicate source references

Detected repeated source-reference identities: **{len(duplicate_source_groups)}**.

{
    _table(
        ("Alert IDs", "Source identity HMAC-SHA-256"),
        (
            (
                ", ".join(map(str, group["alert_ids"])),
                group["source_identity_hmac_sha256"],
            )
            for group in duplicate_source_groups
        ),
    )
    if duplicate_source_groups
    else "No repeated source-reference identity was found."
}

A repeated generic manual label is a review signal, not proof that the underlying
events are the same.

## Duplicate workflow ownership

Alerts linked to more than one explicit owner workflow: **{len(multiple_owner_alerts)}**.

{
    ", ".join(map(str, multiple_owner_alerts))
    if multiple_owner_alerts
    else "No alert has duplicate explicit workflow ownership."
}

## Similar unresolved alerts

{_table(
    ("Alert IDs", "Application", "Owner", "Type", "Assessment"),
    (
        (
            ", ".join(map(str, group["alert_ids"])),
            group["application_id"],
            group["workflow_owner"],
            group["alert_type"],
            group["assessment"],
        )
        for group in similarity_groups
    ),
)}

These groups were not classified as duplicates when their provider case,
provider alert, or document identities differ. Future reconciliation should
confirm that each distinct source represents a distinct monitored subject or
document before any merge is proposed.
"""

    orphan_rows = [
        (
            item["id"],
            item["alert_type"],
            item["status"],
            item["workflow_owner"],
            ", ".join(item["linkage_findings"]),
        )
        for item in alerts
        if item["classification"] == "Orphaned"
    ]
    orphans = f"""# Orphan Report

An alert is classified Orphaned when a required application/workflow object is
missing, broken, or impossible. Optional relationships are not treated as
required.

{_table(("ID", "Type", "Status", "Expected owner", "Finding"), orphan_rows)}

No link was repaired. Every orphan remains **Manual review required**.
"""

    linkage_rows = [
        (
            item["id"],
            item["workflow_owner"],
            len([x for x in item["document_links"] if x.get("document_id")]),
            item["evidence"].get("evidence_count", 0),
            max(
                item["evidence"].get("case_count", 0),
                1 if item.get("case_identifier") else 0,
            ),
            len(item["screening_review_context"]),
            len(
                {
                    x.get("edd_case_id")
                    for x in item["edd_links"]
                    if x.get("edd_case_id")
                }
            ),
            len(
                {
                    x.get("periodic_review_id")
                    for x in item["periodic_review_links"]
                    if x.get("periodic_review_id")
                }
            ),
            len(item["change_request_links"]),
            ", ".join(item["linkage_findings"]) or "none",
        )
        for item in alerts
    ]
    linkage = f"""# Linkage Report

{_table(
    (
        "ID",
        "Owner",
        "Documents",
        "Evidence",
        "Screening cases",
        "App review context",
        "EDD",
        "Periodic",
        "Change",
        "Finding",
    ),
    linkage_rows,
)}

Cross-application links are classified as impossible. Referenced IDs that do not
resolve are classified as broken. Screening Review rows joined only by
application are labelled non-causal context and are never used as an alert link
or migration-routing signal. Change Management is linked only through the
schema-valid `change_alerts.source_reference = monitoring_alert:<id>` bridge;
`change_requests.source_alert_id` is never compared directly with a Monitoring
Alert ID. No linkage was added or changed.
"""

    readiness_rows = [
        (
            item["id"],
            item["status"],
            item["future_status"],
            "Ready" if item["migration_ready"] else item["migration_disposition"],
            ", ".join(item["migration_findings"]) or "none",
        )
        for item in alerts
    ]
    readiness = f"""# Migration Readiness Report

Migration-ready means: canonical current status, no duplicate/orphan/linkage
finding, no unresolved fixture-provenance concern, complete terminal resolution
timestamp where applicable, and an evidence-supported future status.

{_table(
    ("ID", "Current", "Future", "Readiness", "Finding"),
    readiness_rows,
)}

This report is advisory. It performs no backfill and must not be used as an
instruction to mutate the database without founder approval for a later PR.
"""
    return {
        "PR-MON-M1-STATUS-AUDIT-1.md": summary,
        "STATUS_MAPPING_REPORT.md": mapping,
        "DUPLICATE_REPORT.md": duplicates,
        "ORPHAN_REPORT.md": orphans,
        "LINKAGE_REPORT.md": linkage,
        "MIGRATION_READINESS_REPORT.md": readiness,
    }


def write_reports(result: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory = output_dir / "runtime_inventory.json"
    inventory.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    for name, content in render_reports(result).items():
        (output_dir / name).write_text(content.rstrip() + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for sanitised JSON and Markdown reports",
    )
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL (never emitted)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise SystemExit(f"Missing required environment variable: {args.database_url_env}")
    result = collect_from_database(database_url)
    write_reports(result, args.output_dir)
    print(
        json.dumps(
            {
                "audit_id": AUDIT_ID,
                "output_dir": str(args.output_dir),
                "total_alerts": result["statistics"]["total_alerts"],
                "transaction_read_only": result["collection"]["transaction_read_only"],
                "completion": result["collection"]["completion"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

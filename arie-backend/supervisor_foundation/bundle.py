"""Read-only canonical input bundle assembler (subject_type = application).

The bundle is the only thing a review sees. Probes never touch a database, so
the assembler is the single boundary between authoritative stores and the
deterministic review path.

Guarantees, all test-enforced:

* **Read-only.** Every statement issued here is a ``SELECT``.
* **No clock.** ``as_of`` is a required parameter. Nothing in this module reads
  a wall clock, and no function that derives a verdict from one is called
  (see :mod:`supervisor_foundation.adapters` for the audit).
* **No side effects.** No provider calls, no screening or registry refresh, no
  document processing, no risk recomputation, no EDD case creation, no periodic
  review creation.
* **Explicit whitelists.** Every section projects a named field list. A column
  added upstream cannot silently enter a hash.
* **Null distinct from absent.** A whitelisted field present-and-null keeps its
  key with a ``None`` value; a field the row does not have at all is omitted and
  named in that section's ``absent_fields``.

**Privacy posture — PII-minimised, not anonymous.**

Excluded by design: **direct identifiers** — names, dates of birth, residential
and registered addresses, contact details, professional profile URLs — together
with free-text officer prose, file paths, storage keys and raw extracted
document field values.

Retained by design: **stable internal identifiers** (``director:{id}``,
``document:{id}``, ``person_key``) and **name fingerprints** (see
:func:`_fingerprint`). These are **pseudonymous, linkable identifiers**: they
carry no direct identifier themselves, but they resolve back to a natural
person by joining against the source tables, and a fingerprint can be reversed
by hashing a list of candidate names.

The bundle is therefore **personal data under GDPR** and must be handled as
such — access-controlled, retention-bound, and within scope of
``gdpr_erasure``. It is not a de-identified artifact, and nothing in this
package should be described as PII-free.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .canonical_json import DATETIME_FORMAT
from .contracts import (
    BUNDLE_SCHEMA_VERSION,
    AvailabilityStatus,
    SubjectType,
)
from .policy import PolicyProfile, unconfigured_policy

# ── Whitelists ───────────────────────────────────────────────────────
# Each tuple is the complete set of fields that section may contribute.

APPLICATION_FIELDS = (
    "base_risk_level",
    "brn",
    "client_id",
    "company_name",
    "country",
    "decided_at",
    "decision_by",
    "elevation_reason_text",
    "entity_type",
    "final_risk_level",
    "first_approved_at",
    "first_approver_id",
    "id",
    "inputs_updated_at",
    "is_fixture",
    "onboarding_lane",
    "ownership_structure",
    "pre_approval_decision",
    "pre_approval_officer_id",
    "pre_approval_timestamp",
    "ref",
    "screening_mode",
    "sector",
    "status",
    "submitted_at",
)

DIRECTOR_FIELDS = (
    "country_of_residence",
    "date_of_appointment",
    "id",
    "is_pep",
    "nationality",
    "person_key",
)

UBO_FIELDS = (
    "country_of_residence",
    "id",
    "is_pep",
    "nationality",
    "ownership_pct",
    "person_key",
)

INTERMEDIARY_FIELDS = (
    "id",
    "jurisdiction",
    "owned_or_controlled_by",
    "ownership_pct",
    "person_key",
)

DOCUMENT_FIELDS = (
    "doc_type",
    "evidence_class",
    "expiry_date",
    "expiry_source",
    "file_sha256",
    "id",
    "is_current",
    "person_id",
    "person_type",
    "review_status",
    "slot_key",
    "superseded_at",
    "superseded_by_document_id",
    "valid_until",
    "verification_status",
    "verified_at",
    "version",
    "workflow_test_accepted",
)

SCREENING_REVIEW_FIELDS = (
    "disposition",
    "disposition_code",
    "id",
    "requires_four_eyes",
    "reviewer_id",
    "second_disposition_code",
    "second_reviewed_at",
    "second_reviewer_id",
    "subject_type",
)

EDD_CASE_FIELDS = (
    "id",
    "linked_monitoring_alert_id",
    "linked_periodic_review_id",
    "origin_context",
    "risk_level",
    "risk_score",
    "senior_reviewer",
    "stage",
    "trigger_source",
)

DECISION_RECORD_FIELDS = (
    "actor_role",
    "actor_user_id",
    "confidence_score",
    "decision_type",
    "id",
    "override_flag",
    "risk_level",
    "source",
    "timestamp",
)

PERIODIC_REVIEW_FIELDS = (
    "due_date",
    "id",
    "last_review_date",
    "new_risk_level",
    "next_review_date",
    "previous_risk_level",
    "review_type",
    "risk_level",
    "status",
    "trigger_type",
)

RISK_FACTOR_FIELDS = (
    "dimension_id",
    "factor_key",
    "factor_weight",
    "normalized_value",
    "resolution_status",
    "rule_score",
    "weighted_factor_contribution",
)

RISK_DIMENSION_FIELDS = (
    "composite_contribution",
    "dimension_id",
    "dimension_score",
    "dimension_weight",
    "factor_keys",
)

MEMO_ROW_FIELDS = (
    "id",
    "memo_version",
    "quality_score",
    "raw_output_hash",
    "review_status",
    "validation_status",
    "version",
)


# ── Stable ordering rules ────────────────────────────────────────────
# Every list in the bundle is sorted here. Nothing relies on database row
# order, which is not guaranteed and differs between SQLite and PostgreSQL.

ORDERING_RULES = {
    "parties.directors": "(person_key, id)",
    "parties.ubos": "(person_key, id)",
    "parties.intermediaries": "(person_key, id)",
    "documents.active": "(doc_type, slot_key, id)",
    "documents.active[].checks": "(check_id)",
    "document_gate.expectations": "(doc_type, person_type, person_id)",
    "screening.reviews": "(subject_type, subject_name_fingerprint, id)",
    "edd.cases": "(id)",
    "decision.records": "(timestamp, id)",
    "monitoring.periodic_reviews": "(id)",
    "risk.factors": "(dimension_id, factor_key)",
    "risk.dimensions": "(dimension_id)",
    "memo.section_keys": "(section_key)",
    "*.absent_fields": "(field_name)",
}


class BundleAssemblyError(RuntimeError):
    """The bundle could not be assembled from current state."""


# ── Row helpers ──────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Normalise a database row to a plain dict.

    Handles ``sqlite3.Row``, ``dict``, and the mapping shape the PostgreSQL
    path returns, so the assembler works identically on both backends.
    """
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except (AttributeError, TypeError):
        return dict(row)


def _fetchall(db: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    """Run a SELECT and return plain dicts.

    Only ``SELECT`` reaches this function; the assertion is a tripwire for a
    future edit rather than a runtime concern.
    """
    if not sql.lstrip().upper().startswith("SELECT"):
        raise BundleAssemblyError(f"assembler issued a non-SELECT statement: {sql!r}")
    cursor = db.execute(sql, tuple(params))
    rows = cursor.fetchall() if cursor is not None else []
    return [_row_to_dict(row) for row in rows or []]


def _project(
    row: Mapping[str, Any], fields: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """Project ``fields`` out of ``row``.

    Returns ``(values, absent_fields)``. A field present with a null value keeps
    its key mapped to ``None``; a field the row does not carry at all is
    reported in ``absent_fields`` instead. That distinction is what lets a later
    probe tell "the institution recorded nothing" apart from "this deployment's
    schema predates the column".
    """
    values: dict[str, Any] = {}
    absent: list[str] = []
    for name in fields:
        if name in row:
            values[name] = _scalar(row[name])
        else:
            absent.append(name)
    return values, sorted(absent)


def _scalar(value: Any) -> Any:
    """Coerce a stored scalar into a canonically serialisable primitive."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (bytes, bytearray)):
        # Stored blobs are not evidence the Supervisor reads; record presence.
        return f"<{len(value)} bytes>"
    return value


def _load_json(value: Any, default: Any) -> Any:
    """Parse a JSON column that may already be decoded by the driver."""
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "on", "t")


def _fingerprint(value: Any) -> str | None:
    """Stable pseudonymous stand-in for a personal-name sort key.

    Screening reviews must be ordered deterministically, and their natural sort
    key is ``subject_name`` — a personal name. Hashing it preserves the ordering
    while keeping the name itself out of the bundle.

    **This is a pseudonym, not an anonymisation.** The input space of personal
    names is small and enumerable, so a fingerprint can be reversed by hashing a
    list of candidate names, and it is stable enough to link the same person
    across cases. Treat it as personal data.
    """
    if value is None:
        return None
    import hashlib

    return hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()[:16]


def _normalise_as_of(as_of: Any) -> str:
    """Validate and canonicalise the caller-supplied ``as_of``.

    This is the only time value in the entire review path. It is a required
    input precisely so that no code here needs a clock.
    """
    if as_of is None:
        raise BundleAssemblyError(
            "as_of is required: the Supervisor foundation never reads a clock"
        )
    if isinstance(as_of, datetime):
        aware = (
            as_of.replace(tzinfo=timezone.utc)
            if as_of.tzinfo is None
            else as_of.astimezone(timezone.utc)
        )
        return aware.strftime(DATETIME_FORMAT)
    text = str(as_of).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleAssemblyError(f"as_of is not an ISO-8601 timestamp: {as_of!r}") from exc
    aware = (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )
    return aware.strftime(DATETIME_FORMAT)


# ── Section assemblers ───────────────────────────────────────────────


def _application_section(row: Mapping[str, Any]) -> dict[str, Any]:
    values, absent = _project(row, APPLICATION_FIELDS)
    return {"fields": values, "absent_fields": absent}


def _parties_section(db: Any, application_id: str) -> dict[str, Any]:
    def collect(table: str, fields: Sequence[str]) -> dict[str, Any]:
        rows = _fetchall(
            db, f"SELECT * FROM {table} WHERE application_id = ?", (application_id,)
        )
        projected = []
        absent: set[str] = set()
        for row in rows:
            values, row_absent = _project(row, fields)
            absent.update(row_absent)
            # Derived presence flag: whether a PEP declaration payload exists at
            # all. The declaration's contents — office held, relationships,
            # named associates — stay out of the bundle.
            if "pep_declaration" in row:
                values["has_pep_declaration"] = bool(
                    _load_json(row.get("pep_declaration"), {})
                )
            projected.append(values)
        projected.sort(
            key=lambda item: (
                str(item.get("person_key") or ""),
                str(item.get("id") or ""),
            )
        )
        return {"rows": projected, "count": len(projected), "absent_fields": sorted(absent)}

    return {
        "directors": collect("directors", DIRECTOR_FIELDS),
        "ubos": collect("ubos", UBO_FIELDS),
        "intermediaries": collect("intermediaries", INTERMEDIARY_FIELDS),
    }


def _check_summary(verification_results: Any) -> list[dict[str, Any]]:
    """Per-check ``(id, status, classification)`` triples, sorted by check id.

    The full ``verification_results`` payload carries extracted field values —
    names, numbers, addresses — so only the check outcomes are lifted. The
    outcomes are the evidentiary part; the extracted values are direct
    identifiers and are excluded.
    """
    parsed = _load_json(verification_results, {})
    checks = parsed.get("checks") if isinstance(parsed, dict) else None
    if not isinstance(checks, list):
        return []
    summary = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        summary.append(
            {
                "check_id": check.get("id"),
                "classification": check.get("classification"),
                "result": check.get("result"),
            }
        )
    summary.sort(key=lambda item: str(item.get("check_id") or ""))
    return summary


def _documents_section(db: Any, application_id: str) -> dict[str, Any]:
    rows = _fetchall(
        db,
        "SELECT * FROM documents WHERE application_id = ? "
        "AND COALESCE(is_current, TRUE) = TRUE",
        (application_id,),
    )
    projected = []
    absent: set[str] = set()
    for row in rows:
        values, row_absent = _project(row, DOCUMENT_FIELDS)
        absent.update(row_absent)
        values["checks"] = _check_summary(row.get("verification_results"))
        projected.append(values)
    projected.sort(
        key=lambda item: (
            str(item.get("doc_type") or ""),
            str(item.get("slot_key") or ""),
            str(item.get("id") or ""),
        )
    )
    return {
        "active": projected,
        "active_count": len(projected),
        "absent_fields": sorted(absent),
    }


def _document_gate_section(db: Any, app_row: Mapping[str, Any]) -> dict[str, Any]:
    """Document-gate *inputs*, not the gate's verdict.

    ``evaluate_document_reliance_gate`` derives its ``stale_verification``
    blocker from ``datetime.now()``, so its output changes between days for
    unchanged evidence and cannot enter a hashed bundle. Its expectation builder
    is clock-free and side-effect-free, so that is called directly and the
    per-document reliance state is carried alongside. A later probe derives
    staleness from ``as_of``.
    """
    from document_reliance_gate import (  # local: authoritative, read-only
        ALLOWED_RELIANCE_STATES,
        DEFAULT_STALE_DAYS,
        MANUAL_ACCEPTANCE_ROLES,
        POLICY_VERSION,
        build_required_document_expectations,
    )

    expectations = build_required_document_expectations(db, dict(app_row))
    normalised = []
    for expectation in expectations:
        # ``label`` is deliberately dropped: the gate builds per-party labels of
        # the form "Passport / Government ID for <full name>", so carrying it
        # would put personal names into a hashed, exportable structure.
        # doc_type + person_type + person_id identify the expectation exactly.
        normalised.append(
            {
                "doc_type": expectation.get("doc_type"),
                "person_id": expectation.get("person_id"),
                "person_type": expectation.get("person_type"),
            }
        )
    normalised.sort(
        key=lambda item: (
            str(item.get("doc_type") or ""),
            str(item.get("person_type") or ""),
            str(item.get("person_id") or ""),
        )
    )
    return {
        "gate_policy_version": POLICY_VERSION,
        "allowed_reliance_states": sorted(ALLOWED_RELIANCE_STATES),
        "manual_acceptance_roles": sorted(MANUAL_ACCEPTANCE_ROLES),
        "stale_days": DEFAULT_STALE_DAYS,
        "expectations": normalised,
        "expectation_count": len(normalised),
        "verdict_included": False,
        "verdict_exclusion_reason": (
            "evaluate_document_reliance_gate derives staleness from a wall "
            "clock; probes derive it from as_of instead"
        ),
    }


def _screening_section(
    db: Any, application_id: str, prescreening: Mapping[str, Any]
) -> dict[str, Any]:
    """Stored screening evidence, not a computed truth summary.

    ``build_screening_truth_summary`` compares ``screening_valid_until`` against
    ``datetime.now()`` (via ``screening_state._timestamp_is_past``), so its
    ``freshness``, ``stale`` and ``approval_blocking`` outputs move with the
    calendar. The stored report and officer dispositions are carried instead.
    """
    report = prescreening.get("screening_report")
    report = report if isinstance(report, dict) else None

    reviews_raw = _fetchall(
        db,
        "SELECT * FROM screening_reviews WHERE application_id = ?",
        (application_id,),
    )
    reviews = []
    absent: set[str] = set()
    for row in reviews_raw:
        values, row_absent = _project(row, SCREENING_REVIEW_FIELDS)
        absent.update(row_absent)
        values["subject_name_fingerprint"] = _fingerprint(row.get("subject_name"))
        reviews.append(values)
    reviews.sort(
        key=lambda item: (
            str(item.get("subject_type") or ""),
            str(item.get("subject_name_fingerprint") or ""),
            str(item.get("id") or ""),
        )
    )

    report_summary: dict[str, Any] | None = None
    if report is not None:
        report_summary = {
            "provider": report.get("provider"),
            "screening_provider": report.get("screening_provider"),
            "screening_mode": report.get("screening_mode"),
            "screened_at": report.get("screened_at"),
            "director_screening_count": len(report.get("director_screenings") or []),
            "ubo_screening_count": len(report.get("ubo_screenings") or []),
            "company_screening_present": isinstance(
                report.get("company_screening"), dict
            ),
        }

    return {
        "report_present": report is not None,
        "report_summary": report_summary,
        "screening_valid_until": prescreening.get("screening_valid_until"),
        "reviews": reviews,
        "review_count": len(reviews),
        "absent_fields": sorted(absent),
        "truth_summary_included": False,
        "truth_summary_exclusion_reason": (
            "build_screening_truth_summary derives freshness from a wall clock; "
            "probes derive it from as_of instead"
        ),
    }


def _risk_section(row: Mapping[str, Any]) -> dict[str, Any]:
    """Stored risk evidence. Nothing is recomputed.

    ``compute_risk_score`` is not called: recomputation would be a side effect
    on a case whose stored score is the authoritative current state, and the
    stored ``factor_computation_evidence`` already carries per-factor
    provenance under its own declared schema version.
    """
    dimensions_blob = _load_json(row.get("risk_dimensions"), {})
    evidence = (
        dimensions_blob.get("factor_computation_evidence")
        if isinstance(dimensions_blob, dict)
        else None
    )
    evidence = evidence if isinstance(evidence, dict) else {}

    factors = []
    for factor in evidence.get("factors") or []:
        if not isinstance(factor, dict):
            continue
        values, _ = _project(factor, RISK_FACTOR_FIELDS)
        factors.append(values)
    factors.sort(
        key=lambda item: (
            str(item.get("dimension_id") or ""),
            str(item.get("factor_key") or ""),
        )
    )

    dimensions = []
    for dimension in evidence.get("dimensions") or []:
        if not isinstance(dimension, dict):
            continue
        values, _ = _project(dimension, RISK_DIMENSION_FIELDS)
        if isinstance(values.get("factor_keys"), list):
            values["factor_keys"] = sorted(str(key) for key in values["factor_keys"])
        dimensions.append(values)
    dimensions.sort(key=lambda item: str(item.get("dimension_id") or ""))

    return {
        "risk_score": row.get("risk_score"),
        "risk_level": row.get("risk_level"),
        "base_risk_level": row.get("base_risk_level"),
        "final_risk_level": row.get("final_risk_level"),
        "risk_config_version": row.get("risk_config_version"),
        "elevation_reason_text": row.get("elevation_reason_text"),
        "escalations": sorted(
            str(item) for item in (_load_json(row.get("risk_escalations"), []) or [])
        ),
        "evidence_schema_version": evidence.get("schema_version"),
        "dimensions": dimensions,
        "factors": factors,
        "factor_count": len(factors),
        "evidence_present": bool(evidence),
        "recomputed": False,
    }


def _edd_section(
    db: Any, application_id: str, memo_metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Stored EDD state. The routing policy is not re-evaluated.

    Re-running ``evaluate_edd_routing`` and comparing against the actual route
    is probe P-03, which is Phase 0B-2 scope.
    """
    rows = _fetchall(
        db, "SELECT * FROM edd_cases WHERE application_id = ?", (application_id,)
    )
    cases = []
    absent: set[str] = set()
    for row in rows:
        values, row_absent = _project(row, EDD_CASE_FIELDS)
        absent.update(row_absent)
        cases.append(values)
    cases.sort(key=lambda item: str(item.get("id") or ""))

    stored_routing = memo_metadata.get("edd_routing")
    stored_routing = stored_routing if isinstance(stored_routing, dict) else None
    routing_projection = None
    if stored_routing is not None:
        routing_projection = {
            "policy_version": stored_routing.get("policy_version"),
            "route": stored_routing.get("route"),
            "triggers": sorted(
                str(trigger) for trigger in (stored_routing.get("triggers") or [])
            ),
        }

    return {
        "cases": cases,
        "case_count": len(cases),
        "absent_fields": sorted(absent),
        "stored_routing": routing_projection,
        "policy_reevaluated": False,
    }


def _memo_section(db: Any, application_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Latest canonical memo. Returns ``(section, memo_data)``.

    ``memo_data`` is handed to the supervisor-verdict adapter and is not itself
    placed in the bundle: it contains generated prose, which is narration and is
    excluded from every hash.
    """
    from memo_governance import latest_compliance_memo_row  # local: read-only SELECT

    row = _row_to_dict(latest_compliance_memo_row(db, application_id))
    if not row:
        return (
            {
                "present": False,
                "fields": {},
                "absent_fields": [],
                "section_keys": [],
                "metadata": {},
                "validation_issue_count": 0,
            },
            {},
        )

    values, absent = _project(row, MEMO_ROW_FIELDS)
    memo_data = _load_json(row.get("memo_data"), {})
    memo_data = memo_data if isinstance(memo_data, dict) else {}
    sections = memo_data.get("sections")
    section_keys = sorted(sections.keys()) if isinstance(sections, dict) else []
    metadata = memo_data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    # Metadata projection: decision-relevant scalars only. The memo's generated
    # prose (key_findings, conditions, review_checklist) is narration.
    metadata_projection = {
        "ai_source": metadata.get("ai_source"),
        "approval_recommendation": metadata.get("approval_recommendation"),
        "documentation_complete": metadata.get("documentation_complete"),
        "document_count": metadata.get("document_count"),
        "low_confidence_flag": metadata.get("low_confidence_flag"),
        "risk_rating": metadata.get("risk_rating"),
        "risk_score": metadata.get("risk_score"),
        "supervisor_status": metadata.get("supervisor_status"),
        "verified_document_count": metadata.get("verified_document_count"),
    }

    issues = _load_json(row.get("validation_issues"), [])
    return (
        {
            "present": True,
            "fields": values,
            "absent_fields": absent,
            "section_keys": section_keys,
            "metadata": metadata_projection,
            "validation_issue_count": len(issues) if isinstance(issues, list) else 0,
        },
        memo_data,
    )


def _supervisor_verdict_section(memo_data: Mapping[str, Any]) -> dict[str, Any]:
    """Memo-supervisor verdict with ``checked_at`` stripped."""
    if not memo_data:
        return {
            "present": False,
            "verdict": None,
            "availability_status": AvailabilityStatus.DATA_ABSENT.value,
        }
    from .adapters import memo_supervisor_verdict

    verdict = memo_supervisor_verdict(memo_data)
    projection = {
        "verdict": verdict.get("verdict"),
        "contradiction_count": verdict.get("contradiction_count"),
        "warning_count": verdict.get("warning_count"),
        "can_approve": verdict.get("can_approve"),
        "requires_sco_review": verdict.get("requires_sco_review"),
        "mandatory_escalation": verdict.get("mandatory_escalation"),
        "mandatory_escalation_reasons": sorted(
            str(reason) for reason in (verdict.get("mandatory_escalation_reasons") or [])
        ),
        "supervisor_confidence": verdict.get("supervisor_confidence"),
        "rule_engine_status": verdict.get("rule_engine_status"),
        "contradiction_categories": sorted(
            str(item.get("category"))
            for item in (verdict.get("contradictions") or [])
            if isinstance(item, dict)
        ),
    }
    return {
        "present": True,
        "verdict": projection,
        "availability_status": AvailabilityStatus.AVAILABLE.value,
    }


def _decision_section(
    db: Any, app_row: Mapping[str, Any]
) -> dict[str, Any]:
    ref = app_row.get("ref")
    rows = (
        _fetchall(
            db, "SELECT * FROM decision_records WHERE application_ref = ?", (ref,)
        )
        if ref
        else []
    )
    records = []
    absent: set[str] = set()
    for row in rows:
        values, row_absent = _project(row, DECISION_RECORD_FIELDS)
        absent.update(row_absent)
        values["override_flag"] = _as_bool(values.get("override_flag"))
        values["override_reason_present"] = bool(
            str(row.get("override_reason") or "").strip()
        )
        records.append(values)
    records.sort(
        key=lambda item: (str(item.get("timestamp") or ""), str(item.get("id") or ""))
    )
    return {
        "records": records,
        "record_count": len(records),
        "absent_fields": sorted(absent),
        "decided_at": app_row.get("decided_at"),
        "decision_by": app_row.get("decision_by"),
    }


def _monitoring_section(db: Any, application_id: str) -> dict[str, Any]:
    rows = _fetchall(
        db, "SELECT * FROM periodic_reviews WHERE application_id = ?", (application_id,)
    )
    reviews = []
    absent: set[str] = set()
    for row in rows:
        values, row_absent = _project(row, PERIODIC_REVIEW_FIELDS)
        absent.update(row_absent)
        reviews.append(values)
    reviews.sort(key=lambda item: str(item.get("id") or ""))
    return {
        "periodic_reviews": reviews,
        "periodic_review_count": len(reviews),
        "absent_fields": sorted(absent),
    }


def _registry_credentials_available() -> bool:
    """Whether any public-registry credential is configured.

    Reads module constants only — no network call, no provider handshake.
    """
    try:
        from company_registry import COMPANIES_HOUSE_API_KEY
    except Exception:  # pragma: no cover - import guard only
        return False
    return bool(str(COMPANIES_HOUSE_API_KEY or "").strip())


def _gated_supervisor_available() -> bool:
    """Whether the enterprise-gated ``supervisor/`` package would serve.

    Mirrors ``server._ai_supervisor_enabled`` using the same two pure inputs,
    rather than importing ``server`` — importing it would build the route table
    and is emphatically not read-only.
    """
    try:
        from environment import flags, pilot_scope_active
    except Exception:  # pragma: no cover - import guard only
        return False
    return (not pilot_scope_active()) and bool(flags.is_enabled("ENABLE_AI_SUPERVISOR"))


def _availability_section(
    *,
    policy: PolicyProfile,
    screening: Mapping[str, Any],
    risk: Mapping[str, Any],
    memo: Mapping[str, Any],
) -> dict[str, Any]:
    """Per-dependency availability.

    A dependency that is unavailable here must cause any probe relying on it to
    report ``unavailable`` — never ``clear``. Phase 0B-1 ships no probes, so
    this section exists to carry the determination into the bundle for 0B-2.
    """
    dependencies = {
        "gated_supervisor_package": (
            AvailabilityStatus.AVAILABLE
            if _gated_supervisor_available()
            else AvailabilityStatus.DEPENDENCY_GATED
        ),
        "public_registry": (
            AvailabilityStatus.AVAILABLE
            if _registry_credentials_available()
            else AvailabilityStatus.CREDENTIALS_ABSENT
        ),
        "screening_report": (
            AvailabilityStatus.AVAILABLE
            if screening.get("report_present")
            else AvailabilityStatus.DATA_ABSENT
        ),
        "risk_factor_evidence": (
            AvailabilityStatus.AVAILABLE
            if risk.get("evidence_present")
            else AvailabilityStatus.SNAPSHOT_INCOMPLETE
        ),
        "memo": (
            AvailabilityStatus.AVAILABLE
            if memo.get("present")
            else AvailabilityStatus.DATA_ABSENT
        ),
        "historic_decision_snapshot": AvailabilityStatus.SNAPSHOT_INCOMPLETE,
    }
    return {
        "dependencies": {name: status.value for name, status in sorted(dependencies.items())},
        "policy": policy.availability_metadata()["keys"],
        "historic_replay_note": (
            "applications rows are mutated in place with no snapshot table, so "
            "state as at a past decision cannot be reconstructed; probes "
            "evaluating a historic decision must report not_replayable"
        ),
    }


# ── Entry point ──────────────────────────────────────────────────────


def assemble_application_bundle(
    db: Any,
    application_id: str,
    *,
    as_of: Any,
    policy: PolicyProfile | None = None,
) -> dict[str, Any]:
    """Assemble the canonical current-state bundle for one application.

    Args:
        db: A read-only-usable connection exposing ``execute(sql, params)``.
            Only ``SELECT`` statements are issued.
        application_id: ``applications.id``.
        as_of: Caller-supplied review instant. Required — the foundation never
            reads a clock.
        policy: Institution policy contract. Defaults to fully unconfigured, so
            every policy key reports ``policy_not_configured``.

    Returns:
        The bundle, without a hash. ``input_bundle_hash`` is computed by
        :func:`supervisor_foundation.hashing.compute_input_bundle_hash` and
        recorded on the review, not inside the bundle it describes.
    """
    if not application_id:
        raise BundleAssemblyError("application_id is required")

    resolved_policy = policy if policy is not None else unconfigured_policy()
    as_of_text = _normalise_as_of(as_of)

    rows = _fetchall(db, "SELECT * FROM applications WHERE id = ?", (application_id,))
    if not rows:
        raise BundleAssemblyError(f"application not found: {application_id!r}")
    app_row = rows[0]

    prescreening = _load_json(app_row.get("prescreening_data"), {})
    prescreening = prescreening if isinstance(prescreening, dict) else {}

    memo_section, memo_data = _memo_section(db, application_id)
    memo_metadata = memo_data.get("metadata") if isinstance(memo_data, dict) else {}
    memo_metadata = memo_metadata if isinstance(memo_metadata, dict) else {}

    screening = _screening_section(db, application_id, prescreening)
    risk = _risk_section(app_row)

    return {
        "application": _application_section(app_row),
        "availability": _availability_section(
            policy=resolved_policy, screening=screening, risk=risk, memo=memo_section
        ),
        "decision": _decision_section(db, app_row),
        "document_gate": _document_gate_section(db, app_row),
        "documents": _documents_section(db, application_id),
        "edd": _edd_section(db, application_id, memo_metadata),
        "memo": memo_section,
        "meta": {
            "as_of": as_of_text,
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "subject_id": str(application_id),
            "subject_type": SubjectType.APPLICATION.value,
        },
        "monitoring": _monitoring_section(db, application_id),
        "parties": _parties_section(db, application_id),
        "policy": resolved_policy.availability_metadata(),
        "risk": risk,
        "screening": screening,
        "supervisor_verdict": _supervisor_verdict_section(memo_data),
    }

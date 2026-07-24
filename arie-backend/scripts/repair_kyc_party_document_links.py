#!/usr/bin/env python3
"""Diagnose and repair incident-scoped KYC party/document linkage in staging.

The default mode is read-only.  Apply mode is intentionally difficult to
invoke: it requires explicit application refs, ``ENVIRONMENT=staging``, and an
exact confirmation phrase.  The utility never discovers a broad repair scope.

The repair canonicalises a person document from a legacy ``person_key`` (or an
already-canonical party row id) to the unique, application-scoped party row id
and rebuilds its typed slot key:

    person:{director|ubo|intermediary}:{party_row_id}:{document_type}

Ambiguous, duplicate, conflicting, or unresolved references refuse the entire
atomic apply.  Fixture-marker correction is limited to the exact application
identities affected by the July 2026 historical-ref-reuse incident.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, urlsplit


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from document_scope_policy import base_document_scope_error_for_canonical_type


LOGGER = logging.getLogger("kyc_party_document_repair")

REPORT_VERSION = 1
APPLY_CONFIRMATION = "APPLY_STAGING_KYC_PARTY_DOCUMENT_REPAIR"
STAGING_DATABASE_FINGERPRINT_ENV = "REGMIND_STAGING_DATABASE_FINGERPRINT"
MAX_APPLICATION_REFS = 50

# libpq permits URI query parameters such as ``host`` and ``dbname`` to
# override the apparent URI authority/path. Only transport/session parameters
# that cannot redirect the connection are accepted when calculating the
# positive staging identity.
SAFE_POSTGRES_DSN_QUERY_PARAMETERS = {
    "application_name",
    "channel_binding",
    "connect_timeout",
    "gssencmode",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "sslcert",
    "sslcrl",
    "sslcrldir",
    "sslkey",
    "ssl_max_protocol_version",
    "ssl_min_protocol_version",
    "sslmode",
    "sslpassword",
    "sslrootcert",
    "sslsni",
    "tcp_user_timeout",
}
TARGET_AFFECTING_LIBPQ_ENVIRONMENT = (
    "PGDATABASE",
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
)

PARTY_TABLES: Tuple[Tuple[str, str], ...] = (
    ("director", "directors"),
    ("ubo", "ubos"),
    ("intermediary", "intermediaries"),
)

# Inline migration v2.29 historically marked these refs alone in staging.
# Ref-only matching is unsafe because application refs can be reused when a
# staging database is restored or reseeded.
REF_ONLY_HISTORICAL_FIXTURE_IDENTITIES = {
    "ARF-2026-100454": "EX06 DualApproval Test Corp",
    "ARF-2026-100456": "EX06 Validation TestCo Ltd",
    "ARF-2026-100455": "HighRisk Dual Approval Test Ltd",
    "ARF-2026-100421": "Pipeline Test Corp Ltd",
    "ARF-2026-100424": "Portal Audit Test Ltd",
    "ARF-2026-100430": "Probe Test Co",
    "ARF-2026-100428": "test 2",
    "ARF-2026-100427": "test [QA-R10-mnyuuv7q]",
    "ARF-2026-900372": "Smoke Holdco Ltd",
}

# Only these exact replacement identities are authorised for automatic marker
# correction.  A different company on a historically reused ref is surfaced
# as suspicious, but is refused rather than guessed.
APPROVED_REF_REUSE_IDENTITIES = {
    "ARF-2026-100421": "E2E-20260724-150642-S01-Low-Risk",
    "ARF-2026-100430": "E2E-20260724-150642-S02-Cross-Border",
    "ARF-2026-100428": "E2E-20260724-150642-S03-Geographic-Risk",
    "ARF-2026-100424": "E2E-20260724-150642-S07-Higher-Risk-Sector",
    "ARF-2026-100427": "E2E-20260724-150642-S09-Validation-Recovery",
}

# Kept local so the repair does not import server.py (which initialises the
# application runtime).  Values mirror the backend's canonical upload aliases.
DOCUMENT_TYPE_NORMALIZE = {
    "doc-coi": "cert_inc",
    "certificate-incorporation": "cert_inc",
    "certificate incorporation": "cert_inc",
    "certificate_of_incorporation": "cert_inc",
    "certificate of incorporation": "cert_inc",
    "incorporation_certificate": "cert_inc",
    "incorporation certificate": "cert_inc",
    "proof_of_address": "poa",
    "proof of address": "poa",
    "address_proof": "poa",
    "financial_statements": "fin_stmt",
    "financial statements": "fin_stmt",
    "source_of_wealth": "source_wealth",
    "source of wealth": "source_wealth",
    "source_of_funds": "source_funds",
    "source of funds": "source_funds",
    "id_card": "national_id",
    "identity_card": "national_id",
    "drivers_license": "national_id",
    "driver_license": "national_id",
    "driving_license": "national_id",
    "director_id": "national_id",
    "ubo_id": "national_id",
    "doc-memarts": "memarts",
    "memorandum_of_association": "memarts",
    "memorandum of association": "memarts",
    "memorandum_and_articles": "memarts",
    "memorandum and articles": "memarts",
    "memorandum_articles": "memarts",
    "articles_of_association": "memarts",
    "articles of association": "memarts",
    "doc-shareholders": "reg_sh",
    "register_of_shareholders": "reg_sh",
    "register of shareholders": "reg_sh",
    "shareholder_register": "reg_sh",
    "shareholder register": "reg_sh",
    "doc-directors-reg": "reg_dir",
    "register_of_directors": "reg_dir",
    "register of directors": "reg_dir",
    "director_register": "reg_dir",
    "director register": "reg_dir",
    "doc-financials": "fin_stmt",
    "doc-proof-address": "poa",
    "doc-board-res": "board_res",
    "board_resolution": "board_res",
    "board resolution": "board_res",
    "doc-structure-chart": "structure_chart",
    "structure chart": "structure_chart",
    "ownership_structure_chart": "structure_chart",
    "doc-bank-ref": "bankref",
    "bank_reference": "bankref",
    "bank reference": "bankref",
    "doc-license-cert": "licence",
    "license": "licence",
    "licence_certificate": "licence",
    "license_certificate": "licence",
    "doc-contracts": "contracts",
    "doc-source-wealth-proof": "source_wealth",
    "doc-source-funds-proof": "source_funds",
    "doc-bank-statements": "bank_statements",
    "bank statements": "bank_statements",
    "doc-aml-policy": "aml_policy",
    "aml policy": "aml_policy",
    "general": "supporting_document",
}

PERSON_TYPE_ALIASES = {
    "director": "director",
    "directors": "director",
    "dir": "director",
    "ubo": "ubo",
    "ubos": "ubo",
    "beneficial_owner": "ubo",
    "beneficial-owner": "ubo",
    "intermediary": "intermediary",
    "intermediaries": "intermediary",
    "inter": "intermediary",
    "int": "intermediary",
}

REQUIRED_COLUMNS = {
    "applications": {
        "id",
        "ref",
        "company_name",
        "status",
        "is_fixture",
    },
    "directors": {"id", "application_id", "person_key"},
    "ubos": {"id", "application_id", "person_key"},
    "intermediaries": {"id", "application_id", "person_key"},
    "documents": {
        "id",
        "application_id",
        "person_id",
        "person_type",
        "doc_type",
        "slot_key",
        "is_current",
        "version",
    },
}


class RepairSafetyError(RuntimeError):
    """Raised when apply-mode safety preconditions are not satisfied."""


def _as_dict(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _rows(result: Any) -> List[Dict[str, Any]]:
    return [_as_dict(row) for row in result.fetchall()]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _same_identity_text(left: Any, right: Any) -> bool:
    def normalise(value: Any) -> str:
        return " ".join(_clean(value).split()).casefold()

    return normalise(left) == normalise(right)


def _truthy_db_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _clean(value).lower() not in ("", "0", "false", "f", "no", "n", "off")


def normalize_application_refs(values: Optional[Iterable[str]]) -> List[str]:
    refs: List[str] = []
    seen = set()
    for value in values or []:
        for candidate in str(value or "").split(","):
            ref = candidate.strip()
            if ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    if not refs:
        raise RepairSafetyError(
            "at least one explicit --application-ref is required; broad discovery is disabled"
        )
    if len(refs) > MAX_APPLICATION_REFS:
        raise RepairSafetyError(
            f"refusing {len(refs)} refs; maximum explicit scope is {MAX_APPLICATION_REFS}"
        )
    return refs


def normalize_person_type(value: Any) -> Optional[str]:
    normalized = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        _clean(value).lower(),
    ).strip("_")
    return PERSON_TYPE_ALIASES.get(normalized)


def normalize_document_type(value: Any) -> str:
    raw = _clean(value) or "general"
    raw_lower = raw.lower()
    candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_lower).strip("_")
    normalized = DOCUMENT_TYPE_NORMALIZE.get(
        raw,
        DOCUMENT_TYPE_NORMALIZE.get(
            raw_lower,
            DOCUMENT_TYPE_NORMALIZE.get(candidate, candidate),
        ),
    )
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", normalized).strip("_").lower()
    return (normalized or "general")[:80]


def _table_columns(db: Any, table: str) -> set:
    if getattr(db, "is_postgres", False):
        return {
            row["column_name"]
            for row in _rows(
                db.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = ?
                    """,
                    (table,),
                )
            )
        }
    return {
        row["name"]
        for row in _rows(db.execute(f"PRAGMA table_info({table})"))
    }


def validate_schema(db: Any, *, require_audit: bool = False) -> List[Dict[str, Any]]:
    requirements = dict(REQUIRED_COLUMNS)
    if require_audit:
        requirements["audit_log"] = {
            "id",
            "timestamp",
            "user_id",
            "user_name",
            "user_role",
            "action",
            "target",
            "application_id",
            "detail",
            "ip_address",
            "before_state",
            "after_state",
            "previous_hash",
            "entry_hash",
            "request_id",
        }

    refusals = []
    for table, required in requirements.items():
        actual = _table_columns(db, table)
        missing = sorted(required - actual)
        if missing:
            refusals.append(
                {
                    "scope": "schema",
                    "code": "missing_required_columns",
                    "table": table,
                    "columns": missing,
                }
            )
    return refusals


def _fixture_diagnostic(app: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    app_id = _clean(app.get("id"))
    ref = _clean(app.get("ref"))
    company_name = _clean(app.get("company_name"))
    is_fixture = _truthy_db_bool(app.get("is_fixture"))
    historical_company = REF_ONLY_HISTORICAL_FIXTURE_IDENTITIES.get(ref)
    approved_replacement = APPROVED_REF_REUSE_IDENTITIES.get(ref)

    finding = {
        "is_fixture": is_fixture,
        "reserved_fixture_id": app_id.lower().startswith("f1xed"),
        "historical_ref_only_identity": historical_company,
        "change": None,
    }
    refusals: List[Dict[str, Any]] = []
    if not is_fixture:
        return finding, refusals

    if finding["reserved_fixture_id"]:
        refusals.append(
            {
                "scope": "application",
                "code": "reserved_fixture_application",
                "application_ref": ref,
            }
        )
        return finding, refusals

    if not historical_company:
        refusals.append(
            {
                "scope": "application",
                "code": "unclassified_fixture_application",
                "application_ref": ref,
            }
        )
        return finding, refusals

    if _same_identity_text(company_name, historical_company):
        refusals.append(
            {
                "scope": "application",
                "code": "confirmed_historical_fixture_identity",
                "application_ref": ref,
            }
        )
        return finding, refusals

    finding["suspected_false_positive"] = True
    finding["reason"] = "historical ref-only fixture marker does not match current company identity"
    if approved_replacement and _same_identity_text(company_name, approved_replacement):
        finding["change"] = {
            "field": "is_fixture",
            "from": True,
            "to": False,
            "action": "repair",
            "reason": "exact approved July 2026 staging ref-reuse identity",
        }
        return finding, refusals

    refusals.append(
        {
            "scope": "application",
            "code": "unapproved_fixture_ref_reuse_identity",
            "application_ref": ref,
            "historical_company": historical_company,
        }
    )
    return finding, refusals


def _load_parties(
    db: Any,
    application_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    parties: List[Dict[str, Any]] = []
    reference_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    refusals: List[Dict[str, Any]] = []

    for person_type, table in PARTY_TABLES:
        table_rows = _rows(
            db.execute(
                f"""
                SELECT id, application_id, person_key
                FROM {table}
                WHERE application_id = ?
                ORDER BY id
                """,
                (application_id,),
            )
        )
        for row in table_rows:
            raw_party_id = str(row.get("id") or "")
            party_id = raw_party_id.strip()
            person_key = _clean(row.get("person_key"))
            party = {
                "person_type": person_type,
                "party_id": party_id,
                "person_key": person_key or None,
                "change": None,
            }
            parties.append(party)
            if not party_id or party_id != raw_party_id:
                refusals.append(
                    {
                        "scope": "party",
                        "code": "invalid_party_id",
                        "person_type": person_type,
                        "party_id": raw_party_id,
                    }
                )
                continue

            aliases = []
            for alias in (party_id, person_key):
                if alias and alias not in aliases:
                    aliases.append(alias)
                    reference_index[alias].append(party)
            party["reference_aliases"] = aliases

    for reference, candidates in sorted(reference_index.items()):
        unique_candidates = {
            (candidate["person_type"], candidate["party_id"])
            for candidate in candidates
        }
        if len(unique_candidates) > 1:
            refusals.append(
                {
                    "scope": "party",
                    "code": "duplicate_party_reference",
                    "reference": reference,
                    "candidates": [
                        {"person_type": person_type, "party_id": party_id}
                        for person_type, party_id in sorted(unique_candidates)
                    ],
                }
            )

    return parties, reference_index, refusals


def _slot_reference_finding(
    slot_key: str,
    document_person_ref: str,
    normalized_document_type: str,
    resolved_party: Dict[str, Any],
    reference_index: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate that existing slot metadata agrees with the stored document.

    This incident repair must not guess that conflicting slot metadata is stale:
    a type, owner, or category disagreement can be evidence of the displacement
    under investigation.  Such rows are therefore refused for manual review.
    """
    refusals: List[Dict[str, Any]] = []
    notes: List[str] = []
    slot = _clean(slot_key)
    if not slot:
        notes.append("missing slot_key will be rebuilt")
        return refusals, notes

    parts = slot.split(":")
    if parts[0] != "person":
        refusals.append(
            {
                "code": "unsupported_person_document_slot_namespace",
                "slot_key": slot,
            }
        )
        return refusals, notes

    if len(parts) == 3:
        slot_type = None
        slot_person_ref = parts[1]
        slot_document_type = normalize_document_type(parts[2])
        notes.append("legacy untyped person slot will be rebuilt")
    elif len(parts) == 4:
        slot_type = normalize_person_type(parts[1])
        slot_person_ref = parts[2]
        slot_document_type = normalize_document_type(parts[3])
        if not slot_type:
            refusals.append(
                {
                    "code": "invalid_slot_person_type",
                    "slot_key": slot,
                }
            )
            return refusals, notes
    else:
        refusals.append(
            {
                "code": "malformed_person_slot_key",
                "slot_key": slot,
            }
        )
        return refusals, notes

    if slot_document_type != normalized_document_type:
        refusals.append(
            {
                "code": "conflicting_slot_document_type",
                "slot_key": slot,
                "slot_document_type": slot_document_type,
                "document_type": normalized_document_type,
            }
        )

    slot_matches = reference_index.get(_clean(slot_person_ref), [])
    unique_slot_matches = {
        (candidate["person_type"], candidate["party_id"])
        for candidate in slot_matches
    }
    resolved_identity = (
        resolved_party["person_type"],
        resolved_party["party_id"],
    )
    if len(unique_slot_matches) > 1:
        refusals.append(
            {
                "code": "ambiguous_slot_party_reference",
                "slot_key": slot,
            }
        )
    elif len(unique_slot_matches) == 1 and resolved_identity not in unique_slot_matches:
        refusals.append(
            {
                "code": "conflicting_document_and_slot_party",
                "document_person_ref": document_person_ref,
                "slot_person_ref": slot_person_ref,
                "slot_party": {
                    "person_type": next(iter(unique_slot_matches))[0],
                    "party_id": next(iter(unique_slot_matches))[1],
                },
            }
        )
    elif not unique_slot_matches and _clean(slot_person_ref) != document_person_ref:
        refusals.append(
            {
                "code": "unresolved_conflicting_slot_party_reference",
                "document_person_ref": document_person_ref,
                "slot_person_ref": slot_person_ref,
            }
        )

    if slot_type and slot_type != resolved_party["person_type"]:
        refusals.append(
            {
                "code": "conflicting_slot_person_type",
                "slot_key": slot,
                "slot_person_type": slot_type,
                "resolved_person_type": resolved_party["person_type"],
            }
        )
    return refusals, notes


def _load_documents(
    db: Any,
    application_id: str,
    application_ref: str,
    reference_index: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    documents: List[Dict[str, Any]] = []
    refusals: List[Dict[str, Any]] = []
    rows = _rows(
        db.execute(
            """
            SELECT id, application_id, person_id, doc_type, slot_key,
                   person_type, is_current, version
            FROM documents
            WHERE application_id = ?
            ORDER BY id
            """,
            (application_id,),
        )
    )

    for row in rows:
        document_id = _clean(row.get("id"))
        raw_person_id = row.get("person_id")
        person_ref = _clean(raw_person_id)
        raw_person_type = row.get("person_type")
        stored_person_type = normalize_person_type(raw_person_type)
        slot_key = row.get("slot_key")
        stored_doc_type = _clean(row.get("doc_type"))
        normalized_doc_type = normalize_document_type(stored_doc_type)
        document = {
            "document_id": document_id,
            "doc_type": row.get("doc_type"),
            "normalized_doc_type": normalized_doc_type,
            "is_current": _truthy_db_bool(row.get("is_current"), default=True),
            "version": row.get("version"),
            "current": {
                "person_id": raw_person_id,
                "person_type": raw_person_type,
                "slot_key": slot_key,
            },
            "resolved_party": None,
            "proposed": None,
            "action": "unchanged",
            "notes": [],
            "refusals": [],
        }
        documents.append(document)

        if stored_doc_type != normalized_doc_type:
            refusal = {
                "code": "noncanonical_stored_document_type",
                "stored_doc_type": stored_doc_type,
                "canonical_doc_type": normalized_doc_type,
                "reason": (
                    "document category aliases require a separately reviewed "
                    "migration; this repair does not mutate doc_type"
                ),
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue

        if not person_ref:
            if raw_person_type not in (None, ""):
                refusal = {
                    "code": "document_person_type_without_person_id",
                    "person_type": raw_person_type,
                }
                document["action"] = "refused"
                document["refusals"].append(refusal)
                refusals.append(
                    {
                        "scope": "document",
                        "application_ref": application_ref,
                        "document_id": document_id,
                        **refusal,
                    }
                )
            elif _clean(slot_key).startswith("person:"):
                refusal = {
                    "code": "missing_document_person_reference",
                    "slot_key": slot_key,
                }
                document["action"] = "refused"
                document["refusals"].append(refusal)
                refusals.append(
                    {
                        "scope": "document",
                        "application_ref": application_ref,
                        "document_id": document_id,
                        **refusal,
                    }
                )
            else:
                document["notes"].append("entity or non-party document; not in repair scope")
            continue

        if raw_person_type not in (None, "") and not stored_person_type:
            refusal = {
                "code": "invalid_document_person_type",
                "person_type": raw_person_type,
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue

        candidates = reference_index.get(person_ref, [])
        unique_candidates = {
            (candidate["person_type"], candidate["party_id"])
            for candidate in candidates
        }
        if not unique_candidates:
            refusal = {
                "code": "unresolved_document_party_reference",
                "person_reference": person_ref,
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue
        if len(unique_candidates) > 1:
            refusal = {
                "code": "ambiguous_document_party_reference",
                "person_reference": person_ref,
                "candidates": [
                    {"person_type": person_type, "party_id": party_id}
                    for person_type, party_id in sorted(unique_candidates)
                ],
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue

        person_type, party_id = next(iter(unique_candidates))
        if stored_person_type and stored_person_type != person_type:
            refusal = {
                "code": "conflicting_stored_document_person_type",
                "stored_person_type": stored_person_type,
                "resolved_person_type": person_type,
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue
        resolved_party = {
            "person_type": person_type,
            "party_id": party_id,
        }
        document["resolved_party"] = resolved_party
        scope_error = base_document_scope_error_for_canonical_type(
            normalized_doc_type,
            person_type,
        )
        if scope_error:
            refusal = {
                "code": "invalid_document_scope",
                "doc_type": normalized_doc_type,
                "person_type": person_type,
                "reason": (
                    f"{scope_error}; this repair refuses to create a document "
                    "association that the ordinary upload policy would reject"
                ),
            }
            document["action"] = "refused"
            document["refusals"].append(refusal)
            refusals.append(
                {
                    "scope": "document",
                    "application_ref": application_ref,
                    "document_id": document_id,
                    **refusal,
                }
            )
            continue
        slot_refusals, notes = _slot_reference_finding(
            _clean(slot_key),
            person_ref,
            normalized_doc_type,
            resolved_party,
            reference_index,
        )
        document["notes"].extend(notes)
        if slot_refusals:
            document["action"] = "refused"
            document["refusals"].extend(slot_refusals)
            for refusal in slot_refusals:
                refusals.append(
                    {
                        "scope": "document",
                        "application_ref": application_ref,
                        "document_id": document_id,
                        **refusal,
                    }
                )
            continue

        canonical_slot = (
            f"person:{person_type}:{party_id}:{normalized_doc_type}"
        )
        document["proposed"] = {
            "person_id": party_id,
            "person_type": person_type,
            "slot_key": canonical_slot,
        }
        if (
            raw_person_id != party_id
            or raw_person_type != person_type
            or slot_key != canonical_slot
        ):
            document["action"] = "repair"

    current_slots: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for document in documents:
        proposed = document.get("proposed")
        if proposed and document["is_current"]:
            current_slots[proposed["slot_key"]].append(document)

    for slot_key, slot_documents in sorted(current_slots.items()):
        if len(slot_documents) < 2:
            continue
        document_ids = sorted(doc["document_id"] for doc in slot_documents)
        refusal = {
            "scope": "document",
            "application_ref": application_ref,
            "code": "duplicate_projected_current_slot",
            "slot_key": slot_key,
            "document_ids": document_ids,
        }
        refusals.append(refusal)
        for document in slot_documents:
            document["action"] = "refused"
            document["refusals"].append(
                {
                    "code": "duplicate_projected_current_slot",
                    "slot_key": slot_key,
                    "document_ids": document_ids,
                }
            )

    return documents, refusals


def _diagnose_application(db: Any, app: Dict[str, Any]) -> Dict[str, Any]:
    application_id = _clean(app.get("id"))
    application_ref = _clean(app.get("ref"))
    fixture, fixture_refusals = _fixture_diagnostic(app)
    parties, reference_index, party_refusals = _load_parties(db, application_id)
    for refusal in party_refusals:
        refusal.setdefault("application_ref", application_ref)
    documents, document_refusals = _load_documents(
        db,
        application_id,
        application_ref,
        reference_index,
    )
    refusals = fixture_refusals + party_refusals + document_refusals
    change_count = sum(
        1 for document in documents if document["action"] == "repair"
    )
    if fixture.get("change"):
        change_count += 1
    return {
        "application_ref": application_ref,
        "application_id": application_id,
        "company_name": app.get("company_name"),
        "status": app.get("status"),
        "fixture": fixture,
        "parties": parties,
        "documents": documents,
        "refusals": refusals,
        "change_count": change_count,
        "outcome": "refused" if refusals else ("ready" if change_count else "no_changes"),
    }


def _base_report(refs: Sequence[str], *, mode: str) -> Dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "mode": mode,
        "application_refs": list(refs),
        "outcome": "no_changes",
        "summary": {
            "requested_applications": len(refs),
            "resolved_applications": 0,
            "party_rows": 0,
            "document_rows": 0,
            "application_changes": 0,
            "document_changes": 0,
            "refusal_count": 0,
        },
        "applications": [],
        "refusals": [],
    }


def diagnose_repair(
    db: Any,
    application_refs: Iterable[str],
    *,
    require_audit_schema: bool = False,
    schema_already_validated: bool = False,
) -> Dict[str, Any]:
    """Return a read-only, row-level repair report for explicit refs."""
    refs = normalize_application_refs(application_refs)
    report = _base_report(refs, mode="dry_run")
    if not schema_already_validated:
        report["refusals"].extend(
            validate_schema(db, require_audit=require_audit_schema)
        )
        if report["refusals"]:
            report["summary"]["refusal_count"] = len(report["refusals"])
            report["outcome"] = "refused"
            return report

    placeholders = ",".join("?" for _ in refs)
    found = _rows(
        db.execute(
            f"""
            SELECT id, ref, company_name, status, is_fixture
            FROM applications
            WHERE ref IN ({placeholders})
            ORDER BY ref, id
            """,
            tuple(refs),
        )
    )
    by_ref: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for app in found:
        by_ref[_clean(app.get("ref"))].append(app)

    for ref in refs:
        matches = by_ref.get(ref, [])
        if not matches:
            refusal = {
                "scope": "application",
                "code": "application_ref_not_found",
                "application_ref": ref,
            }
            report["refusals"].append(refusal)
            report["applications"].append(
                {
                    "application_ref": ref,
                    "outcome": "refused",
                    "refusals": [refusal],
                }
            )
            continue
        if len(matches) > 1:
            refusal = {
                "scope": "application",
                "code": "duplicate_application_ref",
                "application_ref": ref,
                "application_ids": sorted(_clean(app.get("id")) for app in matches),
            }
            report["refusals"].append(refusal)
            report["applications"].append(
                {
                    "application_ref": ref,
                    "outcome": "refused",
                    "refusals": [refusal],
                }
            )
            continue

        application_report = _diagnose_application(db, matches[0])
        report["applications"].append(application_report)
        report["refusals"].extend(application_report["refusals"])

    resolved = [
        app for app in report["applications"] if app.get("application_id")
    ]
    report["summary"].update(
        {
            "resolved_applications": len(resolved),
            "party_rows": sum(len(app.get("parties", [])) for app in resolved),
            "document_rows": sum(len(app.get("documents", [])) for app in resolved),
            "application_changes": sum(
                1
                for app in resolved
                if app.get("fixture", {}).get("change")
            ),
            "document_changes": sum(
                1
                for app in resolved
                for document in app.get("documents", [])
                if document.get("action") == "repair"
            ),
            "refusal_count": len(report["refusals"]),
        }
    )
    change_count = (
        report["summary"]["application_changes"]
        + report["summary"]["document_changes"]
    )
    report["outcome"] = (
        "refused"
        if report["refusals"]
        else ("ready" if change_count else "no_changes")
    )
    return report


def _assert_apply_guard(
    *,
    environment: Optional[str],
    confirmation: Optional[str],
    database_identity: Optional[str],
) -> None:
    _assert_not_production(
        environment=environment,
        database_identity=database_identity,
    )
    if _clean(environment).lower() != "staging":
        raise RepairSafetyError("apply requires ENVIRONMENT=staging")
    if confirmation != APPLY_CONFIRMATION:
        raise RepairSafetyError(
            f"apply requires --confirm {APPLY_CONFIRMATION}"
        )
    _assert_no_ambient_libpq_target_overrides()


def _assert_no_ambient_libpq_target_overrides() -> None:
    """Refuse libpq environment defaults that can redirect the connection.

    libpq applies environment defaults even when a URI appears to identify a
    staging authority. In particular, ``PGHOSTADDR`` can redirect the network
    endpoint while leaving the URI hostname visible in connection metadata.
    Apply must therefore start from an explicit, self-contained URI.
    """
    present = sorted(
        name
        for name in TARGET_AFFECTING_LIBPQ_ENVIRONMENT
        if _clean(os.environ.get(name))
    )
    if present:
        raise RepairSafetyError(
            "apply refuses target-affecting libpq environment variables: "
            + ", ".join(present)
        )


def _assert_staging_preconnect_identity(
    *,
    environment: Optional[str],
    database_identity: Optional[str],
    expected_fingerprint: Optional[str],
) -> None:
    """Refuse a CLI database connection until its staging URI is approved."""
    _assert_not_production(
        environment=environment,
        database_identity=database_identity,
    )
    if _clean(environment).lower() != "staging":
        raise RepairSafetyError("database access requires ENVIRONMENT=staging")
    _assert_no_ambient_libpq_target_overrides()
    expected = _clean(expected_fingerprint).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RepairSafetyError(
            f"database access requires a pre-approved {STAGING_DATABASE_FINGERPRINT_ENV}"
        )
    actual = database_identity_fingerprint(database_identity)
    if not hmac.compare_digest(actual, expected):
        raise RepairSafetyError(
            "database URI does not match the pre-approved staging fingerprint"
        )


def _canonical_postgres_database_identity(database_identity: Optional[str]) -> str:
    """Return a credential-free, stable identity for an explicit PostgreSQL DSN."""
    identity = _clean(database_identity)
    try:
        parsed = urlsplit(identity)
        port = parsed.port
        query_parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as exc:
        raise RepairSafetyError("apply requires a valid PostgreSQL database identity") from exc
    if parsed.scheme.lower() not in ("postgres", "postgresql"):
        raise RepairSafetyError("apply requires an explicit PostgreSQL database identity")
    if parsed.fragment:
        raise RepairSafetyError("PostgreSQL database identity must not contain a fragment")
    unsafe_parameters = sorted(
        {
            key
            for raw_key, _value in query_parameters
            for key in [unquote(raw_key).strip().lower()]
            if key not in SAFE_POSTGRES_DSN_QUERY_PARAMETERS
        }
    )
    if unsafe_parameters:
        raise RepairSafetyError(
            "PostgreSQL database identity contains target-affecting or unsupported "
            f"query parameters: {', '.join(unsafe_parameters)}"
        )
    hostname = unquote(parsed.hostname or "").strip().lower().rstrip(".")
    database_name = unquote((parsed.path or "").lstrip("/")).strip()
    if (
        not hostname
        or "," in hostname
        or any(char.isspace() for char in hostname)
        or not database_name
        or "/" in database_name
        or any(ord(char) < 32 or ord(char) == 127 for char in database_name)
    ):
        raise RepairSafetyError("apply requires a complete PostgreSQL host and database name")
    return f"postgresql://{hostname}:{port or 5432}/{database_name}"


def database_identity_fingerprint(database_identity: Optional[str]) -> str:
    """Hash a credential-free PostgreSQL host/port/database identity."""
    canonical = _canonical_postgres_database_identity(database_identity)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_staging_database_identity(
    db: Any,
    *,
    database_identity: Optional[str],
    expected_fingerprint: Optional[str],
) -> None:
    """Require a positive, pre-approved staging endpoint fingerprint.

    A negative ``prod`` substring check is not sufficient because managed
    database endpoints can be opaque.  Apply therefore requires both an actual
    PostgreSQL connection and a fingerprint independently recorded for the
    staging host/port/database tuple.  The connected database name is also
    checked against the DSN so a caller cannot supply an unrelated label.
    """
    if not getattr(db, "is_postgres", False):
        raise RepairSafetyError("apply is restricted to the PostgreSQL staging database")
    expected = _clean(expected_fingerprint).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RepairSafetyError(
            f"apply requires a pre-approved {STAGING_DATABASE_FINGERPRINT_ENV}"
        )

    canonical = _canonical_postgres_database_identity(database_identity)
    parsed_canonical = urlsplit(canonical)
    expected_host = (parsed_canonical.hostname or "").lower().rstrip(".")
    expected_port = str(parsed_canonical.port or 5432)
    expected_database_name = unquote(parsed_canonical.path.lstrip("/"))

    raw_connection = getattr(db, "conn", None)
    dsn_parameters_getter = getattr(raw_connection, "get_dsn_parameters", None)
    if not callable(dsn_parameters_getter):
        raise RepairSafetyError(
            "apply could not verify the effective PostgreSQL connection target"
        )
    effective = dsn_parameters_getter() or {}
    effective_host = _clean(effective.get("host")).lower().rstrip(".")
    effective_hostaddr = _clean(effective.get("hostaddr"))
    effective_port = _clean(effective.get("port") or "5432")
    effective_database_name = _clean(
        effective.get("dbname") or effective.get("database")
    )
    if effective_hostaddr:
        raise RepairSafetyError(
            "apply refuses an effective PostgreSQL hostaddr override"
        )
    if (
        effective_host != expected_host
        or effective_port != expected_port
        or effective_database_name != expected_database_name
    ):
        raise RepairSafetyError(
            "effective PostgreSQL host, port, or database does not match "
            "the fingerprinted staging identity"
        )

    row = db.execute(
        "SELECT current_database() AS database_name"
    ).fetchone()
    connected_database_name = _clean(
        row.get("database_name") if isinstance(row, dict) else row["database_name"]
    )
    if connected_database_name != expected_database_name:
        raise RepairSafetyError(
            "connected database name does not match the supplied database identity"
        )

    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise RepairSafetyError(
            "connected database does not match the pre-approved staging fingerprint"
        )


def _assert_not_production(
    *,
    environment: Optional[str],
    database_identity: Optional[str],
) -> None:
    if _clean(environment).lower() in ("prod", "production"):
        raise RepairSafetyError("production access is forbidden for this incident tool")
    identity = _clean(database_identity).lower()
    if identity and re.search(
        r"(^|[/_.:@?-])(prod|production)(?=$|[/_.:@?-])",
        identity,
    ):
        raise RepairSafetyError(
            "database identity appears production-like; apply is forbidden"
        )


def _lock_apply_scope(db: Any, refs: Sequence[str]) -> None:
    placeholders = ",".join("?" for _ in refs)
    if getattr(db, "is_postgres", False):
        apps = _rows(
            db.execute(
                f"""
                SELECT id
                FROM applications
                WHERE ref IN ({placeholders})
                ORDER BY ref
                FOR UPDATE
                """,
                tuple(refs),
            )
        )
        application_ids = [app["id"] for app in apps]
        if not application_ids:
            return
        id_placeholders = ",".join("?" for _ in application_ids)
        for _person_type, table in PARTY_TABLES:
            _rows(
                db.execute(
                    f"""
                    SELECT id
                    FROM {table}
                    WHERE application_id IN ({id_placeholders})
                    ORDER BY application_id, id
                    FOR UPDATE
                    """,
                    tuple(application_ids),
                )
            )
        _rows(
            db.execute(
                f"""
                SELECT id
                FROM documents
                WHERE application_id IN ({id_placeholders})
                ORDER BY application_id, id
                FOR UPDATE
                """,
                tuple(application_ids),
            )
        )
        return

    db.execute("BEGIN IMMEDIATE")


def _rowcount(db: Any, result: Any) -> int:
    value = getattr(result, "rowcount", None)
    if value is None and getattr(db, "_cursor", None) is not None:
        value = getattr(db._cursor, "rowcount", None)
    return -1 if value is None else int(value)


def _apply_report(db: Any, report: Dict[str, Any]) -> None:
    from db import append_audit_log

    for app in report["applications"]:
        if not app.get("application_id"):
            continue
        application_id = app["application_id"]
        application_ref = app["application_ref"]
        fixture_change = app.get("fixture", {}).get("change")
        applied_documents = []
        before_documents = []
        after_documents = []

        if fixture_change:
            result = db.execute(
                """
                UPDATE applications
                SET is_fixture = ?
                WHERE id = ? AND is_fixture = ?
                """,
                (
                    fixture_change["to"],
                    application_id,
                    fixture_change["from"],
                ),
            )
            if _rowcount(db, result) != 1:
                raise RepairSafetyError(
                    f"concurrent fixture-marker change detected for {application_ref}"
                )
            fixture_change["action"] = "applied"

        for document in app.get("documents", []):
            if document.get("action") != "repair":
                continue
            current = document["current"]
            proposed = document["proposed"]
            result = db.execute(
                """
                UPDATE documents
                SET person_id = ?, person_type = ?, slot_key = ?
                WHERE id = ? AND application_id = ?
                  AND COALESCE(person_id, '') = ?
                  AND COALESCE(person_type, '') = ?
                  AND COALESCE(slot_key, '') = ?
                """,
                (
                    proposed["person_id"],
                    proposed["person_type"],
                    proposed["slot_key"],
                    document["document_id"],
                    application_id,
                    str(current.get("person_id") or ""),
                    str(current.get("person_type") or ""),
                    str(current.get("slot_key") or ""),
                ),
            )
            if _rowcount(db, result) != 1:
                raise RepairSafetyError(
                    f"concurrent document change detected for {document['document_id']}"
                )
            document["action"] = "applied"
            applied_documents.append(document["document_id"])
            before_documents.append(
                {
                    "document_id": document["document_id"],
                    "person_id": current.get("person_id"),
                    "person_type": current.get("person_type"),
                    "slot_key": current.get("slot_key"),
                }
            )
            after_documents.append(
                {
                    "document_id": document["document_id"],
                    "person_id": proposed["person_id"],
                    "person_type": proposed["person_type"],
                    "slot_key": proposed["slot_key"],
                }
            )

        if fixture_change or applied_documents:
            detail = json.dumps(
                {
                    "tool": "repair_kyc_party_document_links",
                    "report_version": REPORT_VERSION,
                    "application_ref": application_ref,
                    "fixture_marker_changed": bool(fixture_change),
                    "document_ids": applied_documents,
                    "document_count": len(applied_documents),
                },
                sort_keys=True,
            )
            entry_hash = append_audit_log(
                db,
                user_id="system:staging-kyc-link-repair",
                user_name="Staging KYC Party/Document Repair",
                user_role="system",
                action="KYC Party Document Link Repair",
                target=f"application:{application_ref}",
                application_id=application_id,
                detail=detail,
                before_state={
                    "is_fixture": fixture_change["from"] if fixture_change else None,
                    "documents": before_documents,
                },
                after_state={
                    "is_fixture": fixture_change["to"] if fixture_change else None,
                    "documents": after_documents,
                },
                commit=False,
            )
            app["audit_entry_hash"] = entry_hash
            app["outcome"] = "applied"


def run_repair(
    db: Any,
    application_refs: Iterable[str],
    *,
    apply: bool = False,
    confirmation: Optional[str] = None,
    environment: Optional[str] = None,
    database_identity: Optional[str] = None,
    expected_database_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """Diagnose or atomically apply the repair for an explicit ref set."""
    refs = normalize_application_refs(application_refs)
    _assert_not_production(
        environment=environment,
        database_identity=database_identity,
    )
    if not apply:
        return diagnose_repair(db, refs)

    _assert_apply_guard(
        environment=environment,
        confirmation=confirmation,
        database_identity=database_identity,
    )
    _assert_staging_database_identity(
        db,
        database_identity=database_identity,
        expected_fingerprint=expected_database_fingerprint,
    )
    schema_refusals = validate_schema(db, require_audit=True)
    if schema_refusals:
        report = _base_report(refs, mode="apply")
        report["refusals"] = schema_refusals
        report["summary"]["refusal_count"] = len(schema_refusals)
        report["outcome"] = "refused"
        return report

    try:
        _lock_apply_scope(db, refs)
        report = diagnose_repair(
            db,
            refs,
            require_audit_schema=True,
            schema_already_validated=True,
        )
        report["mode"] = "apply"
        if report["refusals"]:
            db.rollback()
            return report
        _apply_report(db, report)
        db.commit()
        applied_count = (
            report["summary"]["application_changes"]
            + report["summary"]["document_changes"]
        )
        report["summary"]["applied_changes"] = applied_count
        report["outcome"] = "applied" if applied_count else "no_changes"
        return report
    except Exception:
        db.rollback()
        raise


def _database_identity(db: Any) -> Optional[str]:
    identity = getattr(db, "database_identity", None)
    if identity:
        return str(identity)
    return os.environ.get("DATABASE_URL")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit read-only mode (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply the exact non-ambiguous plan atomically in staging.",
    )
    parser.add_argument(
        "--application-ref",
        action="append",
        required=True,
        dest="application_refs",
        help="Exact application ref; repeat or provide a comma-separated list.",
    )
    parser.add_argument(
        "--confirm",
        help=f"Apply confirmation phrase: {APPLY_CONFIRMATION}",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args(argv)

    if not args.apply and args.confirm:
        parser.error("--confirm is only valid with --apply")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    try:
        refs = normalize_application_refs(args.application_refs)
        _assert_staging_preconnect_identity(
            environment=os.environ.get("ENVIRONMENT"),
            database_identity=os.environ.get("DATABASE_URL"),
            expected_fingerprint=os.environ.get(
                STAGING_DATABASE_FINGERPRINT_ENV
            ),
        )
        if args.apply:
            _assert_apply_guard(
                environment=os.environ.get("ENVIRONMENT"),
                confirmation=args.confirm,
                database_identity=os.environ.get("DATABASE_URL"),
            )
    except RepairSafetyError as exc:
        parser.error(str(exc))

    from db import get_db  # Imported after CLI guards; avoids accidental connect.

    db = get_db()
    try:
        report = run_repair(
            db,
            refs,
            apply=args.apply,
            confirmation=args.confirm,
            environment=os.environ.get("ENVIRONMENT"),
            database_identity=_database_identity(db),
            expected_database_fingerprint=os.environ.get(
                STAGING_DATABASE_FINGERPRINT_ENV
            ),
        )
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 2 if report["outcome"] == "refused" else 0
    except RepairSafetyError as exc:
        LOGGER.error("%s", exc)
        print(
            json.dumps(
                {
                    "report_version": REPORT_VERSION,
                    "mode": "apply" if args.apply else "dry_run",
                    "outcome": "aborted",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

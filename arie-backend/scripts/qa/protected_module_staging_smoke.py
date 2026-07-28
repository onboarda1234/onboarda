#!/usr/bin/env python3
"""Authenticated, read-only semantic smoke for protected staging modules.

Authentication is the only POST. Every protected resource request is GET-only,
and the emitted semantic hashes omit timestamps and naturally variable actor
metadata. Credential values and the bearer token are never written or printed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


CANONICAL_REFS = (
    "RM-PILOT-001",
    "RM-PILOT-024",
    "RM-PILOT-028",
    "RM-PILOT-039",
    "RM-PILOT-041",
)
SCREENING_REFS = (
    "ARF-QAFIX-001",
    "ARF-QAFIX-002",
    "ARF-QAFIX-004",
    "ARF-QAFIX-005",
    "ARF-QAFIX-006",
)
NON_CLEAR_SCREENING_REFS = {
    "ARF-QAFIX-001",
    "ARF-QAFIX-004",
    "ARF-QAFIX-005",
}
STAGING_CM_FIXTURE_APPLICATION_ID = "qafix-confirmed-match-817"


def semantic_hash(value) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def content_semantic_hash(value) -> str:
    """Hash memo/supervisor content after removing actor/time metadata."""

    def stable(item):
        if isinstance(item, dict):
            return {
                key: stable(child)
                for key, child in sorted(item.items())
                if key != "id"
                and not key.endswith(
                    (
                        "_at",
                        "_timestamp",
                        "_by",
                        "_by_name",
                        "_actor_id",
                        "_officer_id",
                        "_reviewer_id",
                    )
                )
                and key != "timestamp"
            }
        if isinstance(item, list):
            return [stable(child) for child in item]
        return item

    return semantic_hash(stable(value))


def unwrap(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


class StagingClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/") + "/api"
        self.email = email
        self.password = password
        self.token = ""

    def _request(self, path: str, *, method: str = "GET", data=None):
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(
                f"{method} {path} returned HTTP {exc.code}"
            ) from exc

    def authenticate(self):
        payload = unwrap(
            self._request(
                "/auth/officer/login",
                method="POST",
                data={"email": self.email, "password": self.password},
            )
        )
        self.token = payload.get("token") or payload.get("access_token") or ""
        if not self.token:
            raise RuntimeError("Staging login did not return an access token")
        return {
            "role": (payload.get("user") or {}).get("role"),
            "method": "approved_officer_ui_credentials",
        }

    def get(self, path: str):
        return unwrap(self._request(path))


def document_subset(document: dict) -> dict:
    return {
        key: document.get(key)
        for key in (
            "id",
            "doc_type",
            "slot_key",
            "person_id",
            "person_type",
            "is_current",
            "version",
            "review_status",
            "verification_status",
            "verification_state",
            "verification_success",
            "verification_terminal",
        )
    }


def risk_evidence_subset(evidence: dict) -> dict:
    evidence = evidence if isinstance(evidence, dict) else {}
    computation = evidence.get("computation_evidence") or {}
    return {
        "available": evidence.get("available"),
        "authoritative": evidence.get("authoritative"),
        "read_only": evidence.get("read_only"),
        "status": evidence.get("status"),
        "source": evidence.get("source"),
        "config_version": evidence.get("config_version"),
        "application": evidence.get("application"),
        "computation_evidence": {
            "schema_version": computation.get("schema_version"),
            "base_composite_score": computation.get("base_composite_score"),
            "policy_adjustment": computation.get("policy_adjustment"),
            "final_composite_score": computation.get("final_composite_score"),
            "dimensions": [
                {
                    key: row.get(key)
                    for key in (
                        "dimension_id",
                        "dimension_score",
                        "dimension_weight",
                        "composite_contribution",
                        "rounding_adjustment",
                    )
                }
                for row in computation.get("dimensions") or []
            ],
        },
    }


def risk_dimensions_subset(dimensions: dict) -> dict:
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    mappings = dimensions.get("controlled_mapping_evidence") or []
    services = dimensions.get("service_selection_evidence") or {}
    return {
        key: dimensions.get(key) for key in ("d1", "d2", "d3", "d4", "d5")
    } | {
        "controlled_mapping_count": len(mappings),
        "controlled_mapping_statuses": sorted(
            {str(row.get("resolution_status") or "") for row in mappings}
        ),
        "service_selection": {
            key: services.get(key)
            for key in (
                "resolution_status",
                "selected_max_score",
                "maximum_enforced",
                "selection_count",
                "unique_selection_count",
                "order_independent",
            )
        },
    }


def application_subset(application: dict) -> dict:
    documents = sorted(
        (document_subset(item) for item in application.get("documents") or []),
        key=lambda item: str(item.get("id") or ""),
    )
    risk_evidence = risk_evidence_subset(application.get("risk_report_evidence") or {})
    latest_memo = application.get("latest_memo") or {}
    latest_memo_data = application.get("latest_memo_data") or {}
    gate = application.get("approval_gate_presentation") or {}
    diagnostics = application.get("current_gate_diagnostics") or {}
    document_gate = application.get("document_evidence_gate") or {}
    idv_gate = application.get("idv_gate_summary") or {}
    screening_truth = application.get("screening_truth_summary") or {}
    memo_screening = application.get("memo_screening_current_snapshot") or {}
    subset = {
        key: application.get(key)
        for key in (
            "id",
            "ref",
            "is_fixture",
            "status",
            "status_label",
            "risk_score",
            "risk_level",
            "base_risk_level",
            "final_risk_level",
            "onboarding_lane",
            "approval_route",
            "risk_config_version",
            "risk_escalations",
            "elevation_reason_text",
            "has_authoritative_risk",
            "memo_is_stale",
            "memo_requires_regeneration",
            "supervisor_requires_rerun",
            "gate_blocker_count",
        )
    }
    subset.update(
        {
            "document_count": len(documents),
            "documents": documents,
            "screening_review_count": len(application.get("screening_reviews") or []),
            "monitoring_alert_count": len(application.get("monitoring_alerts") or []),
            "periodic_review_count": len(application.get("periodic_reviews") or []),
            "risk_dimensions": risk_dimensions_subset(
                application.get("risk_dimensions") or {}
            ),
            "risk_evidence": risk_evidence,
            "decision_eligibility": {
                "approval_route": application.get("approval_route") or {},
                "approval_gate": {
                    key: gate.get(key)
                    for key in (
                        "mode",
                        "record_classification",
                        "terminal_status",
                        "is_terminal",
                        "current_gate_has_blockers",
                        "current_gate_blocker_count",
                        "current_gate_diagnostics_label",
                        "decision_basis_available",
                        "decision_time_gate_snapshot_available",
                        "legacy_evidence_incomplete",
                        "officer_guidance",
                    )
                },
                "current_gate_diagnostics": {
                    key: diagnostics.get(key)
                    for key in (
                        "applies_to",
                        "blocking",
                        "blocker_count",
                        "label",
                    )
                }
                | {
                    "blockers_semantic_sha256": content_semantic_hash(
                        diagnostics.get("blockers") or application.get("gate_blockers") or []
                    )
                },
                "decision_basis": {
                    key: (application.get("decision_basis") or {}).get(key)
                    for key in (
                        "available",
                        "source",
                        "decision_type",
                        "risk_level",
                        "approval_gate_snapshot_available",
                        "evidence_warning",
                        "key_flags",
                    )
                },
                "pre_approval_decision": application.get("pre_approval_decision"),
                "submission_basis": application.get("submission_basis"),
                "submission_kind": application.get("submission_kind"),
            },
            "kyc_gates": {
                "document_evidence": {
                    key: document_gate.get(key)
                    for key in (
                        "policy_version",
                        "stage",
                        "passed",
                        "approval_ready",
                        "reliance_status",
                        "required_count",
                        "satisfied_required_count",
                        "uploaded_count",
                        "blocker_count",
                        "allowed_states",
                        "blocked_states",
                        "manual_acceptance_policy",
                        "require_agent_execution",
                    )
                }
                | {
                    "blockers_semantic_sha256": content_semantic_hash(
                        document_gate.get("blockers") or []
                    )
                },
                "idv": {
                    key: idv_gate.get(key)
                    for key in (
                        "required",
                        "approval_ready",
                        "blocking_count",
                        "blocking_reasons",
                        "status_counts",
                    )
                },
            },
            "screening_gate": {
                key: screening_truth.get(key)
                for key in (
                    "canonical_state",
                    "defensible_clear",
                    "approval_ready",
                    "approval_blocking",
                    "screening_terminal",
                    "provider_availability",
                    "provider_mode",
                    "required_evidence",
                    "blocking_reasons",
                )
            },
            "memo": {
                key: latest_memo.get(key)
                for key in (
                    "version",
                    "memo_version",
                    "review_status",
                    "validation_status",
                    "final_status",
                    "blocked",
                    "block_reason",
                    "is_stale",
                    "stale_reason",
                    "stale_reasons",
                    "stale_trigger",
                    "quality_score",
                )
            }
            | {
                "content_semantic_sha256": content_semantic_hash(
                    {
                        key: latest_memo_data.get(key)
                        for key in (
                            "body",
                            "sections",
                            "workflow_state",
                            "risk",
                            "scenario_evidence",
                        )
                    }
                ),
                "supervisor_semantic_sha256": content_semantic_hash(
                    {
                        "supervisor": latest_memo_data.get("supervisor"),
                        "supervisor_evidence": latest_memo_data.get(
                            "supervisor_evidence"
                        ),
                    }
                ),
            },
            "memo_screening_snapshot": {
                key: memo_screening.get(key)
                for key in (
                    "canonical_state",
                    "screening_result",
                    "approval_blocking",
                    "evidence_quality",
                    "adverse_media_evidence_quality",
                    "current_risk_count",
                    "historical_risk_count",
                    "stale_risk_count",
                    "current_unresolved_risk_count",
                    "subject_count",
                    "source",
                )
            },
            "agent3_advisory_semantic_sha256": content_semantic_hash(
                application.get("agent3_screening_interpretation")
            ),
        }
    )
    subset["semantic_sha256"] = semantic_hash(subset)
    return subset


def screening_subset(row: dict) -> dict:
    evidence = row.get("screening_evidence") or {}
    evidence_items = [
        {
            key: item.get(key)
            for key in (
                "hit_id",
                "match_id",
                "provider",
                "provider_alert_id",
                "provider_case_id",
                "provider_customer_id",
                "provider_match_id",
                "provider_profile_id",
                "provider_risk_id",
                "provider_workflow_id",
                "category",
                "evidence_status",
                "evidence_quality",
                "provider_decision",
                "provider_status",
                "record_status",
                "risk_status",
                "relationship_to_application",
                "next_action",
                "officer_disposition",
                "review_history_summary",
                "source_name",
                "source_title",
                "source_url_available",
            )
        }
        for item in evidence.get("items") or []
    ]
    evidence_items.sort(
        key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), default=str
        )
    )
    provider_references = evidence.get("provider_references") or {}
    subset = {
        "application_id": row.get("application_id"),
        "application_ref": row.get("application_ref"),
        "subject_type": row.get("subject_type"),
        "axes": {
            "execution": {
                key: row.get(key)
                for key in (
                    "screening_state",
                    "screening_truth_state",
                    "screening_result",
                    "provider_status",
                    "provider_availability",
                    "provider_mode",
                    "screening_mode",
                    "terminal",
                    "is_terminal",
                )
            },
            "adjudication": {
                key: row.get(key)
                for key in (
                    "canonical_disposition",
                    "canonical_status",
                    "status_key",
                    "status_label",
                    "officer_review_status",
                    "review_disposition",
                    "review_disposition_code",
                    "review_resolved",
                    "defensible_clear",
                    "review_required",
                    "review_actionable",
                    "requires_four_eyes",
                    "review_four_eyes_status",
                    "second_disposition_code",
                )
            }
            | {
                "review_evidence_semantic_sha256": content_semantic_hash(
                    {
                        "reference": row.get("review_evidence_reference"),
                        "documents": row.get("review_evidence_documents"),
                        "rationale": row.get("review_rationale"),
                        "second_rationale": row.get("second_rationale"),
                    }
                )
            },
            "provenance": {
                "providers": evidence.get("providers") or [],
                "provider_references": {
                    key: provider_references.get(key)
                    for key in (
                        "provider",
                        "alert_ids",
                        "case_ids",
                        "customer_ids",
                        "profile_ids",
                        "risk_ids",
                        "workflow_ids",
                    )
                },
                "evidence_status": evidence.get("evidence_status"),
                "evidence_quality": row.get("evidence_quality"),
                "evidence_quality_label": row.get("evidence_quality_label"),
                "evidence_count": evidence.get("evidence_count"),
                "evidence_capped": row.get("evidence_capped"),
                "current_risk_count": row.get("current_risk_count"),
                "current_unresolved_risk_count": row.get(
                    "current_unresolved_risk_count"
                ),
                "historical_risk_count": row.get("historical_risk_count"),
                "stale_risk_count": row.get("stale_risk_count"),
                "risk_category_counts": row.get("risk_category_counts") or {},
                "evidence_item_count": len(evidence_items),
                "evidence_items_semantic_sha256": semantic_hash(evidence_items),
                "triage_semantic_sha256": content_semantic_hash(
                    row.get("triage") or {}
                ),
            },
        },
        "total_hits": row.get("total_hits"),
        "state_integrity_flags": row.get("state_integrity_flags"),
    }
    subset["semantic_sha256"] = semantic_hash(subset)
    return subset


def risk_model_subset(payload: dict) -> dict:
    model = payload.get("runtime_model") if isinstance(payload, dict) else {}
    model = model if isinstance(model, dict) else {}
    source = model.get("runtime_source") or {}
    subset = {
        "read_only": model.get("read_only"),
        "runtime_source": {
            key: source.get(key)
            for key in (
                "activation_flag",
                "activation_enabled",
                "config_loader",
                "scorer",
                "config_version",
                "parser_mode",
            )
        },
        "dimensions": [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "weight": row.get("weight"),
            }
            for row in model.get("dimensions") or []
        ],
        "thresholds": model.get("thresholds") or [],
        "edd_policy": model.get("edd_policy") or {},
        "approval_policy": model.get("approval_policy") or {},
        "monitoring_policy": model.get("monitoring_policy") or {},
    }
    subset["semantic_sha256"] = semantic_hash(subset)
    return subset


def edd_case_subset(row: dict) -> dict:
    subset = {
        key: row.get(key)
        for key in (
            "application_id",
            "application_ref",
            "stage",
            "risk_level",
            "risk_score",
            "trigger_source",
            "terminal_outcome",
        )
    }
    subset["semantic_sha256"] = semantic_hash(subset)
    return subset


def change_management_subset(row: dict) -> dict:
    obligations = sorted(
        (
            {
                key: obligation.get(key)
                for key in ("code", "label", "advisory")
            }
            for obligation in row.get("downstream_obligations") or []
        ),
        key=lambda obligation: str(obligation.get("code") or ""),
    )
    items = sorted(
        (
            {
                key: item.get(key)
                for key in (
                    "change_type",
                    "field_name",
                    "materiality",
                    "person_action",
                )
            }
            for item in row.get("preview_items") or []
        ),
        key=lambda item: (
            str(item.get("change_type") or ""),
            str(item.get("field_name") or ""),
        ),
    )
    subset = {
        key: row.get(key)
        for key in (
            "application_id",
            "application_ref",
            "is_fixture",
            "status",
            "materiality",
            "source",
            "source_channel",
            "changed_fields_count",
            "pre_change_risk_level",
            "post_change_risk_level",
            "risk_review_required",
            "screening_required",
            "edd_review_required",
            "decision_notes",
            "memo_addendum_hook",
            "periodic_review_acceleration_hook",
        )
    }
    subset.update(
        {
            "downstream_obligations": obligations,
            "items": items,
            "precondition_results_semantic_sha256": content_semantic_hash(
                row.get("precondition_results") or {}
            ),
        }
    )
    subset["semantic_sha256"] = semantic_hash(subset)
    return subset


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("STAGING_BASE_URL", "https://staging.regmind.co"),
    )
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Optional compact semantic baseline whose stable hashes must match.",
    )
    return parser.parse_args()


def baseline_comparison(result: dict, baseline_path: Path) -> dict[str, bool]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = baseline["expected_semantics"]
    checks = {
        "baseline_risk_model_matches": result["risk_model"]["semantic_sha256"]
        == expected["risk_model_semantic_sha256"],
        "baseline_screening_provider_matches": result["screening_provider"]
        == expected["screening_provider"],
        "baseline_environment_features_match": result["environment"]["features"]
        == expected["environment_features"],
        "baseline_change_management_matches": (
            result.get("change_management_fixture") or {}
        ).get("semantic_sha256")
        == expected["change_management_semantic_sha256"],
        "baseline_fixture_visibility_matches": {
            key: result["visibility"].get(key)
            for key in expected["fixture_visibility"]
        }
        == expected["fixture_visibility"],
    }
    for reference, expected_hash in expected["applications"].items():
        checks["baseline_application_" + reference.lower().replace("-", "_")] = (
            result["applications"].get(reference, {}).get("semantic_sha256")
            == expected_hash
        )
    for reference, expected_hash in expected["screening"].items():
        checks["baseline_screening_" + reference.lower().replace("-", "_")] = (
            result["screening"].get(reference, {}).get("semantic_sha256")
            == expected_hash
        )
    for reference, expected_hash in expected["edd_cases"].items():
        checks["baseline_edd_" + reference.lower().replace("-", "_")] = (
            result["edd_cases"].get(reference, {}).get("semantic_sha256")
            == expected_hash
        )
    return checks


def run(args) -> dict:
    email = os.environ.get("STAGING_QA_EMAIL", "")
    password = os.environ.get("STAGING_QA_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            "STAGING_QA_EMAIL and STAGING_QA_PASSWORD are required; "
            "credential values are never emitted"
        )

    client = StagingClient(args.base_url, email, password)
    auth = client.authenticate()
    version = client.get("/version")
    environment = client.get("/config/environment")
    risk_model = risk_model_subset(client.get("/config/risk-model"))
    screening_status = client.get("/screening/status")

    default_apps = client.get(
        "/applications?" + urlencode({"limit": 100, "offset": 0, "q": "RM-PILOT-"})
    )
    fixture_apps = client.get(
        "/applications?"
        + urlencode(
            {
                "limit": 100,
                "offset": 0,
                "q": "RM-PILOT-",
                "show_fixtures": "true",
            }
        )
    )
    applications = {
        reference: application_subset(
            client.get(
                "/applications/"
                + quote(reference, safe="")
                + "?show_fixtures=true"
            )
        )
        for reference in CANONICAL_REFS
    }

    default_screening = client.get(
        "/screening/queue?"
        + urlencode({"limit": 100, "offset": 0, "search": "QAFIX"})
    )
    fixture_screening = client.get(
        "/screening/queue?"
        + urlencode(
            {
                "limit": 100,
                "offset": 0,
                "search": "QAFIX",
                "include_evidence": 1,
                "show_fixtures": "true",
            }
        )
    )
    all_screening_rows = fixture_screening.get("rows") or []
    screening_rows = {}
    for reference in SCREENING_REFS:
        candidates = [
            row
            for row in all_screening_rows
            if row.get("application_ref") == reference
        ]
        candidates.sort(
            key=lambda row: (
                row.get("subject_type") != "entity",
                str(row.get("subject_type") or ""),
                str(row.get("subject_name") or ""),
            )
        )
        if candidates:
            screening_rows[reference] = screening_subset(candidates[0])

    edd_payload = client.get(
        "/edd/cases?"
        + urlencode({"limit": 500, "offset": 0, "show_fixtures": "true"})
    )
    edd_cases = {}
    for row in edd_payload.get("cases") or []:
        reference = row.get("application_ref")
        if reference in {"RM-PILOT-024", "RM-PILOT-028"}:
            edd_cases[reference] = edd_case_subset(row)

    default_cm = client.get(
        "/change-management/requests?"
        + urlencode({"limit": 500, "offset": 0})
    )
    fixture_cm = client.get(
        "/change-management/requests?"
        + urlencode(
            {"limit": 500, "offset": 0, "show_fixtures": "true"}
        )
    )

    app_rows_default = default_apps.get("applications") or default_apps.get("items") or []
    app_rows_fixture = fixture_apps.get("applications") or fixture_apps.get("items") or []
    default_screening_rows = default_screening.get("rows") or []
    default_cm_rows = default_cm.get("requests") or []
    fixture_cm_rows = fixture_cm.get("requests") or []
    default_cm_application_ids = {
        row.get("application_id") for row in default_cm_rows
    }
    fixture_cm_application_ids = {
        row.get("application_id") for row in fixture_cm_rows
    }
    change_management_fixture = next(
        (
            change_management_subset(row)
            for row in fixture_cm_rows
            if row.get("application_id") == STAGING_CM_FIXTURE_APPLICATION_ID
        ),
        None,
    )
    checks = {
        "version_matches_expected_sha": (
            version.get("git_sha") == args.expected_git_sha
            and version.get("image_tag") == args.expected_git_sha
        ),
        "canonical_applications_hidden_by_default": len(app_rows_default) == 0,
        "canonical_applications_available_by_opt_in": all(
            reference
            in {
                row.get("ref") or row.get("application_ref")
                for row in app_rows_fixture
            }
            for reference in CANONICAL_REFS
        ),
        "canonical_applications_explicitly_marked": all(
            application.get("is_fixture") is True
            for application in applications.values()
        ),
        "screening_fixtures_hidden_by_default": len(default_screening_rows) == 0,
        "protected_screening_fixtures_available_by_opt_in": all(
            reference in screening_rows for reference in SCREENING_REFS
        ),
        "pending_failed_stale_never_clear": all(
            screening_rows.get(reference, {})
            .get("axes", {})
            .get("adjudication", {})
            .get("defensible_clear")
            is False
            and "clear"
            not in str(
                screening_rows.get(reference, {})
                .get("axes", {})
                .get("adjudication", {})
                .get("status_key")
                or ""
            ).lower()
            for reference in NON_CLEAR_SCREENING_REFS
        ),
        "four_eyes_fixture_remains_explicit": (
            screening_rows.get("ARF-QAFIX-002", {})
            .get("axes", {})
            .get("adjudication", {})
            .get("requires_four_eyes")
            is True
        ),
        "risk_model_read_only": risk_model.get("read_only") is True,
        "risk_model_dimensions_d1_d5": [
            row.get("id") for row in risk_model.get("dimensions") or []
        ]
        == ["D1", "D2", "D3", "D4", "D5"],
        "authoritative_risk_evidence_available": all(
            application.get("risk_evidence", {}).get("available") is True
            and application.get("risk_evidence", {}).get("authoritative") is True
            and application.get("risk_evidence", {}).get("read_only") is True
            for application in applications.values()
        ),
        "edd_cases_link_to_canonical_applications": set(edd_cases)
        == {"RM-PILOT-024", "RM-PILOT-028"},
        "change_management_fixture_hidden_by_default": (
            STAGING_CM_FIXTURE_APPLICATION_ID not in default_cm_application_ids
        ),
        "change_management_fixture_available_by_opt_in": (
            STAGING_CM_FIXTURE_APPLICATION_ID in fixture_cm_application_ids
        ),
        "change_management_fixture_semantics_available": (
            change_management_fixture is not None
        ),
        "monitoring_dashboard_flag_enabled": (
            (environment.get("features") or {}).get(
                "ENABLE_MONITORING_DASHBOARD"
            )
            is True
        ),
    }

    provider_truth = (
        screening_status.get("provider_truth")
        if isinstance(screening_status, dict)
        else {}
    ) or {}
    result = {
        "suite": "protected-module-staging-smoke",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "authentication": auth,
        "resource_request_policy": "GET-only after authentication",
        "version": {
            key: version.get(key)
            for key in ("git_sha", "image_tag", "environment", "service")
        },
        "environment": {
            "environment": environment.get("environment"),
            "features": environment.get("features") or {},
        },
        "screening_provider": {
            "active_aml_screening_provider": provider_truth.get(
                "active_aml_screening_provider"
            ),
            "screening_abstraction_enabled": provider_truth.get(
                "screening_abstraction_enabled"
            ),
            "simulation_fallback_mode": provider_truth.get(
                "simulation_fallback_mode"
            ),
            "rescreen_enabled": screening_status.get("rescreen_enabled"),
            "hydration_enabled": screening_status.get("hydration_enabled"),
        },
        "risk_model": risk_model,
        "visibility": {
            "default_application_fixture_count": len(app_rows_default),
            "opt_in_application_fixture_count": len(app_rows_fixture),
            "default_screening_fixture_count": len(default_screening_rows),
            "opt_in_screening_row_count": len(all_screening_rows),
            "default_change_request_count": len(default_cm_rows),
            "opt_in_change_request_count": len(fixture_cm_rows),
            "change_management_fixture_application_id": (
                STAGING_CM_FIXTURE_APPLICATION_ID
            ),
            "change_management_fixture_hidden_by_default": (
                STAGING_CM_FIXTURE_APPLICATION_ID
                not in default_cm_application_ids
            ),
            "change_management_fixture_available_by_opt_in": (
                STAGING_CM_FIXTURE_APPLICATION_ID
                in fixture_cm_application_ids
            ),
        },
        "applications": applications,
        "screening": screening_rows,
        "edd_cases": edd_cases,
        "change_management_fixture": change_management_fixture,
        "checks": checks,
    }
    result["semantic_sha256"] = semantic_hash(
        {
            key: value
            for key, value in result.items()
            if key not in {"captured_at", "semantic_sha256"}
        }
    )
    if args.baseline:
        result["checks"].update(baseline_comparison(result, args.baseline))
    return result


def main() -> int:
    args = parse_args()
    try:
        result = run(args)
    except Exception as exc:
        print(f"protected staging smoke failed: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"protected staging smoke evidence: {args.output}")
    else:
        print(payload, end="")
    failed = sorted(name for name, passed in result["checks"].items() if passed is not True)
    if failed:
        print("failed checks: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(
        "protected staging smoke passed: " + result["semantic_sha256"],
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

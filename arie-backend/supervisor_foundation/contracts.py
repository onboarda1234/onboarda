"""Closed vocabularies and version constants for the Supervisor foundation.

Every value here is part of the reproducibility contract. Changing one changes
review hashes, so each is a governed change requiring a version bump.
"""

from __future__ import annotations

from enum import Enum


# ── Version constants ────────────────────────────────────────────────
# Bumping any of these changes review_hash for every subject.

BUNDLE_SCHEMA_VERSION = "supervisor-bundle-v1"
REVIEW_SCHEMA_VERSION = "supervisor-review-v1"
POLICY_CONTRACT_VERSION = "policy-contract-v1"

# Phase 0B-1 ships no probes. The probe set version is part of review_hash so
# that adding the first real probe in 0B-2 produces a visibly different hash.
PROBE_SET_VERSION = "probe-set-0b1-empty"


class SubjectType(str, Enum):
    """Review subject. Phase 0B-1 supports ``application`` only.

    The other members are reserved by the Phase 0A framework so that later
    phases do not force a schema version bump. ``assemble_application_bundle``
    rejects anything other than ``APPLICATION``.
    """

    APPLICATION = "application"
    CUSTOMER = "customer"
    PERIODIC_REVIEW = "periodic_review"
    MONITORING_EVENT = "monitoring_event"
    CHANGE_REQUEST = "change_request"


class FindingStatus(str, Enum):
    """Outcome of evaluating a probe.

    ``unavailable`` and ``not_replayable`` are distinct from ``clear`` and must
    never be rendered, aggregated, or counted as ``clear``. A check that could
    not be evaluated is not a check that passed.
    """

    HIT = "hit"
    CLEAR = "clear"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    NOT_REPLAYABLE = "not_replayable"


class AvailabilityStatus(str, Enum):
    """Why a probe could or could not run.

    Attached alongside ``FindingStatus`` so the reason a check did not run
    survives into the output: an absent registry credential, a gated module and
    an unconfigured policy are three different operational problems with three
    different owners.
    """

    AVAILABLE = "available"
    DEPENDENCY_GATED = "dependency_gated"
    CREDENTIALS_ABSENT = "credentials_absent"
    DATA_ABSENT = "data_absent"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    POLICY_NOT_CONFIGURED = "policy_not_configured"


#: Statuses that must never be presented as a passing check.
NON_CLEAR_STATUSES = frozenset(
    {FindingStatus.UNAVAILABLE, FindingStatus.NOT_REPLAYABLE}
)

SUBJECT_TYPES = tuple(member.value for member in SubjectType)
FINDING_STATUSES = tuple(member.value for member in FindingStatus)
AVAILABILITY_STATUSES = tuple(member.value for member in AvailabilityStatus)


# ── Institution policy contract ──────────────────────────────────────
# Input contract only. Phase 0B-1 introduces no persistence, no administration,
# no approval workflow and no defaults. See §3.3.5 of the framework document.

#: The eight configuration keys the framework declares. Declaring a key here
#: does not configure it — an unset key is reported ``policy_not_configured``.
POLICY_KEYS = (
    "control_person_required",
    "factor_evidence_mapping",
    "factor_materiality_threshold",
    "manual_acceptance_roles",
    "ownership_exemption_types",
    "ownership_reconciliation_tolerance",
    "pep_evidence_requirements",
    "ubo_identification_threshold",
)


# ── Bundle section names ─────────────────────────────────────────────
#: Every section the canonical bundle carries, in canonical (sorted) order.
#: The assembler emits all of them on every run; a section with no data is an
#: explicit empty structure, never an omitted key.
BUNDLE_SECTIONS = (
    "application",
    "availability",
    "decision",
    "document_gate",
    "documents",
    "edd",
    "memo",
    "meta",
    "monitoring",
    "parties",
    "policy",
    "risk",
    "screening",
    "supervisor_verdict",
)


#: Fields a probe must supply for every finding. The runner rejects a draft
#: that omits any of them — a probe that cannot answer all five framework
#: questions is not ready to ship.
REQUIRED_FINDING_FIELDS = (
    "availability_status",
    "category",
    "claim",
    "close_condition",
    "confidence",
    "evidence_refs",
    "officer_question",
    "probe_id",
    "probe_version",
    "regulatory_or_policy_basis",
    "required_action",
    "severity",
    "source_modules",
    "status",
    "why_it_matters",
)

"""AI Compliance Supervisor — deterministic foundation (Phase 0B-1).

Read-only, advisory, isolated. This package assembles a canonical current-state
input bundle for a single application and produces stable hashes and stable
finding identifiers. It contains no probes, no persistence, no API, no UI.

Design constraints enforced by tests in ``tests/``:

* No module in this package calls a wall clock. ``as_of`` is injected by the
  caller and is the only permitted time source.
* Nothing in this package writes to any table.
* No authoritative module imports this package, so authoritative runtime
  behaviour cannot depend on it.

See ``docs/architecture/ai-compliance-supervisor-review-framework.md``.
"""

from .contracts import (
    AVAILABILITY_STATUSES,
    BUNDLE_SCHEMA_VERSION,
    FINDING_STATUSES,
    POLICY_CONTRACT_VERSION,
    POLICY_KEYS,
    PROBE_SET_VERSION,
    REVIEW_SCHEMA_VERSION,
    SUBJECT_TYPES,
    AvailabilityStatus,
    FindingStatus,
    SubjectType,
)
from .canonical_json import canonical_bytes, canonical_json
from .hashing import compute_finding_id, compute_input_bundle_hash, compute_review_hash
from .policy import PolicyProfile
from .bundle import assemble_application_bundle
from .review import run_review

__all__ = [
    "AVAILABILITY_STATUSES",
    "BUNDLE_SCHEMA_VERSION",
    "FINDING_STATUSES",
    "POLICY_CONTRACT_VERSION",
    "POLICY_KEYS",
    "PROBE_SET_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "SUBJECT_TYPES",
    "AvailabilityStatus",
    "FindingStatus",
    "SubjectType",
    "PolicyProfile",
    "canonical_bytes",
    "canonical_json",
    "compute_finding_id",
    "compute_input_bundle_hash",
    "compute_review_hash",
    "assemble_application_bundle",
    "run_review",
]

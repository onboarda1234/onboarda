"""Hash definitions for the Supervisor foundation.

Three hashes, each over an explicitly enumerated input. Nothing is hashed
implicitly, so it is always readable from this file what would change a hash.

``input_bundle_hash``
    SHA-256 over the canonical encoding of the assembled bundle. Identifies the
    current-state evidence a review was computed from.

``review_hash``
    SHA-256 over the policy version, probe set version, input bundle hash and
    the sorted finding list. Separating ``input_bundle_hash`` from
    ``policy_version`` is what makes policy replay a hash comparison later:
    same bundle, different policy.

``finding_id``
    SHA-256 (truncated) over the finding's identity fields. Derived rather than
    random, so the same defect on the same subject keeps the same identifier
    across runs — which is what lets a later phase diff two reviews.

Excluded from every hash, by construction: narration, wall-clock timestamps,
request IDs, random IDs, and non-evidentiary runtime metadata. The exclusions
are applied by the caller (``adapters`` strips them before assembly) and
re-asserted by ``assert_no_excluded_keys``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from .canonical_json import canonical_bytes, canonical_json

#: Length of the truncated hex digest used for finding identifiers. 16 hex
#: characters is 64 bits — ample for per-subject uniqueness, short enough to
#: read in a UI and a log line.
FINDING_ID_LENGTH = 16

#: Keys that must never appear anywhere inside a hashed structure. Narration is
#: excluded so prose may vary without changing a hash; the rest are excluded
#: because they vary between runs of an identical review.
EXCLUDED_HASH_KEYS = frozenset(
    {
        "checked_at",
        "evaluated_at",
        "generated_at",
        "narrative",
        "narration",
        "request_id",
        "suggested_questions",
        "updated_at",
    }
)


class ExcludedHashKeyError(ValueError):
    """A non-evidentiary key reached a structure that is about to be hashed."""


def assert_no_excluded_keys(value: Any, *, path: str = "$") -> None:
    """Raise if any key in ``EXCLUDED_HASH_KEYS`` appears anywhere in ``value``.

    Defence in depth. ``adapters`` already strips these, so a hit here means a
    new upstream field slipped through and would have made hashes unstable.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in EXCLUDED_HASH_KEYS:
                raise ExcludedHashKeyError(
                    f"non-evidentiary key {key!r} present at {path}; "
                    "strip it in adapters before hashing"
                )
            assert_no_excluded_keys(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_excluded_keys(item, path=f"{path}[{index}]")


def sha256_hex(payload: bytes) -> str:
    """Hex SHA-256. The one hash primitive; there is no second one anywhere."""
    return hashlib.sha256(payload).hexdigest()


#: Retained for the call sites in this module, which predate the public name.
_sha256_hex = sha256_hex


def compute_input_bundle_hash(bundle: Mapping[str, Any]) -> str:
    """Hash the assembled bundle.

    The bundle passed here must not already carry its own hash — a hash cannot
    contain itself. ``assemble_application_bundle`` attaches the result to
    ``meta.input_bundle_hash`` after this returns.
    """
    assert_no_excluded_keys(bundle)
    return _sha256_hex(canonical_bytes(bundle))


def sort_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return findings in canonical order.

    Ordered by ``(probe_id, category, claim, finding_id)``. The first three make
    the order meaningful to a reader; ``finding_id`` is the deterministic final
    tie-breaker so two findings identical in the first three still sort stably.
    """
    return sorted(
        (dict(finding) for finding in findings),
        key=lambda finding: (
            str(finding.get("probe_id") or ""),
            str(finding.get("category") or ""),
            str(finding.get("claim") or ""),
            str(finding.get("finding_id") or ""),
        ),
    )


def compute_review_hash(
    *,
    policy_version: str,
    probe_set_version: str,
    input_bundle_hash: str,
    findings: Sequence[Mapping[str, Any]],
) -> str:
    """Hash a completed review.

    Keyword-only: the four inputs are easy to transpose positionally, and a
    transposition would silently produce a wrong-but-stable hash.
    """
    payload = {
        "findings": sort_findings(findings),
        "input_bundle_hash": input_bundle_hash,
        "policy_version": policy_version,
        "probe_set_version": probe_set_version,
    }
    assert_no_excluded_keys(payload)
    return _sha256_hex(canonical_bytes(payload))


def compute_finding_id(
    *,
    subject_type: str,
    subject_id: str,
    probe_id: str,
    probe_version: str,
    category: str,
    primary_evidence_ref: str,
) -> str:
    """Derive a finding's stable identifier.

    ``primary_evidence_ref`` is what distinguishes two hits from the same probe
    on the same subject — for example the same ownership probe firing on two
    different parties. A probe that can hit more than once per subject must
    supply a distinct primary reference for each hit, or the two collapse to one
    identifier.
    """
    payload = {
        "category": category,
        "primary_evidence_ref": primary_evidence_ref,
        "probe_id": probe_id,
        "probe_version": probe_version,
        "subject_id": subject_id,
        "subject_type": subject_type,
    }
    return _sha256_hex(canonical_json(payload).encode("utf-8"))[:FINDING_ID_LENGTH]

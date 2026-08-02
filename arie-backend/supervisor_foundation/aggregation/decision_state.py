"""Whether the bundle establishes that a decision was taken.

Some things the review says are only true of a case that reached a decision.
"Why was this customer approved without a live screening result?" is a fair
question about an approved file and a false statement about a draft one — it
asserts an approval that never happened, in a document an officer may hand to a
regulator.

The rule this module exists to enforce: **approval-dependent wording appears
only where the structured bundle establishes approval.** Not where severity
suggests it, and not where the case merely looks finished.

Severity is not sufficient, and relying on it was the original defect. P-04
raises a screening finding to ``critical`` when the case was approved, so
severity *correlates* with approval — but it is the probe's encoding of
reliance, not a decision record, and reading approval back out of it couples
this layer's grammar to another layer's severity policy. Two structured signals
are read instead, and either is sufficient:

* ``application.fields.status == "approved"``
* a ``decision.records`` entry with ``decision_type == "approve"``

Everything else — pending, draft, rejected, in review, unknown, or a decision
section that cannot be read — is **not established**, and the review uses
factually neutral wording. The failure direction is deliberate: a neutral
question on an approved file is a slightly weaker question, while an approval
claim on a rejected file is a false statement.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The one application status that establishes approval on its own.
APPROVED_STATUS = "approved"

#: The one decision type that establishes approval on its own.
APPROVE_DECISION = "approve"


def _mapping(value: Any) -> Mapping[str, Any]:
    """``or {}`` rescues ``None`` but passes a truthy non-mapping to ``.get``."""
    return value if isinstance(value, Mapping) else {}


def approval_established(bundle: Mapping[str, Any]) -> bool:
    """Whether the bundle positively establishes that this case was approved.

    Total on malformed input: a bundle whose application or decision section is
    absent, ``None``, or the wrong shape returns ``False``, because a section
    that cannot be read has established nothing.
    """
    if not isinstance(bundle, Mapping):
        return False

    fields = _mapping(_mapping(bundle.get("application")).get("fields"))
    if str(fields.get("status") or "").strip().lower() == APPROVED_STATUS:
        return True

    records = _mapping(bundle.get("decision")).get("records")
    if not isinstance(records, (list, tuple)):
        return False
    return any(
        str(_mapping(record).get("decision_type") or "").strip().lower() == APPROVE_DECISION
        for record in records
    )

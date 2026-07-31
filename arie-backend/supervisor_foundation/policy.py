"""Typed institution-policy input contract.

Phase 0B-1 scope, per §3.3.5 of the framework document: the minimum typed and
versioned contract needed to represent *policy availability*. Deliberately
absent — and requiring separate founder approval — are persistence, migrations,
an administration UI, a rule builder, an approval workflow, template activation
and any automatic default.

The governing rule (§3.3.4): **an unset key is reported
``policy_not_configured``, never filled from a template or a default.** A
finding derived from an unapproved default is a finding the institution never
agreed to be measured against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import POLICY_CONTRACT_VERSION, POLICY_KEYS, AvailabilityStatus


class UnknownPolicyKeyError(KeyError):
    """A key outside the declared contract was supplied or requested."""


@dataclass(frozen=True)
class PolicyProfile:
    """An institution's approved policy, as consumed by the Supervisor.

    This is a read model. Nothing here creates, edits, approves or supersedes a
    policy — those are separate, unapproved concerns.

    ``policy_version`` is the institution's approved version identifier. The
    sentinel :data:`UNCONFIGURED_POLICY_VERSION` marks the state where no policy
    has been supplied at all, which is the Phase 0B-1 default.
    """

    policy_version: str
    policy_jurisdiction: str | None = None
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.values) - set(POLICY_KEYS))
        if unknown:
            raise UnknownPolicyKeyError(
                f"keys outside the declared policy contract: {unknown}"
            )
        # Freeze so a caller cannot mutate configured values between the hash
        # and the review that claims to be described by it.
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    # ── Availability ─────────────────────────────────────────────────

    def is_configured(self, key: str) -> bool:
        """Whether ``key`` has an approved value. No defaults are consulted."""
        if key not in POLICY_KEYS:
            raise UnknownPolicyKeyError(key)
        return key in self.values and self.values[key] is not None

    def availability(self, key: str) -> AvailabilityStatus:
        """Availability of ``key``: ``available`` or ``policy_not_configured``."""
        return (
            AvailabilityStatus.AVAILABLE
            if self.is_configured(key)
            else AvailabilityStatus.POLICY_NOT_CONFIGURED
        )

    def get(self, key: str) -> Any:
        """Return the approved value for ``key``.

        Raises when the key is unconfigured. There is intentionally no
        ``default`` parameter: a caller that wants a fallback is asking to make
        an unapproved value authoritative, which §3.3.4 prohibits. Callers must
        branch on :meth:`is_configured` and emit ``policy_not_configured``.
        """
        if not self.is_configured(key):
            raise UnknownPolicyKeyError(
                f"policy key {key!r} is not configured; emit "
                "policy_not_configured rather than substituting a default"
            )
        return self.values[key]

    # ── Bundle projection ────────────────────────────────────────────

    def availability_metadata(self) -> dict[str, Any]:
        """The only policy content that enters the bundle.

        Availability metadata only — never the configured values themselves.
        Values would make the bundle hash change when a threshold is retuned,
        conflating "the evidence changed" with "the policy changed", which
        §10.7 requires to stay distinguishable.
        """
        return {
            "policy_contract_version": POLICY_CONTRACT_VERSION,
            "policy_version": self.policy_version,
            "policy_jurisdiction": self.policy_jurisdiction,
            "keys": {
                key: self.availability(key).value for key in sorted(POLICY_KEYS)
            },
            "configured_key_count": sum(
                1 for key in POLICY_KEYS if self.is_configured(key)
            ),
            "declared_key_count": len(POLICY_KEYS),
        }


#: Marks "no institution policy supplied". Not a policy version an institution
#: can approve — it is the absence of one.
UNCONFIGURED_POLICY_VERSION = "unconfigured"


def unconfigured_policy() -> PolicyProfile:
    """The Phase 0B-1 default: every declared key unconfigured.

    Used whenever a caller supplies no policy. Every key reports
    ``policy_not_configured``, so a policy-dependent probe added in a later
    phase degrades to ``unavailable`` instead of silently adopting a default.
    """
    return PolicyProfile(policy_version=UNCONFIGURED_POLICY_VERSION)

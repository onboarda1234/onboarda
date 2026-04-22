"""
EDD Routing Policy — Priority B / Workstream C (decision-path integrity).
=========================================================================

Pure, deterministic, server-side routing policy that decides whether an
onboarding case must be routed to Enhanced Due Diligence (EDD) instead of
standard review.

Design principles
-----------------
* **Pure**: ``evaluate_edd_routing`` takes a fact dict and returns a
  decision dict. No DB, HTTP, or filesystem access.
* **Deterministic**: identical inputs always produce identical outputs.
  No prompt, no model, no randomness.
* **Versioned**: every decision carries a ``policy_version`` so audit
  consumers can detect drift between policy editions.
* **Provider-agnostic**: facts are described in canonical terms
  (``sector_risk_tier``, ``jurisdiction_risk_tier``,
  ``ownership_transparency_status``, ``screening_terminality_summary``)
  rather than provider-specific shapes — keeping the door open for the
  ComplyAdvantage migration without coupling to Sumsub.
* **Fail-closed**: when the contract is incomplete, the policy errs on
  the side of EDD with explicit ``incomplete_contract`` triggers.

The minimum-safe contract this policy understands is documented below
under ``REQUIRED_FACT_KEYS`` and is exactly the contract Agent 5 is
required to honour (see ``memo_handler.build_compliance_memo``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("arie.edd_routing")

# ── Policy metadata ──────────────────────────────────────────────────
POLICY_VERSION = "edd_routing_policy_v1"

# ── Canonical contract keys ──────────────────────────────────────────
# These are the fields the policy consults. Missing keys are treated
# as ``None`` and trigger an ``incomplete_contract`` flag.
REQUIRED_FACT_KEYS = (
    "final_risk_level",
    "declared_pep_present",
    "sector_risk_tier",
    "jurisdiction_risk_tier",
    "ownership_transparency_status",
    "screening_terminality_summary",
    "edd_trigger_flags",
    "supervisor_mandatory_escalation",
)

# ── Trigger vocabulary (stable; adding new triggers requires a new policy version) ──
TRIGGER_HIGH_RISK = "high_or_very_high_risk"
TRIGGER_DECLARED_PEP = "declared_pep_present"
TRIGGER_HIGH_SECTOR = "high_risk_sector"
TRIGGER_CRYPTO_SECTOR = "crypto_or_virtual_asset_sector"
TRIGGER_ELEVATED_JURISDICTION = "elevated_jurisdiction"
TRIGGER_OPAQUE_OWNERSHIP = "opaque_or_incomplete_ownership"
TRIGGER_MANDATORY_ESCALATION = "supervisor_mandatory_escalation"
TRIGGER_SCREENING_MATCH = "material_screening_concern"
TRIGGER_INCOMPLETE_CONTRACT = "incomplete_contract"

ALL_TRIGGERS = (
    TRIGGER_HIGH_RISK,
    TRIGGER_DECLARED_PEP,
    TRIGGER_HIGH_SECTOR,
    TRIGGER_CRYPTO_SECTOR,
    TRIGGER_ELEVATED_JURISDICTION,
    TRIGGER_OPAQUE_OWNERSHIP,
    TRIGGER_MANDATORY_ESCALATION,
    TRIGGER_SCREENING_MATCH,
    TRIGGER_INCOMPLETE_CONTRACT,
)

ROUTE_EDD = "edd"
ROUTE_STANDARD = "standard"


def _norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip().lower()


def evaluate_edd_routing(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate the deterministic EDD routing policy.

    Args:
        facts: A dict containing the canonical authoritative case facts
            (see ``REQUIRED_FACT_KEYS``). Unknown keys are ignored;
            missing keys are treated as ``None``.

    Returns:
        A decision dict::

            {
                "policy_version": "edd_routing_policy_v1",
                "route": "edd" | "standard",
                "triggers": [str, ...],   # stable trigger vocabulary
                "inputs": {...key inputs echoed...},
                "evaluated_at": "<iso utc timestamp>",
            }

    Notes:
        - The same case (same canonical facts) MUST always return the
          same triggers in the same order. ``triggers`` is sorted to
          guarantee that property.
        - ``route="edd"`` if any trigger is present; ``route="standard"``
          only when ``triggers == []``.
    """
    triggers: List[str] = []

    final_risk = (_norm(facts.get("final_risk_level"))).upper()
    declared_pep = bool(facts.get("declared_pep_present"))
    sector_tier = _norm(facts.get("sector_risk_tier"))
    sector_label = _norm(facts.get("sector_label"))
    jurisdiction_tier = _norm(facts.get("jurisdiction_risk_tier"))
    ownership = _norm(facts.get("ownership_transparency_status"))
    screening = facts.get("screening_terminality_summary") or {}
    edd_flags = facts.get("edd_trigger_flags") or []
    mandatory_escalation = bool(facts.get("supervisor_mandatory_escalation"))

    contract_incomplete = False
    for key in REQUIRED_FACT_KEYS:
        if key not in facts:
            contract_incomplete = True
            break

    if final_risk in ("HIGH", "VERY_HIGH"):
        triggers.append(TRIGGER_HIGH_RISK)

    if declared_pep:
        triggers.append(TRIGGER_DECLARED_PEP)

    if sector_tier in ("high", "very_high", "elevated"):
        triggers.append(TRIGGER_HIGH_SECTOR)
    # Crypto / virtual-asset is called out separately so officers can
    # see it explicitly in the trigger list, even though it is also a
    # high-risk sector. This is a *narrative* discriminator, not a
    # different risk weight.
    if any(tok in sector_label for tok in ("crypto", "virtual asset", "digital asset")):
        triggers.append(TRIGGER_CRYPTO_SECTOR)
        if TRIGGER_HIGH_SECTOR not in triggers:
            triggers.append(TRIGGER_HIGH_SECTOR)

    if jurisdiction_tier in ("high", "very_high", "restricted", "elevated", "sanctioned"):
        triggers.append(TRIGGER_ELEVATED_JURISDICTION)

    if ownership in ("opaque", "incomplete", "unknown", "high"):
        triggers.append(TRIGGER_OPAQUE_OWNERSHIP)

    if mandatory_escalation:
        triggers.append(TRIGGER_MANDATORY_ESCALATION)

    # Material screening concern: any subject with a terminal *match*
    # (not merely "non-terminal pending"). The screening_terminality
    # summary is built by memo_handler from the canonical screening_state
    # module.
    if isinstance(screening, dict):
        if screening.get("has_terminal_match"):
            triggers.append(TRIGGER_SCREENING_MATCH)
        # Non-terminal screening is an officer concern but not, on its
        # own, an EDD trigger — it is handled by the approval gate /
        # screening completeness check elsewhere. Recorded here only
        # so audit consumers can see it considered.

    # Also treat any explicit edd_trigger_flags surfaced by the rule
    # engine as a trigger source, prefixed for traceability.
    if isinstance(edd_flags, (list, tuple)):
        for flag in edd_flags:
            if not flag:
                continue
            tag = "edd_flag:" + str(flag).strip().lower().replace(" ", "_")
            if tag not in triggers:
                triggers.append(tag)

    if contract_incomplete:
        triggers.append(TRIGGER_INCOMPLETE_CONTRACT)

    # Sort trigger list for deterministic output (excluding edd_flag:*
    # which we keep at the end, sorted, to preserve grouping).
    base = sorted({t for t in triggers if not t.startswith("edd_flag:")})
    extra = sorted({t for t in triggers if t.startswith("edd_flag:")})
    triggers_sorted = base + extra

    route = ROUTE_EDD if triggers_sorted else ROUTE_STANDARD

    decision = {
        "policy_version": POLICY_VERSION,
        "route": route,
        "triggers": triggers_sorted,
        "inputs": {
            "final_risk_level": final_risk or None,
            "declared_pep_present": declared_pep,
            "sector_risk_tier": sector_tier or None,
            "sector_label": sector_label or None,
            "jurisdiction_risk_tier": jurisdiction_tier or None,
            "ownership_transparency_status": ownership or None,
            "supervisor_mandatory_escalation": mandatory_escalation,
            "screening_terminality_summary": (
                {
                    "terminal": bool(screening.get("terminal")) if isinstance(screening, dict) else None,
                    "has_terminal_match": bool(screening.get("has_terminal_match")) if isinstance(screening, dict) else None,
                    "has_non_terminal": bool(screening.get("has_non_terminal")) if isinstance(screening, dict) else None,
                }
            ),
            "edd_trigger_flags": list(edd_flags) if isinstance(edd_flags, (list, tuple)) else [],
        },
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    return decision


def assert_routing_invariant(facts: Dict[str, Any], routing: Dict[str, Any]) -> Optional[str]:
    """
    Drift-detection invariant.

    Recomputes the routing from ``facts`` using the current policy and
    compares it to a previously-emitted ``routing`` dict (e.g. one read
    back from persistence or audit). Returns ``None`` when the two
    agree, otherwise returns a human-readable description of the
    divergence. Designed so callers (and tests) can fail-loud when a
    persisted routing drifts from policy.

    The invariant is intentionally narrow: it compares
    ``(policy_version, route, triggers)``. Timestamps and echoed inputs
    are excluded so that legitimate clock movement does not trip it.
    """
    if not isinstance(routing, dict):
        return "routing is not a dict"
    fresh = evaluate_edd_routing(facts)
    if fresh["policy_version"] != routing.get("policy_version"):
        return (
            "policy_version drift: persisted="
            + str(routing.get("policy_version"))
            + ", current=" + fresh["policy_version"]
        )
    if fresh["route"] != routing.get("route"):
        return (
            "route drift: persisted=" + str(routing.get("route"))
            + ", current=" + fresh["route"]
        )
    persisted_triggers = sorted(list(routing.get("triggers") or []))
    if persisted_triggers != list(fresh["triggers"]):
        return (
            "triggers drift: persisted=" + str(persisted_triggers)
            + ", current=" + str(fresh["triggers"])
        )
    return None


def emit_routing_audit(db, user: Dict[str, Any], application_ref: str,
                       routing: Dict[str, Any], client_ip: str = "") -> None:
    """
    Write a single ``audit_log`` row for an EDD routing evaluation.

    The row uses the canonical audit_log shape (user_id, user_name,
    user_role, action, target, detail, ip_address). Detail is a JSON
    blob containing the policy version, route, triggers and key inputs
    so reviewers can reconstruct the decision without re-running the
    pipeline.

    This helper deliberately swallows DB errors: routing evaluation is
    advisory and must never block memo generation. The caller commits.
    """
    if db is None or routing is None:
        return
    try:
        import json as _json
        detail = _json.dumps({
            "policy_version": routing.get("policy_version"),
            "route": routing.get("route"),
            "triggers": routing.get("triggers", []),
            "inputs": routing.get("inputs", {}),
            "evaluated_at": routing.get("evaluated_at"),
        }, default=str, sort_keys=True)
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                (user or {}).get("sub", "system"),
                (user or {}).get("name", "system"),
                (user or {}).get("role", "system"),
                "edd_routing.evaluated",
                "application:" + str(application_ref or ""),
                detail,
                client_ip or "",
            ),
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.error("Failed to write EDD routing audit row: %s", e)

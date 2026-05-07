"""
routing_actuator
================

Priority E hardening: deterministic, auditable, idempotent EDD-route
actuation glue.

This module is a thin orchestration layer that lives BETWEEN the
already-existing pure policy (``edd_routing_policy.evaluate_edd_routing``)
and the already-existing actuator (``server._actuate_edd_routing``,
which writes the ``edd_cases`` row + flips ``applications.status`` to
``edd_required`` when the policy says so).

Why a new module instead of inlining?
-------------------------------------
Before this PR, the ONLY production callers of
``evaluate_edd_routing`` + ``_actuate_edd_routing`` were:

* ``server.MemoSupervisorRunHandler.post`` (post-supervisor verdict)
* ``memo_handler.run_supervisor_stage`` (memo regeneration)

Live evidence on staging shows that pre-screening / risk-recompute /
screening-update paths NEVER ran the policy and instead mapped
``onboarding_lane`` purely from ``risk_level`` via::

    {"LOW": "Fast Lane", "MEDIUM": "Standard Review",
     "HIGH": "EDD", "VERY_HIGH": "EDD"}

That meant a Medium-scored crypto / BVI / declared-PEP case with
opaque ownership stayed in "Standard Review" until/unless a memo
supervisor re-evaluated it later -- a hard control failure.

This module exposes ONE function, :func:`apply_routing_decision`,
that any of those upstream paths can call. It:

1. Builds the canonical fact dict that ``evaluate_edd_routing``
   consumes (risk level + sector + jurisdiction + ownership + PEP +
   screening + supervisor escalation + explicit edd flags).
2. Runs the deterministic v1 policy.
3. Persists the resulting lane on ``applications.onboarding_lane``
   (``"EDD"`` when policy route == edd; otherwise the supplied
   level-based lane is left alone).
4. Calls ``server._actuate_edd_routing`` to UPSERT the ``edd_cases``
   row (idempotent, will not duplicate, writes audit row
   ``edd_routing.actuated``).
5. Writes the ``edd_routing.evaluated`` audit row (via
   ``server._emit_edd_routing_audit``) so that drift can be analysed
   even when actuation is a no-op (route=standard).
6. Generates backend-only application enhanced requirement records
   when the evaluated route or durable application facts expose
   enhanced-review triggers.  This is idempotent and does not create
   RMI requests, portal prompts, memo output, or approval blockers.

The function is fail-soft for the upstream caller -- it never
re-raises and always returns a structured ``dict`` so the caller can
log / surface / ignore as appropriate. It MUST NOT raise: the
upstream paths (prescreening, recompute, screening) are mid-
transaction critical paths whose primary contract (risk score
persistence) must succeed even if EDD plumbing has a transient
issue. The accompanying reconciliation utility
(``tools/reconcile_edd_routing.py``) is the safety net.

This module performs NO ``db.commit()``. The caller owns the
transaction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Sources accepted by the audit row. Keep stable; tests/dashboards
# pivot on these strings.
SOURCE_PRESCREENING_SUBMIT = "prescreening_submit"
SOURCE_RISK_RECOMPUTE = "risk_recompute"
SOURCE_SCREENING_UPDATE = "screening_update"
SOURCE_MANUAL_RECONCILIATION = "manual_reconciliation"

_VALID_SOURCES = frozenset(
    {
        SOURCE_PRESCREENING_SUBMIT,
        SOURCE_RISK_RECOMPUTE,
        SOURCE_SCREENING_UPDATE,
        SOURCE_MANUAL_RECONCILIATION,
    }
)


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def build_routing_facts(
    *,
    app_row: Mapping[str, Any],
    risk_dict: Optional[Mapping[str, Any]] = None,
    screening_summary: Optional[Mapping[str, Any]] = None,
    supervisor_mandatory_escalation: Optional[bool] = None,
    edd_trigger_flags: Optional[list] = None,
) -> Dict[str, Any]:
    """Compose the canonical facts dict consumed by
    :func:`edd_routing_policy.evaluate_edd_routing`.

    Reads the ``final_risk_level`` / ``base_risk_level`` carefully so
    that policy uses the FINAL post-elevation level, never the stale
    base level. Falls back to ``risk_level`` only if neither is
    present.
    """

    rd = dict(risk_dict or {})
    ar = dict(app_row or {})

    # Final risk level: prefer in-memory recompute output, then DB
    # ``final_risk_level`` column, then ``base_risk_level``, then
    # ``risk_level`` legacy column.
    final_risk_level = (
        rd.get("final_risk_level")
        or rd.get("level")
        or ar.get("final_risk_level")
        or ar.get("base_risk_level")
        or ar.get("risk_level")
        or ""
    )

    composite = (
        rd.get("score")
        if rd.get("score") is not None
        else ar.get("risk_score")
    )

    sector_label = rd.get("sector_label") or ar.get("sector") or ""
    sector_risk_tier = (
        rd.get("sector_risk_tier") or ar.get("sector_risk_tier") or ""
    )
    jurisdiction_risk_tier = (
        rd.get("jurisdiction_risk_tier")
        or ar.get("jurisdiction_risk_tier")
        or ""
    )
    ownership_transparency = (
        rd.get("ownership_transparency_status")
        or ar.get("ownership_transparency_status")
        or ar.get("ownership_structure")
        or ""
    )

    declared_pep = bool(
        rd.get("declared_pep_present")
        if rd.get("declared_pep_present") is not None
        else ar.get("has_declared_pep")
        or ar.get("declared_pep_present")
        or False
    )

    facts: Dict[str, Any] = {
        "final_risk_level": final_risk_level,
        "composite_score": composite,
        "declared_pep_present": declared_pep,
        "sector_label": sector_label,
        "sector_risk_tier": sector_risk_tier,
        "jurisdiction_risk_tier": jurisdiction_risk_tier,
        "ownership_transparency_status": ownership_transparency,
        "supervisor_mandatory_escalation": bool(
            supervisor_mandatory_escalation
        )
        if supervisor_mandatory_escalation is not None
        else False,
        "edd_trigger_flags": list(edd_trigger_flags or []),
        # Required-fact-keys contract from the pure policy: presence is
        # what matters; values may be None / empty strings.
        "company_name": ar.get("company_name") or "",
        "country": ar.get("country") or "",
        "sector": sector_label,
    }

    # Always materialise the canonical contract keys (with safe
    # defaults) so the policy never spuriously fires
    # `incomplete_contract` for fields that callers are not aware
    # of yet (screening summary, edd_trigger_flags, etc.).
    facts.setdefault("screening_terminality_summary", {})
    if isinstance(screening_summary, Mapping):
        facts["screening_terminality_summary"] = dict(screening_summary)

    return facts


def apply_routing_decision(
    *,
    db,
    app_row: Mapping[str, Any],
    risk_dict: Optional[Mapping[str, Any]] = None,
    screening_summary: Optional[Mapping[str, Any]] = None,
    supervisor_mandatory_escalation: Optional[bool] = None,
    edd_trigger_flags: Optional[list] = None,
    user: Optional[Mapping[str, Any]] = None,
    client_ip: str = "",
    source: str = SOURCE_PRESCREENING_SUBMIT,
) -> Dict[str, Any]:
    """Run the EDD routing policy and persist its consequences.

    Returns a structured result::

        {
          "ran": bool,                # False only on hard guard
          "route": "edd" | "standard",
          "policy_version": str,
          "triggers": list[str],
          "lane_persisted": str | None,  # the value written, if any
          "actuation": {...},         # forwarded from _actuate_edd_routing
          "errors": list[str],
        }

    Never raises. The caller MUST NOT depend on side effects when
    ``ran`` is False -- in that case, the reconciler will heal it
    later.
    """

    result: Dict[str, Any] = {
        "ran": False,
        "route": None,
        "policy_version": None,
        "triggers": [],
        "lane_persisted": None,
        "actuation": None,
        "enhanced_requirements_generation": None,
        "enhanced_requirement_triggers": [],
        "errors": [],
    }

    if source not in _VALID_SOURCES:
        result["errors"].append(f"invalid_source:{source}")
        return result
    if not app_row:
        result["errors"].append("missing_app_row")
        return result
    if db is None:
        result["errors"].append("missing_db")
        return result

    try:
        # Imported lazily to avoid a hard import cycle at module load
        # time (server.py imports this module via the wiring patch).
        from edd_routing_policy import (  # type: ignore
            evaluate_edd_routing,
            ROUTE_EDD,
            ROUTE_STANDARD,
        )
    except Exception as e:  # pragma: no cover - import guard
        result["errors"].append(f"policy_import:{e}")
        return result

    facts = build_routing_facts(
        app_row=app_row,
        risk_dict=risk_dict,
        screening_summary=screening_summary,
        supervisor_mandatory_escalation=supervisor_mandatory_escalation,
        edd_trigger_flags=edd_trigger_flags,
    )

    try:
        routing = evaluate_edd_routing(facts)
    except Exception as e:
        logger.exception(
            "evaluate_edd_routing failed for app %s source=%s",
            (dict(app_row).get("ref") or dict(app_row).get("id")),
            source,
        )
        result["errors"].append(f"policy_eval:{e}")
        return result

    routing = dict(routing or {})
    # Tag the source so downstream audit can distinguish the path.
    routing["source"] = source

    result["route"] = routing.get("route")
    result["policy_version"] = routing.get("policy_version")
    result["triggers"] = list(routing.get("triggers") or [])

    app_dict = dict(app_row)
    application_id = app_dict.get("id")
    application_ref = app_dict.get("ref") or _norm_str(application_id)

    # ---- Persist lane decision -----------------------------------------
    try:
        if routing.get("route") == ROUTE_EDD:
            db.execute(
                "UPDATE applications SET onboarding_lane = ? WHERE id = ?",
                ("EDD", application_id),
            )
            result["lane_persisted"] = "EDD"
    except Exception as e:
        logger.warning(
            "Failed to persist EDD lane for app %s: %s",
            application_ref,
            e,
        )
        result["errors"].append(f"lane_persist:{e}")

    # ---- Audit: edd_routing.evaluated ---------------------------------
    try:
        from server import _emit_edd_routing_audit  # type: ignore

        _emit_edd_routing_audit(
            db, user, application_ref, routing, client_ip
        )
    except Exception as e:
        logger.warning(
            "Failed to emit edd_routing.evaluated audit for %s: %s",
            application_ref,
            e,
        )
        result["errors"].append(f"audit_evaluated:{e}")

    # ---- Actuate EDD case (idempotent) --------------------------------
    if routing.get("route") == ROUTE_EDD:
        try:
            from server import _actuate_edd_routing  # type: ignore

            actuation = _actuate_edd_routing(
                db,
                app_row,
                routing,
                {
                    "mandatory_escalation": bool(
                        supervisor_mandatory_escalation
                    ),
                    "mandatory_escalation_reasons": list(
                        edd_trigger_flags or []
                    ),
                },
                user,
                client_ip,
            )
            result["actuation"] = actuation
        except Exception as e:
            logger.error(
                "EDD actuation failed for app %s source=%s: %s",
                application_ref,
                source,
                e,
                exc_info=True,
            )
            result["errors"].append(f"actuate:{e}")

    # ---- Step 3: backend-only enhanced requirement generation ----------
    # Keep this after existing routing/actuation so EDD case semantics and
    # status changes remain owned by the established path. The generation
    # engine is fail-soft and create-only/idempotent.
    try:
        from enhanced_requirements import (  # type: ignore
            detect_application_enhanced_requirement_triggers,
            generate_application_enhanced_requirements,
        )

        trigger_result = detect_application_enhanced_requirement_triggers(
            db,
            application_id=application_id,
            app_row=app_row,
            routing=routing,
        )
        detected_triggers = list(trigger_result.get("triggers") or [])
        result["enhanced_requirement_triggers"] = detected_triggers

        final_level = _norm_str(facts.get("final_risk_level")).upper()
        app_lane = _norm_str(app_dict.get("onboarding_lane")).upper()
        app_status = _norm_str(app_dict.get("status")).lower()
        should_generate = (
            routing.get("route") == ROUTE_EDD
            or final_level in {"HIGH", "VERY_HIGH"}
            or app_lane == "EDD"
            or app_status == "edd_required"
            or bool(detected_triggers)
        )

        if should_generate:
            result["enhanced_requirements_generation"] = (
                generate_application_enhanced_requirements(
                    db,
                    application_id,
                    app_row=app_row,
                    routing=trigger_result.get("routing") or routing,
                    actor=user,
                    generation_source=source,
                )
            )
    except Exception as e:
        logger.warning(
            "Enhanced requirement generation failed for app %s source=%s: %s",
            application_ref,
            source,
            e,
        )
        result["errors"].append(f"enhanced_requirements:{e}")

    result["ran"] = True
    return result

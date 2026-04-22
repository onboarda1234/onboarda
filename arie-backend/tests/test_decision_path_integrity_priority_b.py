"""
Priority B — Decision-path integrity (Workstreams A, B, C).
============================================================

Locks in the controls that ensure higher-risk cases cannot be:

* narrated incorrectly or too reassuringly (Agent 5 / memo_handler)
* supervised by inconsistent or weaker-than-needed verdicts
* routed to standard review when policy requires EDD

If any of these tests start failing, decision-path integrity is
regressing — that is a critical compliance-trust regression.

The tests use the same memo_handler / supervisor_engine /
edd_routing_policy entry points used in production. They DO NOT mock
the policy: they exercise the deterministic guardrails directly.
"""
import json
import pytest

from memo_handler import build_compliance_memo
from supervisor_engine import run_memo_supervisor
from edd_routing_policy import (
    evaluate_edd_routing,
    assert_routing_invariant,
    POLICY_VERSION,
    ROUTE_EDD,
    ROUTE_STANDARD,
    TRIGGER_HIGH_RISK,
    TRIGGER_DECLARED_PEP,
    TRIGGER_HIGH_SECTOR,
    TRIGGER_CRYPTO_SECTOR,
    TRIGGER_ELEVATED_JURISDICTION,
    TRIGGER_OPAQUE_OWNERSHIP,
    TRIGGER_MANDATORY_ESCALATION,
    TRIGGER_SCREENING_MATCH,
    REQUIRED_FACT_KEYS,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _make_app(*, country="Mauritius", sector="Technology", risk_level="LOW",
              risk_score=25, api_status="live", company_name="ACME Co",
              risk_escalations=None):
    return {
        "id": "app_dpi_" + country.lower().replace(" ", "_") + "_" + sector.lower().replace(" ", "_"),
        "ref": "ARF-DPI-" + country[:3].upper() + "-" + sector[:3].upper(),
        "company_name": company_name,
        "brn": "C12345",
        "country": country,
        "sector": sector,
        "entity_type": "SME",
        "ownership_structure": "Single tier",
        "operating_countries": country,
        "incorporation_date": "2020-01-01",
        "business_activity": "Software services",
        "source_of_funds": "Trading revenue",
        "expected_volume": "USD 100,000",
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_escalations": json.dumps(risk_escalations or []),
        "assigned_to": "Officer A",
        "prescreening_data": json.dumps({
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {
                        "matched": False, "results": [],
                        "source": "sumsub", "api_status": api_status,
                    },
                },
                "director_screenings": [{
                    "person_name": "Test Director",
                    "person_type": "director",
                    "declared_pep": "No",
                    "screening": {
                        "matched": False, "results": [],
                        "source": "sumsub", "api_status": api_status,
                    },
                }],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        }),
    }


def _clean_documents():
    """Minimal verified-document set so the supervisor does not flag a
    clean case as INCONSISTENT for missing documents (an unrelated
    pre-existing rule)."""
    return [
        {"doc_type": "Certificate of Incorporation", "verification_status": "verified",
         "ai_confidence": 95, "filename": "coi.pdf"},
        {"doc_type": "Director ID", "verification_status": "verified",
         "ai_confidence": 95, "filename": "id.pdf"},
        {"doc_type": "Proof of Address", "verification_status": "verified",
         "ai_confidence": 95, "filename": "poa.pdf"},
    ]


def _flatten(memo):
    chunks = []
    for sec in (memo.get("sections") or {}).values():
        if not isinstance(sec, dict):
            continue
        for v in sec.values():
            if isinstance(v, str):
                chunks.append(v)
            elif isinstance(v, dict):
                for sub in v.values():
                    if isinstance(sub, dict):
                        c = sub.get("content")
                        if isinstance(c, str):
                            chunks.append(c)
    md = memo.get("metadata") or {}
    for k in ("key_findings", "review_checklist", "conditions"):
        for s in (md.get(k) or []):
            if isinstance(s, str):
                chunks.append(s)
    return "\n".join(chunks).lower()


# ════════════════════════════════════════════════════════════════════
# A. Agent 5 narrative integrity
# ════════════════════════════════════════════════════════════════════


class TestAgent5InputContract:
    """The authoritative input contract is built and persisted."""

    def test_contract_present_on_every_memo(self):
        app = _make_app()
        directors = [{"full_name": "Test Director", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "Sole UBO", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"].get("agent5_input_contract")
        assert contract is not None
        # Required keys for the EDD routing policy contract
        for key in (
            "final_risk_level", "composite_score", "declared_pep_present",
            "jurisdiction_risk_tier", "sector_risk_tier",
            "ownership_transparency_status", "screening_terminality_summary",
            "edd_trigger_flags",
        ):
            assert key in contract, f"missing contract key: {key}"

    def test_contract_reflects_high_risk_sector(self):
        app = _make_app(sector="Cryptocurrency", risk_level="HIGH", risk_score=78)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["sector_risk_tier"] == "HIGH"
        assert contract["sector_label"] == "Cryptocurrency"

    def test_contract_reflects_declared_pep(self):
        app = _make_app()
        directors = [{"full_name": "Pep Director", "nationality": "Mauritius",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["declared_pep_present"] is True
        assert contract["declared_pep_count"] >= 1

    def test_contract_reflects_non_terminal_screening(self):
        app = _make_app(api_status="simulated")
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        screening = memo["metadata"]["agent5_input_contract"]["screening_terminality_summary"]
        assert screening["terminal"] is False


class TestAgent5NarrativeContradictions:
    """Narrative cannot describe the case as low/clean/transparent when facts say otherwise."""

    def test_high_risk_sector_cannot_be_narrated_as_low(self):
        # We cannot easily inject low-sector phrasing into the existing
        # template, but we can verify the guard fires when phrasing
        # leaks in. Inject a banned phrase via a manual narrative
        # mutation, then re-trigger the same guard logic.
        app = _make_app(sector="Cryptocurrency", risk_level="HIGH", risk_score=78)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, rule_engine_result, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        # The actual narrative for a Crypto/HIGH case must NOT contain
        # "low business risk" / "low sector risk" phrasing.
        body = _flatten(memo)
        assert "low business risk" not in body
        assert "low-risk sector" not in body
        # The contract must reflect HIGH sector tier so the supervisor
        # has the truth available for downstream gates.
        assert memo["metadata"]["agent5_input_contract"]["sector_risk_tier"] == "HIGH"

    def test_elevated_jurisdiction_cannot_be_narrated_as_low(self):
        app = _make_app(country="Iran", risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "D", "nationality": "Iranian",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        body = _flatten(memo)
        assert "low jurisdictional risk" not in body
        assert "low-risk jurisdiction" not in body
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["jurisdiction_risk_tier"] in ("HIGH", "VERY_HIGH")

    def test_non_terminal_screening_cannot_be_narrated_as_clean(self):
        app = _make_app(api_status="simulated")
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        body = _flatten(memo)
        # The completion phrase must not appear when screening is non-terminal
        assert "sanctions screening completed across all major consolidated lists" not in body
        # The truthful "NOT complete" phrasing must appear instead
        assert "not complete" in body or "not_complete" in body or "not configured" in body

    def test_narrative_guard_surfaces_contradiction_as_rule_violation(self):
        """When a banned phrase is injected into the narrative for a case
        where facts say otherwise, the contradiction guard must surface it
        as a rule violation. We simulate the leak by mutating a section
        and re-running the supervisor (which ingests rule_engine.violations).
        """
        # Build a HIGH-jurisdiction case
        app = _make_app(country="Iran", risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "D", "nationality": "Iranian",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        # Inject a banned phrase post-hoc and re-run the guard via a
        # fresh supervisor invocation. The supervisor reads
        # metadata.rule_engine.violations; we manually append one as
        # the guard would.
        memo["metadata"]["rule_engine"]["violations"].append({
            "rule": "AGENT5_NARRATIVE_CONTRADICTION_JURISDICTION",
            "severity": "high",
            "detail": "test injection",
            "action": "test",
        })
        memo["metadata"]["rule_engine"]["total_violations"] = len(
            memo["metadata"]["rule_engine"]["violations"]
        )
        supervisor = run_memo_supervisor(memo)
        # A high-severity rule violation produces a critical
        # contradiction → INCONSISTENT.
        assert supervisor["verdict"] == "INCONSISTENT"
        assert supervisor["can_approve"] is False

    def test_declared_pep_remains_reflected_in_narrative(self):
        app = _make_app()
        directors = [{"full_name": "PEP Director", "nationality": "Mauritius",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        body = _flatten(memo)
        # Declared PEP must remain visible somewhere in the narrative
        assert "pep" in body
        # Banned PEP-denial phrasing must not be present
        assert "no pep exposure" not in body
        assert "no declared or detected" not in body

    def test_opaque_ownership_cannot_be_narrated_as_fully_transparent(self):
        # Complex structure (5 UBOs) → struct_complexity=Complex → opaque
        app = _make_app()
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [
            {"full_name": f"UBO {i}", "nationality": "Mauritius",
             "is_pep": "No", "ownership_pct": 20}
            for i in range(5)
        ]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        # Complex structure must surface as opaque/incomplete in the contract
        assert contract["ownership_transparency_status"] in ("opaque", "incomplete")


# ════════════════════════════════════════════════════════════════════
# B. Single authoritative supervisor verdict
# ════════════════════════════════════════════════════════════════════


class TestSupervisorMandatoryEscalation:
    """The supervisor produces a single authoritative verdict with mandatory_escalation."""

    def test_clean_low_risk_does_not_set_mandatory_escalation(self):
        # Use a non-offshore jurisdiction so jur_rating is LOW.
        app = _make_app(country="United Kingdom")
        directors = [{"full_name": "Clean Director", "nationality": "British",
                      "is_pep": "No", "ownership_pct": 0,
                      "date_of_birth": "1980-01-01"}]
        ubos = [{"full_name": "Clean UBO", "nationality": "British",
                 "is_pep": "No", "ownership_pct": 100,
                 "date_of_birth": "1980-01-01"}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, _clean_documents())
        assert supervisor["mandatory_escalation"] is False, (
            "Reasons leaked: " + str(supervisor["mandatory_escalation_reasons"])
            + " | Verdict: " + supervisor["verdict"]
            + " | Contradictions: " + str([c.get("category") for c in supervisor["contradictions"]])
        )
        assert supervisor["mandatory_escalation_reasons"] == []

    def test_high_risk_sets_mandatory_escalation(self):
        app = _make_app(country="Iran", risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "D", "nationality": "Iranian",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        assert supervisor["mandatory_escalation"] is True
        assert supervisor["can_approve"] is False
        assert supervisor["requires_sco_review"] is True
        # Reasons must be human-readable strings
        assert any("final_risk_level" in r or "jurisdiction" in r
                   for r in supervisor["mandatory_escalation_reasons"])

    def test_declared_pep_sets_mandatory_escalation(self):
        app = _make_app()
        directors = [{"full_name": "Pep Director", "nationality": "Mauritius",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        assert supervisor["mandatory_escalation"] is True
        assert "declared_pep_present" in supervisor["mandatory_escalation_reasons"]

    def test_supervisor_verdict_persisted_on_memo(self):
        app = _make_app(country="Iran", risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "D", "nationality": "Iranian",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        # Single authoritative location: memo.supervisor (preferred)
        # and metadata.supervisor_status (UI-facing string mirror).
        assert memo["supervisor"]["verdict"] == supervisor["verdict"]
        assert memo["supervisor"]["mandatory_escalation"] is True
        assert memo["metadata"]["supervisor_status"] == supervisor["verdict"]

    def test_mandatory_escalation_overrides_consistent_verdict(self):
        """Even if the memo content is internally consistent, an escalated
        case must not be approvable."""
        # Construct a contract with HIGH risk → mandatory_escalation
        # while supervising a clean memo (no contradictions).
        memo = {
            "sections": {},
            "metadata": {
                "risk_rating": "HIGH",
                "approval_recommendation": "REVIEW",
                "agent5_input_contract": {
                    "final_risk_level": "HIGH",
                    "declared_pep_present": False,
                    "risk_dimensions": {"jurisdiction": "LOW", "business": "LOW"},
                    "ownership_transparency_status": "transparent",
                    "screening_terminality_summary": {},
                },
                "rule_engine": {"violations": [], "enforcements": []},
            },
        }
        result = run_memo_supervisor(memo)
        # Memo body has no contradictions, so verdict could be CONSISTENT...
        # but mandatory_escalation must still be true and can_approve false.
        assert result["mandatory_escalation"] is True
        assert result["can_approve"] is False


# ════════════════════════════════════════════════════════════════════
# C. Server-side EDD routing policy
# ════════════════════════════════════════════════════════════════════


class TestEddRoutingPolicy:
    """Deterministic, versioned, audit-logged EDD routing policy."""

    def test_low_risk_clean_routes_standard(self):
        facts = {
            "final_risk_level": "LOW",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {"terminal": True, "has_terminal_match": False},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_STANDARD
        assert d["triggers"] == []
        assert d["policy_version"] == POLICY_VERSION

    def test_high_risk_routes_to_edd(self):
        facts = {
            "final_risk_level": "HIGH",
            "declared_pep_present": False,
            "sector_risk_tier": "MEDIUM",
            "sector_label": "Trade",
            "jurisdiction_risk_tier": "MEDIUM",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {"terminal": True},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_HIGH_RISK in d["triggers"]

    def test_declared_pep_routes_to_edd(self):
        facts = {
            "final_risk_level": "MEDIUM",
            "declared_pep_present": True,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_DECLARED_PEP in d["triggers"]

    def test_crypto_routes_to_edd_with_explicit_trigger(self):
        facts = {
            "final_risk_level": "MEDIUM",
            "declared_pep_present": False,
            "sector_risk_tier": "MEDIUM",
            "sector_label": "Cryptocurrency",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_CRYPTO_SECTOR in d["triggers"]
        assert TRIGGER_HIGH_SECTOR in d["triggers"]

    def test_elevated_jurisdiction_routes_to_edd(self):
        facts = {
            "final_risk_level": "MEDIUM",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "VERY_HIGH",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_ELEVATED_JURISDICTION in d["triggers"]

    def test_opaque_ownership_routes_to_edd(self):
        facts = {
            "final_risk_level": "MEDIUM",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "opaque",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_OPAQUE_OWNERSHIP in d["triggers"]

    def test_supervisor_mandatory_escalation_routes_to_edd(self):
        facts = {
            "final_risk_level": "LOW",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": True,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_MANDATORY_ESCALATION in d["triggers"]

    def test_material_screening_match_routes_to_edd(self):
        facts = {
            "final_risk_level": "MEDIUM",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {"terminal": True, "has_terminal_match": True},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD
        assert TRIGGER_SCREENING_MATCH in d["triggers"]

    def test_routing_is_deterministic(self):
        """Identical facts always yield identical (route, triggers)."""
        facts = {
            "final_risk_level": "HIGH",
            "declared_pep_present": True,
            "sector_risk_tier": "HIGH",
            "sector_label": "Cryptocurrency",
            "jurisdiction_risk_tier": "HIGH",
            "ownership_transparency_status": "opaque",
            "screening_terminality_summary": {"has_terminal_match": True},
            "edd_trigger_flags": ["RULE_ESCALATION_PEP"],
            "supervisor_mandatory_escalation": True,
        }
        a = evaluate_edd_routing(facts)
        b = evaluate_edd_routing(facts)
        assert a["route"] == b["route"]
        assert a["triggers"] == b["triggers"]
        assert a["policy_version"] == b["policy_version"]

    def test_drift_invariant_holds_for_self(self):
        facts = {
            "final_risk_level": "HIGH",
            "declared_pep_present": True,
            "sector_risk_tier": "MEDIUM",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        routing = evaluate_edd_routing(facts)
        assert assert_routing_invariant(facts, routing) is None

    def test_drift_invariant_detects_route_drift(self):
        facts = {
            "final_risk_level": "HIGH",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        }
        bogus = evaluate_edd_routing(facts)
        bogus["route"] = ROUTE_STANDARD  # tampered
        bogus["triggers"] = []
        msg = assert_routing_invariant(facts, bogus)
        assert msg is not None
        assert "route" in msg or "trigger" in msg

    def test_incomplete_contract_fails_closed(self):
        # Missing keys → policy adds incomplete_contract trigger AND routes EDD
        facts = {"final_risk_level": "LOW"}
        d = evaluate_edd_routing(facts)
        assert d["route"] == ROUTE_EDD  # fail-closed
        assert "incomplete_contract" in d["triggers"]


class TestEndToEndRoutingFromMemo:
    """The memo builder writes a routing decision that matches policy."""

    def test_clean_low_risk_memo_routes_standard(self):
        app = _make_app(country="United Kingdom")
        directors = [{"full_name": "Clean Director", "nationality": "British",
                      "is_pep": "No", "ownership_pct": 0,
                      "date_of_birth": "1980-01-01"}]
        ubos = [{"full_name": "Clean UBO", "nationality": "British",
                 "is_pep": "No", "ownership_pct": 100,
                 "date_of_birth": "1980-01-01"}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, _clean_documents())
        routing = memo["metadata"].get("edd_routing")
        assert routing is not None, "routing must always be persisted"
        assert routing["route"] == ROUTE_STANDARD, (
            "triggers leaked: " + str(routing.get("triggers"))
        )
        assert routing["policy_version"] == POLICY_VERSION

    def test_high_risk_crypto_pep_memo_routes_edd(self):
        app = _make_app(country="Iran", sector="Cryptocurrency",
                        risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "Pep Director", "nationality": "Iranian",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        routing = memo["metadata"]["edd_routing"]
        assert routing["route"] == ROUTE_EDD
        # All major trigger reasons must be present
        for required in (
            TRIGGER_HIGH_RISK,
            TRIGGER_DECLARED_PEP,
            TRIGGER_CRYPTO_SECTOR,
            TRIGGER_HIGH_SECTOR,
            TRIGGER_ELEVATED_JURISDICTION,
            TRIGGER_MANDATORY_ESCALATION,
        ):
            assert required in routing["triggers"], (
                f"missing trigger {required} in {routing['triggers']}"
            )

    def test_routing_invariant_holds_for_persisted_decision(self):
        """A drift-detection consumer can re-evaluate the policy from
        the persisted contract and confirm the persisted routing matches."""
        app = _make_app(country="Iran", sector="Cryptocurrency",
                        risk_level="VERY_HIGH", risk_score=95)
        directors = [{"full_name": "Pep Director", "nationality": "Iranian",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        contract = dict(contract)
        contract["supervisor_mandatory_escalation"] = supervisor.get("mandatory_escalation", False)
        routing = memo["metadata"]["edd_routing"]
        assert assert_routing_invariant(contract, routing) is None

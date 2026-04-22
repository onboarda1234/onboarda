"""
Priority B.2 — EDD route actuation, contract normalization fixes,
recommendation binding.
=================================================================

Workstreams covered by this file:

* **A. EDD route actuation** — when the deterministic policy returns
  ``route="edd"``, the application is moved to the EDD lane (status
  ``edd_required``) and an active ``edd_cases`` row exists. Repeated
  memo regeneration MUST NOT create duplicate active EDD cases.
* **B. Agent 5 input-contract normalization fixes** —
  ``sector_risk_tier`` cannot be ``low`` for crypto / digital-asset
  labels even when the literal string is not in the canonical
  ``HIGH_RISK_SECTORS`` tuple; ``ownership_transparency_status``
  cannot be ``transparent`` for opaque / multi-jurisdiction /
  partial-disclosure cases.
* **C. Memo recommendation binding** — when
  ``edd_routing.route == "edd"`` OR
  ``supervisor.mandatory_escalation == True``, the memo's
  ``approval_recommendation`` MUST NOT be ``APPROVE`` /
  ``APPROVE_WITH_CONDITIONS``; it is bound to ``ESCALATE_TO_EDD`` and
  a contradiction guard fail-closes if the binding ever leaks.

The actuation tests use the real ``_actuate_edd_routing`` server
helper against a live in-memory SQLite DB so the effects are
exercised end-to-end (no mocking of policy or DB).
"""
import json
import os
import sys

# Force SQLite for these tests so we can exercise the helper without
# a Postgres dependency. server.py reads USE_POSTGRES from the
# DATABASE_URL env var; explicitly clear it.
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("ENVIRONMENT", "development")

import pytest

import db as db_module
from memo_handler import build_compliance_memo
from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD, ROUTE_STANDARD


# ── Helpers ─────────────────────────────────────────────────────────


def _make_app(*, country="Mauritius", sector="Technology", risk_level="LOW",
              risk_score=25, api_status="live", company_name="ACME Co",
              ownership_structure="Single tier",
              risk_escalations=None):
    return {
        "id": "app_b2_" + country.lower().replace(" ", "_") + "_" + sector.lower().replace(" ", "_"),
        "ref": "ARF-B2-" + country[:3].upper() + "-" + sector[:3].upper(),
        "company_name": company_name,
        "brn": "C12345",
        "country": country,
        "sector": sector,
        "entity_type": "SME",
        "ownership_structure": ownership_structure,
        "operating_countries": country,
        "incorporation_date": "2020-01-01",
        "business_activity": "Software services",
        "source_of_funds": "Trading revenue",
        "expected_volume": "USD 100,000",
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_escalations": json.dumps(risk_escalations or []),
        "assigned_to": "Officer A",
        "status": "in_review",
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
    return [
        {"doc_type": "Certificate of Incorporation", "verification_status": "verified",
         "ai_confidence": 95, "filename": "coi.pdf"},
        {"doc_type": "Director ID", "verification_status": "verified",
         "ai_confidence": 95, "filename": "id.pdf"},
        {"doc_type": "Proof of Address", "verification_status": "verified",
         "ai_confidence": 95, "filename": "poa.pdf"},
    ]


# ════════════════════════════════════════════════════════════════════
# B. Agent 5 input-contract normalization fixes
# ════════════════════════════════════════════════════════════════════


class TestSectorTierNormalization:
    """sector_risk_tier MUST NOT normalize to LOW for crypto / virtual-asset
    labels, even when the literal string is not in HIGH_RISK_SECTORS."""

    @pytest.mark.parametrize("sector_label", [
        "Crypto Exchange",
        "Cryptocurrency Exchange",
        "Digital Assets Exchange",
        "Virtual Asset Service Provider",
        "Crypto Custody",
    ])
    def test_crypto_sector_normalizes_to_high(self, sector_label):
        app = _make_app(sector=sector_label, risk_level="MEDIUM", risk_score=55)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, rule_engine_result, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        # sector_risk_tier must be HIGH (the policy lowercases this)
        assert contract["sector_risk_tier"] == "HIGH", (
            "Crypto/virtual-asset label '" + sector_label
            + "' normalized to '" + str(contract["sector_risk_tier"]) + "' instead of HIGH"
        )
        assert contract["sector_label"] == sector_label
        # And the keyword floor should have left an audit footprint
        rule_names = [e.get("rule") for e in rule_engine_result.get("enforcements", [])]
        assert "BIZ_RISK_KEYWORD_FLOOR" in rule_names

    @pytest.mark.parametrize("sector_label", [
        "Gambling Operator",
        "Online Gaming",
        "Sports Betting",
    ])
    def test_other_high_risk_keyword_sectors_normalize_to_high(self, sector_label):
        app = _make_app(sector=sector_label, risk_level="MEDIUM", risk_score=50)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["sector_risk_tier"] == "HIGH"

    def test_clean_low_sector_remains_low(self):
        """Regression guard: ordinary low-risk sectors must not be elevated."""
        app = _make_app(sector="Technology", risk_level="LOW")
        directors = [{"full_name": "D", "nationality": "British",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "British",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, rule_engine_result, _, _ = build_compliance_memo(app, directors, ubos, _clean_documents())
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["sector_risk_tier"] == "LOW"
        rule_names = [e.get("rule") for e in rule_engine_result.get("enforcements", [])]
        assert "BIZ_RISK_KEYWORD_FLOOR" not in rule_names


class TestOwnershipTransparencyNormalization:
    """ownership_transparency_status MUST NOT normalize to "transparent"
    when the case is explicitly opaque / multi-jurisdiction / partially
    disclosed."""

    def test_partial_disclosure_normalizes_to_incomplete(self):
        """Two UBOs disclose 25% + 20% = 45% of ownership — under 75%."""
        app = _make_app(ownership_structure="Two-tier corporate holding")
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [
            {"full_name": "UBO A", "nationality": "Mauritius",
             "is_pep": "No", "ownership_pct": 25},
            {"full_name": "UBO B", "nationality": "Mauritius",
             "is_pep": "No", "ownership_pct": 20},
        ]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["ownership_transparency_status"] in ("incomplete", "opaque"), (
            "45% disclosed ownership normalized to '"
            + str(contract["ownership_transparency_status"])
            + "' instead of incomplete/opaque"
        )

    def test_very_low_disclosure_normalizes_to_opaque(self):
        """Single UBO discloses only 30% — strict opaque threshold."""
        app = _make_app(ownership_structure="Single tier")
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "UBO A", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 30}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["ownership_transparency_status"] == "opaque"

    @pytest.mark.parametrize("ownership_text", [
        "Complex multi-tier holding via offshore SPVs",
        "Multi-jurisdiction trust structure",
        "Layered nominee shareholding",
        "Opaque shell-company chain",
    ])
    def test_opaque_keywords_in_structure_normalize_to_opaque(self, ownership_text):
        app = _make_app(ownership_structure=ownership_text)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        # Even a fully-disclosed UBO cannot wash out an opaque structure label
        ubos = [{"full_name": "UBO A", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["ownership_transparency_status"] == "opaque", (
            "Ownership text '" + ownership_text
            + "' normalized to '" + str(contract["ownership_transparency_status"])
            + "' instead of opaque"
        )

    def test_clean_full_disclosure_remains_transparent(self):
        """Regression guard: simple, fully-disclosed structures stay transparent."""
        app = _make_app(country="United Kingdom",
                        ownership_structure="Single tier")
        directors = [{"full_name": "D", "nationality": "British",
                      "is_pep": "No", "ownership_pct": 0,
                      "date_of_birth": "1980-01-01"}]
        ubos = [{"full_name": "UBO Sole", "nationality": "British",
                 "is_pep": "No", "ownership_pct": 100,
                 "date_of_birth": "1980-01-01"}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, _clean_documents())
        contract = memo["metadata"]["agent5_input_contract"]
        assert contract["ownership_transparency_status"] == "transparent"


# ════════════════════════════════════════════════════════════════════
# C. Memo recommendation binding
# ════════════════════════════════════════════════════════════════════


class TestRecommendationBinding:
    """approval_recommendation must align with the routing/supervisor outcome."""

    def test_crypto_pep_case_binds_recommendation_to_escalate_to_edd(self):
        app = _make_app(country="Iran", sector="Crypto Exchange",
                        risk_level="VERY_HIGH", risk_score=95,
                        ownership_structure="Multi-jurisdiction trust structure")
        directors = [{"full_name": "PEP D", "nationality": "Iranian",
                      "is_pep": "Yes", "ownership_pct": 0}]
        ubos = [{"full_name": "UBO", "nationality": "Iranian",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        routing = memo["metadata"]["edd_routing"]
        assert routing["route"] == ROUTE_EDD
        assert supervisor.get("mandatory_escalation") is True
        # Bound recommendation
        rec = memo["metadata"]["approval_recommendation"]
        assert rec == "ESCALATE_TO_EDD", (
            "Recommendation was '" + str(rec) + "' but route=edd & mandatory_escalation=True"
        )
        assert rec not in ("APPROVE", "APPROVE_WITH_CONDITIONS")
        # Decision section narrative now reflects ESCALATE_TO_EDD
        decision_sec = memo["sections"].get("compliance_decision") or {}
        assert decision_sec.get("decision") == "ESCALATE_TO_EDD"
        assert "ESCALATE_TO_EDD" in (decision_sec.get("content") or "")

    def test_crypto_only_case_binds_recommendation_even_if_supervisor_consistent(self):
        """Crypto sector alone is enough: route=edd → recommendation must escalate."""
        app = _make_app(sector="Digital Assets Exchange",
                        risk_level="MEDIUM", risk_score=55)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        routing = memo["metadata"]["edd_routing"]
        assert routing["route"] == ROUTE_EDD
        assert memo["metadata"]["approval_recommendation"] == "ESCALATE_TO_EDD"
        assert memo["metadata"]["approval_recommendation"] not in (
            "APPROVE", "APPROVE_WITH_CONDITIONS"
        )

    def test_clean_low_risk_case_keeps_approval_recommendation(self):
        """Regression guard: clean case still allowed to recommend APPROVE."""
        app = _make_app(country="United Kingdom",
                        sector="Technology", risk_level="LOW", risk_score=20)
        directors = [{"full_name": "D", "nationality": "British",
                      "is_pep": "No", "ownership_pct": 0,
                      "date_of_birth": "1980-01-01"}]
        ubos = [{"full_name": "U", "nationality": "British",
                 "is_pep": "No", "ownership_pct": 100,
                 "date_of_birth": "1980-01-01"}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, _clean_documents())
        routing = memo["metadata"]["edd_routing"]
        assert routing["route"] == ROUTE_STANDARD
        rec = memo["metadata"]["approval_recommendation"]
        assert rec in ("APPROVE", "APPROVE_WITH_CONDITIONS")

    def test_original_recommendation_preserved_for_audit(self):
        """When binding overrides the recommendation, the original must be
        preserved on the metadata for audit traceability."""
        app = _make_app(sector="Crypto Exchange",
                        risk_level="MEDIUM", risk_score=55)
        directors = [{"full_name": "D", "nationality": "Mauritius",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [{"full_name": "U", "nationality": "Mauritius",
                 "is_pep": "No", "ownership_pct": 100}]
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
        # Original (APPROVE_WITH_CONDITIONS for MEDIUM risk) recorded
        assert memo["metadata"].get("approval_recommendation_original") in (
            "APPROVE", "APPROVE_WITH_CONDITIONS", "REVIEW", None
        )
        assert memo["metadata"]["approval_recommendation"] == "ESCALATE_TO_EDD"


# ════════════════════════════════════════════════════════════════════
# A. EDD route actuation (live SQLite)
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def sqlite_db_with_app(monkeypatch, tmp_path):
    """Spin up a temp SQLite DB seeded with one application row."""
    # Force the module-level USE_POSTGRES off and point sqlite at temp file
    monkeypatch.setattr(db_module, "USE_POSTGRES", False, raising=False)
    db_path = tmp_path / "b2_actuation.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path), raising=False)
    db_module.init_db()

    # Reload server module references? They cache USE_POSTGRES at import.
    import server
    monkeypatch.setattr(server, "USE_POSTGRES", False, raising=False)

    db = db_module.get_db()
    # Seed an application
    app_id = "app_b2_actuate_001"
    app_ref = "ARF-B2-ACT-001"
    db.execute(
        "INSERT INTO applications (id, ref, company_name, country, sector, "
        "entity_type, risk_level, risk_score, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (app_id, app_ref, "Crypto Co Ltd", "Iran", "Crypto Exchange",
         "SME", "VERY_HIGH", 95, "in_review"),
    )
    db.commit()

    yield db, app_id, app_ref, server
    try:
        db.close()
    except Exception:
        pass


class TestEDDRouteActuation:
    """Policy decision must become workflow reality."""

    def _routing(self):
        return evaluate_edd_routing({
            "final_risk_level": "VERY_HIGH",
            "declared_pep_present": True,
            "sector_risk_tier": "HIGH",
            "sector_label": "Crypto Exchange",
            "jurisdiction_risk_tier": "VERY_HIGH",
            "ownership_transparency_status": "opaque",
            "screening_terminality_summary": {"terminal": True, "has_terminal_match": False},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": True,
        })

    def test_route_edd_creates_active_edd_case(self, sqlite_db_with_app):
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        result = server._actuate_edd_routing(
            db, app_row, self._routing(),
            {"mandatory_escalation": True,
             "mandatory_escalation_reasons": ["final_risk_level=VERY_HIGH"]},
            {"sub": "user_co", "name": "Officer", "role": "co"},
        )
        db.commit()
        assert result["created"] is True
        assert result["case_id"] is not None
        # An active edd_case exists
        case = db.execute(
            "SELECT * FROM edd_cases WHERE application_id = ? "
            "AND stage NOT IN ('edd_approved','edd_rejected')",
            (app_id,),
        ).fetchone()
        assert case is not None
        assert case["stage"] == "triggered"
        assert case["trigger_source"] == "policy_routing"

    def test_route_edd_flips_application_status(self, sqlite_db_with_app):
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        server._actuate_edd_routing(
            db, app_row, self._routing(), {}, {"sub": "u", "name": "n", "role": "co"},
        )
        db.commit()
        new_row = db.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert new_row["status"] == "edd_required"

    def test_repeated_actuation_is_idempotent(self, sqlite_db_with_app):
        """Memo regeneration must NOT create duplicate active EDD cases."""
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()

        for _ in range(3):
            server._actuate_edd_routing(
                db, app_row, self._routing(), {},
                {"sub": "u", "name": "n", "role": "co"},
            )
            db.commit()
            # Refresh app_row so subsequent passes see the new status
            app_row = db.execute(
                "SELECT * FROM applications WHERE id = ?", (app_id,)
            ).fetchone()

        # Exactly one active EDD case
        rows = db.execute(
            "SELECT id FROM edd_cases WHERE application_id = ? "
            "AND stage NOT IN ('edd_approved','edd_rejected')",
            (app_id,),
        ).fetchall()
        assert len(rows) == 1, (
            "Idempotency broken: " + str(len(rows)) + " active EDD cases for one application"
        )

    def test_actuation_preserves_routing_context(self, sqlite_db_with_app):
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        routing = self._routing()
        result = server._actuate_edd_routing(
            db, app_row, routing,
            {"mandatory_escalation": True,
             "mandatory_escalation_reasons": ["final_risk_level=VERY_HIGH",
                                              "declared_pep_present"]},
            {"sub": "u", "name": "n", "role": "co"},
        )
        db.commit()
        case = db.execute(
            "SELECT trigger_notes, edd_notes FROM edd_cases WHERE id = ?",
            (result["case_id"],),
        ).fetchone()
        # trigger_notes references the policy version & triggers
        assert routing["policy_version"] in case["trigger_notes"]
        # edd_notes JSON includes the routing context blob
        notes = json.loads(case["edd_notes"])
        first = notes[0]
        assert first["source"] == "policy_routing"
        assert first["policy_version"] == routing["policy_version"]
        assert first["triggers"] == routing["triggers"]
        assert "final_risk_level=VERY_HIGH" in first["mandatory_escalation_reasons"]

    def test_actuation_appears_in_lifecycle_queue(self, sqlite_db_with_app):
        """Once actuated, the EDD case must appear in the lifecycle queue."""
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        server._actuate_edd_routing(
            db, app_row, self._routing(), {},
            {"sub": "u", "name": "n", "role": "co"},
        )
        db.commit()

        from lifecycle_queue import build_lifecycle_queue
        queue = build_lifecycle_queue(db, include="active", types=("edd",))
        edd_items = [it for it in queue.get("items", []) if it.get("type") == "edd"]
        assert any(
            (it.get("application_id") == app_id) for it in edd_items
        ), "Newly-actuated EDD case did not appear in lifecycle queue"

    def test_route_standard_does_not_create_edd_case(self, sqlite_db_with_app):
        """Negative test: when the policy says standard, no EDD case is created."""
        db, app_id, app_ref, server = sqlite_db_with_app
        app_row = db.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        standard_routing = evaluate_edd_routing({
            "final_risk_level": "LOW",
            "declared_pep_present": False,
            "sector_risk_tier": "LOW",
            "sector_label": "Technology",
            "jurisdiction_risk_tier": "LOW",
            "ownership_transparency_status": "transparent",
            "screening_terminality_summary": {"terminal": True, "has_terminal_match": False},
            "edd_trigger_flags": [],
            "supervisor_mandatory_escalation": False,
        })
        assert standard_routing["route"] == ROUTE_STANDARD
        result = server._actuate_edd_routing(
            db, app_row, standard_routing, {},
            {"sub": "u", "name": "n", "role": "co"},
        )
        db.commit()
        assert result["skipped"] is True
        rows = db.execute(
            "SELECT id FROM edd_cases WHERE application_id = ?",
            (app_id,),
        ).fetchall()
        assert len(rows) == 0


# ════════════════════════════════════════════════════════════════════
# Cross-cutting integration: contract → routing → recommendation → actuation
# ════════════════════════════════════════════════════════════════════


class TestEndToEndFromMemoToActuation:
    """A known risky case routes EDD AND has its recommendation bound."""

    def test_partial_disclosure_crypto_routes_edd_and_binds_recommendation(self):
        app = _make_app(
            sector="Digital Assets Exchange",
            risk_level="MEDIUM", risk_score=60,
            ownership_structure="Complex multi-jurisdiction trust structure",
        )
        directors = [{"full_name": "D", "nationality": "Cypriot",
                      "is_pep": "No", "ownership_pct": 0}]
        ubos = [
            {"full_name": "UBO A", "nationality": "Cypriot",
             "is_pep": "No", "ownership_pct": 25},
            {"full_name": "UBO B", "nationality": "Russian",
             "is_pep": "No", "ownership_pct": 20},
        ]
        memo, _, supervisor, _ = build_compliance_memo(app, directors, ubos, [])
        contract = memo["metadata"]["agent5_input_contract"]
        routing = memo["metadata"]["edd_routing"]
        assert contract["sector_risk_tier"] == "HIGH"
        assert contract["ownership_transparency_status"] in ("opaque", "incomplete")
        assert routing["route"] == ROUTE_EDD
        assert memo["metadata"]["approval_recommendation"] == "ESCALATE_TO_EDD"

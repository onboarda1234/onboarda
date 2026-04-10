#!/usr/bin/env python3
"""
Onboarda — Sprint 4: Pilot Seed Data & Demo Runner
====================================================
Creates 5 realistic demo scenarios covering all risk tiers.
Deterministic: same seed data always produces the same results.

Usage:
    python demo_pilot_data.py          # Seed all 5 scenarios
    python demo_pilot_data.py --run    # Seed + run full pipeline on each
    python demo_pilot_data.py --clean  # Remove demo data
"""

import os
import sys
import json
import uuid
import logging
import secrets
from datetime import datetime

# Ensure imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", os.environ.get("SECRET_KEY", "demo-key-not-for-production"))

from db import get_db, init_db
from rule_engine import compute_risk_score
from memo_handler import build_compliance_memo
from branding import BRAND

logger = logging.getLogger("arie.demo")

# ═══════════════════════════════════════════════════════════
# DEMO SCENARIO DEFINITIONS
# ═══════════════════════════════════════════════════════════

DEMO_SCENARIOS = [
    # ── SCENARIO 1: Low-Risk Clean Company ──────────────────
    {
        "id": "demo-scenario-01",
        "ref": "ARF-2026-DEMO01",
        "title": "Low-Risk: Clean Technology Company",
        "demo_order": 1,
        "talking_points": [
            "This is a straightforward, low-risk case — the kind that should flow through quickly.",
            "Notice: single jurisdiction, transparent ownership, clean sanctions screening.",
            "The system routes this to Sonnet (cost-efficient AI) because risk is LOW.",
            "Result: APPROVE with minimal conditions. Fast Lane onboarding.",
        ],
        "application": {
            "company_name": "Meridian Software Ltd",
            "brn": "C18-042891",
            "country": "United Kingdom",
            "sector": "Technology",
            "entity_type": "SME",
            "ownership_structure": "Simple",
            "prescreening_data": json.dumps({
                "source_of_funds": "Revenue from enterprise SaaS contracts",
                "expected_volume": "GBP 85,000 monthly",
                "cross_border": False,
                "monthly_volume": "50,000-500,000",
                "introduction_method": "Direct",
                "operating_countries": ["United Kingdom"],
                "target_markets": ["United Kingdom", "Germany"],
            }),
        },
        "directors": [
            {"full_name": "James Whitfield", "nationality": "United Kingdom", "is_pep": "No"},
            {"full_name": "Sarah Chen", "nationality": "Singapore", "is_pep": "No"},
        ],
        "ubos": [
            {"full_name": "James Whitfield", "nationality": "United Kingdom", "ownership_pct": 65.0, "is_pep": "No"},
            {"full_name": "Sarah Chen", "nationality": "Singapore", "ownership_pct": 35.0, "is_pep": "No"},
        ],
        "documents": [
            {"doc_type": "certificate_of_incorporation", "doc_name": "meridian_coi.pdf", "verification_status": "verified"},
            {"doc_type": "passport", "doc_name": "whitfield_passport.pdf", "verification_status": "verified", "person_name": "James Whitfield"},
            {"doc_type": "passport", "doc_name": "chen_passport.pdf", "verification_status": "verified", "person_name": "Sarah Chen"},
            {"doc_type": "proof_of_address", "doc_name": "whitfield_utility_bill.pdf", "verification_status": "verified", "person_name": "James Whitfield"},
            {"doc_type": "proof_of_address", "doc_name": "chen_bank_statement.pdf", "verification_status": "verified", "person_name": "Sarah Chen"},
            {"doc_type": "financial_statements", "doc_name": "meridian_financials_2025.pdf", "verification_status": "verified"},
        ],
    },

    # ── SCENARIO 2: Medium-Risk Complex UBO ─────────────────
    {
        "id": "demo-scenario-02",
        "ref": "ARF-2026-DEMO02",
        "title": "Medium-Risk: Offshore Holding with Complex UBO",
        "demo_order": 2,
        "talking_points": [
            "This case shows the system handling structural complexity.",
            "Mauritius domiciliation triggers offshore jurisdiction risk (MEDIUM floor).",
            "Multi-tier UBO structure: two shareholders, one via intermediary.",
            "Watch how the rule engine enforces ownership risk floors when structure is complex.",
            "Result: APPROVE_WITH_CONDITIONS — enhanced monitoring recommended.",
        ],
        "application": {
            "company_name": "Coral Bay Holdings Ltd",
            "brn": "GBL2-2024-0731",
            "country": "Mauritius",
            "sector": "Financial Services",
            "entity_type": "Global Business Company",
            "ownership_structure": "3+",
            "prescreening_data": json.dumps({
                "source_of_funds": "Investment returns and management fees from portfolio companies",
                "expected_volume": "USD 250,000 monthly",
                "cross_border": True,
                "monthly_volume": "50,000-500,000",
                "introduction_method": "Regulated",
                "operating_countries": ["Mauritius", "South Africa", "Kenya"],
                "target_markets": ["Sub-Saharan Africa"],
                "intermediary_shareholders": [
                    {"name": "Coral Bay Trust", "jurisdiction": "Jersey"},
                ],
            }),
        },
        "directors": [
            {"full_name": "Raj Naidoo", "nationality": "South Africa", "is_pep": "No"},
            {"full_name": "Fatima Al-Rashid", "nationality": "United Arab Emirates", "is_pep": "No"},
            {"full_name": "Pierre Leclerc", "nationality": "France", "is_pep": "No"},
        ],
        "ubos": [
            {"full_name": "Raj Naidoo", "nationality": "South Africa", "ownership_pct": 45.0, "is_pep": "No"},
            {"full_name": "Fatima Al-Rashid", "nationality": "United Arab Emirates", "ownership_pct": 30.0, "is_pep": "No"},
            {"full_name": "Coral Bay Trust (Pierre Leclerc, Settlor)", "nationality": "Jersey", "ownership_pct": 25.0, "is_pep": "No"},
        ],
        "documents": [
            {"doc_type": "certificate_of_incorporation", "doc_name": "coralbay_coi.pdf", "verification_status": "verified"},
            {"doc_type": "passport", "doc_name": "naidoo_passport.pdf", "verification_status": "verified", "person_name": "Raj Naidoo"},
            {"doc_type": "passport", "doc_name": "alrashid_passport.pdf", "verification_status": "verified", "person_name": "Fatima Al-Rashid"},
            {"doc_type": "passport", "doc_name": "leclerc_passport.pdf", "verification_status": "verified", "person_name": "Pierre Leclerc"},
            {"doc_type": "trust_deed", "doc_name": "coralbay_trust_deed.pdf", "verification_status": "verified"},
            {"doc_type": "financial_statements", "doc_name": "coralbay_financials_2025.pdf", "verification_status": "pending"},
        ],
    },

    # ── SCENARIO 3: High-Risk Red Flags ─────────────────────
    {
        "id": "demo-scenario-03",
        "ref": "ARF-2026-DEMO03",
        "title": "High-Risk: PEP Exposure with Red Flags",
        "demo_order": 3,
        "talking_points": [
            "This is where the system really proves its value.",
            "A director is a declared PEP — the system flags this for enhanced due diligence.",
            "Crypto sector triggers HIGH sector risk floor automatically.",
            "Cross-border activity with high-risk operating countries.",
            "The AI routes to Opus (thorough model) because risk score exceeds 55.",
            "Result: REVIEW or APPROVE_WITH_CONDITIONS with stringent EDD requirements.",
        ],
        "application": {
            "company_name": "Atlas Digital Assets DMCC",
            "brn": "DMCC-2025-8817",
            "country": "United Arab Emirates",
            "sector": "Cryptocurrency",
            "entity_type": "Newly incorporated",
            "ownership_structure": "1-2",
            "prescreening_data": json.dumps({
                "source_of_funds": "Seed funding from venture capital; trading revenue",
                "expected_volume": "USD 500,000 monthly",
                "cross_border": True,
                "monthly_volume": "over 500,000",
                "introduction_method": "Non-regulated",
                "operating_countries": ["United Arab Emirates", "Nigeria", "Turkey"],
                "target_markets": ["Middle East", "West Africa"],
            }),
        },
        "directors": [
            {"full_name": "Hassan Osman", "nationality": "Nigeria", "is_pep": "Yes",
             "pep_declaration": json.dumps({"role": "Former Deputy Minister of Finance, Nigeria (2018-2022)", "declared": True})},
            {"full_name": "Viktor Petrov", "nationality": "Turkey", "is_pep": "No"},
        ],
        "ubos": [
            {"full_name": "Hassan Osman", "nationality": "Nigeria", "ownership_pct": 60.0, "is_pep": "Yes",
             "pep_declaration": json.dumps({"role": "Former Deputy Minister of Finance, Nigeria (2018-2022)", "declared": True})},
            {"full_name": "Viktor Petrov", "nationality": "Turkey", "ownership_pct": 40.0, "is_pep": "No"},
        ],
        "documents": [
            {"doc_type": "certificate_of_incorporation", "doc_name": "atlas_coi.pdf", "verification_status": "verified"},
            {"doc_type": "passport", "doc_name": "osman_passport.pdf", "verification_status": "verified", "person_name": "Hassan Osman"},
            {"doc_type": "passport", "doc_name": "petrov_passport.pdf", "verification_status": "verified", "person_name": "Viktor Petrov"},
            {"doc_type": "proof_of_address", "doc_name": "osman_poa.pdf", "verification_status": "pending", "person_name": "Hassan Osman"},
            {"doc_type": "source_of_wealth", "doc_name": "osman_sow_declaration.pdf", "verification_status": "pending", "person_name": "Hassan Osman"},
        ],
    },

    # ── SCENARIO 4: Edge Case — Missing Data ────────────────
    {
        "id": "demo-scenario-04",
        "ref": "ARF-2026-DEMO04",
        "title": "Edge Case: Incomplete Application with Data Gaps",
        "demo_order": 4,
        "talking_points": [
            "Real-world applications are messy. This shows how the system handles gaps.",
            "Missing source of funds, no financial statements, unverified documents.",
            "Rule 4D kicks in: 3+ data gaps escalate risk to at least MEDIUM.",
            "Rule 4E: low confidence from missing docs blocks clean APPROVE.",
            "The system generates a Request for More Information (RMI) checklist.",
            "Result: REVIEW — system identifies exactly what is missing.",
        ],
        "application": {
            "company_name": "Sunshine Trading Co",
            "brn": "",
            "country": "Mauritius",
            "sector": "Import/Export",
            "entity_type": "SME",
            "ownership_structure": "",
            "prescreening_data": json.dumps({
                "source_of_funds": "",
                "expected_volume": "",
                "cross_border": True,
                "monthly_volume": "under 50,000",
                "introduction_method": "Unsolicited",
                "operating_countries": ["Mauritius"],
                "target_markets": [],
            }),
        },
        "directors": [
            {"full_name": "Priya Ramgoolam", "nationality": "Mauritius", "is_pep": "No"},
        ],
        "ubos": [
            {"full_name": "Priya Ramgoolam", "nationality": "Mauritius", "ownership_pct": 100.0, "is_pep": "No"},
        ],
        "documents": [
            {"doc_type": "certificate_of_incorporation", "doc_name": "sunshine_coi.pdf", "verification_status": "pending"},
            {"doc_type": "passport", "doc_name": "ramgoolam_passport.pdf", "verification_status": "pending", "person_name": "Priya Ramgoolam"},
        ],
    },

    # ── SCENARIO 5: Rejection Case ──────────────────────────
    {
        "id": "demo-scenario-05",
        "ref": "ARF-2026-DEMO05",
        "title": "Rejection: Sanctioned Jurisdiction with Shell Structure",
        "demo_order": 5,
        "talking_points": [
            "The system must know when to say no. This is that case.",
            "Sanctioned country (Syria) triggers VERY_HIGH automatic floor.",
            "Shell company entity type — highest entity risk tier.",
            "Opaque ownership: nominee shareholders, no verifiable UBOs.",
            "The rule engine blocks any possibility of approval.",
            "Result: REJECT — with full audit trail explaining why.",
        ],
        "application": {
            "company_name": "Levant Global Enterprises S.A.L.",
            "brn": "CR-2024-99102",
            "country": "Syria",
            "sector": "Import/Export",
            "entity_type": "Shell",
            "ownership_structure": "Complex",
            "prescreening_data": json.dumps({
                "source_of_funds": "Not provided",
                "expected_volume": "Not provided",
                "cross_border": True,
                "monthly_volume": "over 500,000",
                "introduction_method": "Non-regulated",
                "operating_countries": ["Syria", "Lebanon", "Iraq"],
                "target_markets": ["Middle East"],
            }),
        },
        "directors": [
            {"full_name": "Nominee Director Services Ltd", "nationality": "Syria", "is_pep": "No"},
        ],
        "ubos": [
            {"full_name": "Unknown — Nominee Shareholders", "nationality": "Syria", "ownership_pct": 0, "is_pep": "No"},
        ],
        "documents": [
            {"doc_type": "certificate_of_incorporation", "doc_name": "levant_coi.pdf", "verification_status": "failed"},
        ],
    },
]


# ═══════════════════════════════════════════════════════════
# SEED FUNCTIONS
# ═══════════════════════════════════════════════════════════

def seed_demo_client(db):
    """Create a demo client account for pilot demonstrations."""
    import bcrypt
    demo_client_pw = os.environ.get("DEMO_CLIENT_PASSWORD", "")
    if not demo_client_pw:
        logger.warning("DEMO_CLIENT_PASSWORD not set — generating random demo password")
        demo_client_pw = secrets.token_urlsafe(16)
    demo_pw = bcrypt.hashpw(demo_client_pw.encode(), bcrypt.gensalt()).decode()
    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status) VALUES (?,?,?,?,?)",
        ("demo-client-001", "demo@onboarda.com", demo_pw, f"{BRAND['portal_name']} Demo Client", "active")
    )
    db.commit()
    logger.info("Demo client seeded: demo@onboarda.com (password from DEMO_CLIENT_PASSWORD env var)")


def seed_scenario(db, scenario):
    """Insert a single demo scenario into the database."""
    app = scenario["application"]
    app_id = scenario["id"]
    ref = scenario["ref"]

    # Parse prescreening data for risk scoring
    ps = json.loads(app.get("prescreening_data", "{}"))

    # Compute risk score using the real rule engine
    risk_input = {
        "entity_type": app["entity_type"],
        "ownership_structure": app.get("ownership_structure", ""),
        "country": app["country"],
        "sector": app["sector"],
        "directors": scenario["directors"],
        "ubos": scenario["ubos"],
        "cross_border": ps.get("cross_border", False),
        "monthly_volume": ps.get("monthly_volume", "under 50,000"),
        "introduction_method": ps.get("introduction_method", "Direct"),
        "operating_countries": ps.get("operating_countries", []),
        "target_markets": ps.get("target_markets", []),
        "intermediary_shareholders": ps.get("intermediary_shareholders", []),
    }
    risk = compute_risk_score(risk_input)

    # Insert application
    db.execute("""
        INSERT OR REPLACE INTO applications
        (id, ref, client_id, company_name, brn, country, sector, entity_type,
         ownership_structure, prescreening_data, risk_score, risk_level,
         risk_dimensions, onboarding_lane, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        app_id, ref, "demo-client-001",
        app["company_name"], app.get("brn", ""),
        app["country"], app["sector"], app["entity_type"],
        app.get("ownership_structure", ""),
        app.get("prescreening_data", "{}"),
        risk["score"], risk["level"],
        json.dumps(risk.get("dimensions", {})),
        risk.get("lane", "Standard Review"),
        "compliance_review",  # Ready for demo
        datetime.now().isoformat(),
        datetime.now().isoformat(),
    ))

    # Insert directors
    for d in scenario["directors"]:
        did = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT OR REPLACE INTO directors (id, application_id, full_name, nationality, is_pep, pep_declaration)
            VALUES (?,?,?,?,?,?)
        """, (did, app_id, d["full_name"], d["nationality"], d["is_pep"], d.get("pep_declaration", "")))

    # Insert UBOs
    for u in scenario["ubos"]:
        uid = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT OR REPLACE INTO ubos (id, application_id, full_name, nationality, ownership_pct, is_pep, pep_declaration)
            VALUES (?,?,?,?,?,?,?)
        """, (uid, app_id, u["full_name"], u["nationality"], u["ownership_pct"], u["is_pep"], u.get("pep_declaration", "")))

    # Insert documents
    for doc in scenario["documents"]:
        doc_id = uuid.uuid4().hex[:16]
        db.execute("""
            INSERT OR REPLACE INTO documents (id, application_id, doc_type, doc_name, file_path, verification_status)
            VALUES (?,?,?,?,?,?)
        """, (doc_id, app_id, doc["doc_type"], doc["doc_name"], f"/demo/docs/{doc['doc_name']}", doc["verification_status"]))

    db.commit()
    return risk


def seed_all_scenarios():
    """Seed all 5 demo scenarios."""
    init_db()
    db = get_db()
    seed_demo_client(db)

    print("\n" + "=" * 70)
    print(f"  {BRAND['portal_name'].upper()} — PILOT DEMO DATA SEEDING")
    print(f"  {BRAND['powered_by']}")
    print("=" * 70)

    results = {}
    for scenario in DEMO_SCENARIOS:
        risk = seed_scenario(db, scenario)
        results[scenario["id"]] = risk
        print(f"\n  [{scenario['demo_order']}] {scenario['title']}")
        print(f"      Ref:   {scenario['ref']}")
        print(f"      Risk:  {risk['level']} (score: {risk['score']})")
        print(f"      Lane:  {risk.get('lane', 'N/A')}")

    db.close()
    print("\n" + "=" * 70)
    print(f"  Seeded {len(DEMO_SCENARIOS)} scenarios. Demo client: demo@onboarda.com")
    print("=" * 70 + "\n")
    return results


def run_pipeline_on_scenarios():
    """Run the full compliance pipeline on each demo scenario and print results."""
    init_db()
    db = get_db()

    print("\n" + "=" * 70)
    print(f"  {BRAND['backoffice_name'].upper()} — FULL PIPELINE EXECUTION (DEMO)")
    print(f"  {BRAND['powered_by']}")
    print("=" * 70)

    for scenario in DEMO_SCENARIOS:
        app_id = scenario["id"]
        app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if not app_row:
            print(f"\n  [!] Scenario {app_id} not found — run seeding first")
            continue

        app = dict(app_row)
        dirs = [dict(r) for r in db.execute("SELECT * FROM directors WHERE application_id = ?", (app_id,)).fetchall()]
        uboss = [dict(r) for r in db.execute("SELECT * FROM ubos WHERE application_id = ?", (app_id,)).fetchall()]
        docs = [dict(r) for r in db.execute("SELECT * FROM documents WHERE application_id = ?", (app_id,)).fetchall()]

        # Enrich app with prescreening fields for memo_handler
        ps = json.loads(app.get("prescreening_data", "{}"))
        app["source_of_funds"] = ps.get("source_of_funds", "")
        app["expected_volume"] = ps.get("expected_volume", "")

        memo, rule_result, supervisor_result, validation_result = build_compliance_memo(app, dirs, uboss, docs)

        decision = memo.get("metadata", {}).get("approval_recommendation", "N/A")
        confidence = memo.get("metadata", {}).get("confidence_level", 0)
        val_status = validation_result.get("validation_status", "N/A")
        sup_verdict = supervisor_result.get("verdict", "N/A")
        quality = validation_result.get("quality_score", 0)
        violations = len(rule_result.get("violations", []))
        enforcements = len(rule_result.get("enforcements", []))

        print(f"\n  {'─' * 66}")
        print(f"  [{scenario['demo_order']}] {scenario['title']}")
        print(f"  {'─' * 66}")
        print(f"  Company:       {app['company_name']}")
        print(f"  Risk:          {app['risk_level']} (score: {app['risk_score']})")
        print(f"  Decision:      {decision}")
        print(f"  Confidence:    {confidence:.0%}" if isinstance(confidence, float) else f"  Confidence:    {confidence}")
        print(f"  Validation:    {val_status} (quality: {quality}/10)")
        print(f"  Supervisor:    {sup_verdict}")
        print(f"  Rule Engine:   {violations} violations, {enforcements} enforcements")

        # Store results back
        db.execute("""
            UPDATE applications SET
                status = 'compliance_review',
                updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), app_id))

        # Store memo as JSON in a companion field (if column exists) or print summary
        print(f"  Talking Points:")
        for tp in scenario["talking_points"]:
            print(f"    → {tp}")

    db.commit()
    db.close()
    print(f"\n{'=' * 70}")
    print(f"  Pipeline executed on {len(DEMO_SCENARIOS)} scenarios.")
    print(f"{'=' * 70}\n")


def clean_demo_data():
    """Remove all demo data from the database."""
    db = get_db()
    demo_ids = [s["id"] for s in DEMO_SCENARIOS]
    for did in demo_ids:
        db.execute("DELETE FROM documents WHERE application_id = ?", (did,))
        db.execute("DELETE FROM directors WHERE application_id = ?", (did,))
        db.execute("DELETE FROM ubos WHERE application_id = ?", (did,))
        db.execute("DELETE FROM applications WHERE id = ?", (did,))
    db.execute("DELETE FROM clients WHERE id = 'demo-client-001'")
    db.commit()
    db.close()
    print("Demo data cleaned.")


# ═══════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean_demo_data()
    elif "--run" in sys.argv:
        seed_all_scenarios()
        run_pipeline_on_scenarios()
    else:
        seed_all_scenarios()

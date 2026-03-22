#!/usr/bin/env python3
"""
Generate a sample compliance memo PDF for the Coral Bay Holdings (Scenario 2) demo case.
Uses the real pipeline: memo_handler → validation_engine → supervisor → pdf_generator.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "demo-key")
os.environ.setdefault("DB_PATH", "/tmp/arie_sample_memo.db")

from db import init_db, get_db, seed_initial_data
from demo_pilot_data import DEMO_SCENARIOS, seed_demo_client, seed_scenario
from memo_handler import build_compliance_memo
from pdf_generator import generate_memo_pdf
from datetime import datetime

def main():
    init_db()
    db = get_db()
    try:
        seed_initial_data(db)
        db.commit()
    except Exception:
        pass
    seed_demo_client(db)

    # Use Scenario 2 (Medium-risk) — richest demo case for a sample memo
    scenario = DEMO_SCENARIOS[1]  # Coral Bay Holdings
    seed_scenario(db, scenario)

    app_id = scenario["id"]
    app_row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    app = dict(app_row)

    dirs = [dict(r) for r in db.execute("SELECT * FROM directors WHERE application_id = ?", (app_id,)).fetchall()]
    ubos = [dict(r) for r in db.execute("SELECT * FROM ubos WHERE application_id = ?", (app_id,)).fetchall()]
    docs = [dict(r) for r in db.execute("SELECT * FROM documents WHERE application_id = ?", (app_id,)).fetchall()]

    # Enrich
    ps = json.loads(app.get("prescreening_data", "{}"))
    app["source_of_funds"] = ps.get("source_of_funds", "")
    app["expected_volume"] = ps.get("expected_volume", "")

    memo, rule_result, supervisor_result, validation_result = build_compliance_memo(app, dirs, ubos, docs)

    pdf_bytes = generate_memo_pdf(
        memo_data=memo,
        application=app,
        validation_result=validation_result,
        supervisor_result=supervisor_result,
        approved_by="Raj Patel (SCO)",
        approved_at=datetime.now().isoformat(),
    )

    output_path = "/sessions/magical-stoic-newton/mnt/Onboarda/Onboarda_Sample_Compliance_Memo.pdf"
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)

    db.close()
    print(f"Sample memo PDF generated: {output_path} ({len(pdf_bytes):,} bytes)")
    print(f"  Company:  {app['company_name']}")
    print(f"  Risk:     {app['risk_level']} ({app['risk_score']})")
    print(f"  Decision: {memo['metadata']['approval_recommendation']}")
    print(f"  Quality:  {validation_result['quality_score']}/10")

if __name__ == "__main__":
    main()

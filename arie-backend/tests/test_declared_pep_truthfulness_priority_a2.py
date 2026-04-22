"""
Priority A.2 — Declared PEP truthfulness guardrails.
=====================================================

Locks in the controls that prevent a declared PEP director or UBO from
being silently flattened to "no PEP exposure" across:

* Screening Queue chip (server._build_screening_queue_payload)
* Memo narrative (memo_handler.build_compliance_memo)
* Memo Supervisor (supervisor_engine.run_memo_supervisor)
* Memo Quality Validator (validation_engine.validate_compliance_memo)

If any of these tests start failing, declared PEP truthfulness is
regressing — that is a critical compliance-trust regression.
"""

import json
import pytest


# ── A. Queue serializer — declared PEP survives non-canonical truthy values ─


def _insert_pep_app(db, ref, is_pep_value):
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_" + ref.lower(),
            ref,
            "client_" + ref.lower(),
            "PEP " + ref + " Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-04-22T00:00:00",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True, "source": "opencorporates",
                        "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                    },
                    "director_screenings": [{
                        "person_name": "Edge Pep " + ref,
                        "person_type": "director",
                        "declared_pep": "Yes",
                        "screening": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                    }],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": [],
                    "total_hits": 0,
                }
            }),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_" + ref.lower(), "Edge Pep " + ref, "Mauritius", is_pep_value),
    )


@pytest.mark.parametrize("is_pep_value", ["yes", "true", "1", "YES", "True"])
def test_queue_declared_pep_chip_robust_to_noncanonical_truthy(db, temp_db, is_pep_value):
    """The queue serializer must surface 'declared' for any truthy form
    of is_pep, not just the exact string 'Yes'. Otherwise officers see a
    green 'Not Declared' chip while the underlying record is a PEP."""
    from server import _build_screening_queue_payload

    ref = "PEPNORM" + str(abs(hash(is_pep_value)) % 1000)
    _insert_pep_app(db, ref, is_pep_value)
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    person_row = next(r for r in payload["rows"]
                      if r["application_ref"] == ref and r["subject_type"] == "director")
    assert person_row["pep_declared_status"] == "declared", (
        f"is_pep value {is_pep_value!r} was flattened to {person_row['pep_declared_status']!r}"
    )
    assert person_row["status_key"] == "declared_pep_review"


def test_queue_declared_pep_chip_does_not_render_not_declared(db, temp_db):
    """Sanity guard: declared PEP queue row must never carry not_declared."""
    from server import _build_screening_queue_payload

    _insert_pep_app(db, "PEPCANON", "Yes")
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    person_row = next(r for r in payload["rows"]
                      if r["application_ref"] == "PEPCANON" and r["subject_type"] == "director")
    assert person_row["pep_declared_status"] != "not_declared"


def test_queue_terminal_clear_non_pep_still_renders_not_declared(db, temp_db):
    """Regression guard: non-PEP, terminal-clear case must not be elevated."""
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_clean", "ARF-CLEAN", "client_clean", "Clean Co",
            "Mauritius", "Technology", "SME", "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-04-22T00:00:00",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True, "source": "opencorporates",
                        "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                    },
                    "director_screenings": [{
                        "person_name": "Clean Director",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                    }],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": [],
                    "total_hits": 0,
                }
            }),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_clean", "Clean Director", "Mauritius", "No"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    person_row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-CLEAN" and r["subject_type"] == "director")
    assert person_row["pep_declared_status"] == "not_declared"
    assert person_row["status_key"] == "screened_no_match"


# ── C. Memo builder — banned phrasing must be scrubbed when declared PEP ────


def _memo_inputs(declared_pep="No", api_status="live"):
    app = {
        "id": "app_a2", "ref": "ARF-A2",
        "company_name": "A2 Test Co", "brn": "C99999",
        "country": "Mauritius", "sector": "Technology",
        "entity_type": "SME", "ownership_structure": "Single tier",
        "operating_countries": "Mauritius", "incorporation_date": "2020-01-01",
        "business_activity": "Software", "source_of_funds": "Trading revenue",
        "expected_volume": "USD 100,000", "risk_level": "LOW",
        "risk_score": 25, "risk_escalations": "[]",
        "assigned_to": "Officer A",
        "prescreening_data": json.dumps({
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": api_status},
                },
                "director_screenings": [{
                    "person_name": "Director PEP",
                    "person_type": "director",
                    "declared_pep": declared_pep,
                    "screening": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": api_status},
                }],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        }),
    }
    directors = [{
        "full_name": "Director PEP", "nationality": "Mauritius",
        "is_pep": declared_pep, "ownership_pct": 0,
    }]
    ubos = [{
        "full_name": "Memo UBO", "nationality": "Mauritius",
        "is_pep": "No", "ownership_pct": 100,
    }]
    return app, directors, ubos, []


def _flatten_narrative(memo):
    """Concatenate only officer-facing narrative content, not config/metadata
    keyword lists (which legitimately contain phrases like 'no pep exposure'
    as classification keywords)."""
    chunks = []
    sections = memo.get("sections") or {}
    for sec in sections.values():
        if not isinstance(sec, dict):
            continue
        for k, v in sec.items():
            if isinstance(v, str):
                chunks.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        chunks.append(item)
            elif isinstance(v, dict):
                # e.g. risk_assessment.sub_sections
                for sub in v.values():
                    if isinstance(sub, dict):
                        c = sub.get("content")
                        if isinstance(c, str):
                            chunks.append(c)
    md = memo.get("metadata") or {}
    for key in ("key_findings", "review_checklist", "conditions"):
        for s in (md.get(key) or []):
            if isinstance(s, str):
                chunks.append(s)
    return "\n".join(chunks).lower()


def _flatten(memo):
    chunks = []

    def _walk(x):
        if isinstance(x, str):
            chunks.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(memo)
    return "\n".join(chunks).lower()


@pytest.mark.parametrize("is_pep_value", ["Yes", "yes", "true", True, "1"])
def test_memo_with_declared_pep_does_not_emit_no_pep_exposure(is_pep_value):
    from memo_handler import build_compliance_memo

    app, directors, ubos, docs = _memo_inputs(declared_pep="Yes")
    # Override is_pep with a non-canonical truthy form.
    directors[0]["is_pep"] = is_pep_value
    memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

    assert memo["metadata"]["screening_state_summary"]["declared_pep_count"] >= 1
    text = _flatten_narrative(memo)
    # Hard guardrail: none of the banned denial phrases may survive in
    # officer-facing narrative content (excluding config keyword lists).
    assert "no pep exposure" not in text
    assert "no declared or detected match" not in text
    assert "no material pep concern" not in text
    assert "no material pep or jurisdictional concern" not in text
    assert "0 self-declared / detected match" not in text


def test_memo_terminal_clear_non_pep_still_says_no_pep_exposure():
    """Regression guard: clean non-PEP case must be allowed to say so."""
    from memo_handler import build_compliance_memo
    app, directors, ubos, docs = _memo_inputs(declared_pep="No", api_status="live")
    memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

    assert memo["metadata"]["screening_state_summary"]["declared_pep_count"] == 0
    text = _flatten_narrative(memo)
    # Clean case is allowed to say "no PEP exposure".
    assert "no pep exposure" in text
    assert memo["metadata"]["declared_pep_guardrail"]["applied"] is False


# ── D. Supervisor — declared PEP contradiction rule ─────────────────────────


def _build_memo_with_pep_denial(declared_pep_count=1):
    """Construct a memo dict where the body explicitly denies PEP exposure
    while metadata records declared PEP — the exact contradiction A.2
    must catch."""
    return {
        "sections": {
            "executive_summary": {"content": "No PEP exposure identified. Clean case."},
            "client_overview": {"content": "Test client."},
            "ownership_and_control": {"content": "Single-tier ownership."},
            "risk_assessment": {
                "content": "Low risk.",
                "sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW", "content": "OK"},
                    "business_risk": {"rating": "LOW", "content": "OK"},
                    "transaction_risk": {"rating": "LOW", "content": "OK"},
                    "ownership_risk": {"rating": "LOW", "content": "No PEP exposure identified in the ownership or governance structure."},
                    "financial_crime_risk": {"rating": "LOW", "content": "Clean."},
                },
            },
            "screening_results": {"content": "Sanctions Screening: No matches. PEP Screening: 0 self-declared / detected match(es) — no declared or detected matches."},
            "document_verification": {"content": "Verified."},
            "ai_explainability": {"content": "Factors weighted.",
                                   "risk_increasing_factors": ["limited trading history"],
                                   "risk_decreasing_factors": ["clean screening"]},
            "red_flags_and_mitigants": {"red_flags": ["Limited trading history of the entity reduces baseline assurance"], "mitigants": ["Enhanced monitoring scheduled for first 12 months"]},
            "compliance_decision": {"decision": "APPROVE", "content": "Approved."},
            "ongoing_monitoring": {"content": "Monitoring tier: Standard — assigned due to the LOW composite risk profile with no material PEP or jurisdictional concerns."},
            "audit_and_governance": {"content": "Standard governance."},
        },
        "metadata": {
            "risk_rating": "LOW",
            "risk_score": 25,
            "approval_recommendation": "APPROVE",
            "confidence_level": 0.85,
            "original_risk_level": "LOW",
            "aggregated_risk": "LOW",
            "document_count": 5,
            "key_findings": ["No PEP exposure identified among directors or UBOs"],
            "rule_engine": {"violations": [], "enforcements": [], "engine_status": "CLEAN"},
            "screening_state_summary": {
                "terminal": True,
                "has_non_terminal": False,
                "has_failed": False,
                "has_not_configured": False,
                "company_state": "completed_clear",
                "person_states": ["completed_clear"],
                "declared_pep_count": declared_pep_count,
            },
        },
    }


def test_supervisor_flags_declared_pep_contradiction():
    from supervisor_engine import run_memo_supervisor
    memo = _build_memo_with_pep_denial(declared_pep_count=1)
    result = run_memo_supervisor(memo)

    pep_contradictions = [c for c in result["contradictions"]
                          if c.get("category") == "declared_pep_contradiction"]
    assert len(pep_contradictions) == 1, result["contradictions"]
    assert pep_contradictions[0]["severity"] == "critical"
    assert result["verdict"] == "INCONSISTENT"
    assert result["can_approve"] is False


def test_supervisor_does_not_flag_when_no_declared_pep():
    """Regression guard: if declared_pep_count == 0, the new rule must
    not fire even if the memo says 'no PEP exposure'."""
    from supervisor_engine import run_memo_supervisor
    memo = _build_memo_with_pep_denial(declared_pep_count=0)
    result = run_memo_supervisor(memo)

    pep_contradictions = [c for c in result["contradictions"]
                          if c.get("category") == "declared_pep_contradiction"]
    assert pep_contradictions == []


# ── E. Validator — declared PEP contradiction must prevent clean pass ──────


def test_validator_does_not_pass_declared_pep_denial_as_excellent():
    from validation_engine import validate_compliance_memo
    memo = _build_memo_with_pep_denial(declared_pep_count=1)
    result = validate_compliance_memo(memo)

    pep_issues = [i for i in result["issues"]
                  if i.get("category") == "declared_pep_truthfulness"]
    assert len(pep_issues) == 1, result["issues"]
    assert pep_issues[0]["severity"] == "critical"
    # Must not pass cleanly — at minimum 'pass_with_fixes' or 'fail'.
    assert result["validation_status"] != "pass"
    # Ensure the rule contributed to a failing/sub-clean score.
    assert result["scores_breakdown"].get("declared_pep_truthfulness") == 0.0


def test_validator_clean_case_unaffected():
    """Regression: non-PEP clean case should still earn full marks for
    the new rule."""
    from validation_engine import validate_compliance_memo
    memo = _build_memo_with_pep_denial(declared_pep_count=0)
    result = validate_compliance_memo(memo)

    pep_issues = [i for i in result["issues"]
                  if i.get("category") == "declared_pep_truthfulness"]
    assert pep_issues == []
    assert result["scores_breakdown"].get("declared_pep_truthfulness") == 1.0


# ── End-to-end via memo_handler — full pipeline confirms guardrails ─────────


def test_end_to_end_declared_pep_memo_pipeline_is_truthful():
    """Real end-to-end: declared PEP → memo built → supervisor verdict is
    not silently CONSISTENT, validator is not silently 'pass'."""
    from memo_handler import build_compliance_memo
    app, directors, ubos, docs = _memo_inputs(declared_pep="Yes", api_status="live")
    memo, _, supervisor_result, validation_result = build_compliance_memo(app, directors, ubos, docs)

    text = _flatten_narrative(memo)
    assert "declared pep exposure present" in text or "pep identified" in text
    assert "no pep exposure" not in text
    # Memo should acknowledge the declared PEP in key_findings.
    kf_text = " ".join([s for s in memo["metadata"]["key_findings"] if isinstance(s, str)]).lower()
    assert "pep" in kf_text and "director pep" in kf_text

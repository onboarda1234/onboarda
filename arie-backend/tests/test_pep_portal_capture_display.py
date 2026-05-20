import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _memo_officer_text(memo):
    chunks = []
    for section in (memo.get("sections") or {}).values():
        if not isinstance(section, dict):
            continue
        for value in section.values():
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, list):
                chunks.extend([item for item in value if isinstance(item, str)])
            elif isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, dict) and isinstance(nested.get("content"), str):
                        chunks.append(nested["content"])
    metadata = memo.get("metadata") or {}
    for key in ("key_findings", "review_checklist", "conditions"):
        chunks.extend([item for item in metadata.get(key, []) if isinstance(item, str)])
    return "\n".join(chunks).lower()


def test_portal_pep_form_contains_discrete_required_fields():
    html = (REPO_ROOT / "arie-portal.html").read_text()

    assert "buildPepDeclarationPanelHTML" in html
    assert 'id="pep-prescreening-panels"' in html
    assert "renderPrescreeningPepPanels" in html
    assert "collectPepDeclarationPayload" in html
    assert "validateAllPepDeclarations" in html
    for field in (
        "pep-role-type-",
        "pep-position-title-",
        "pep-country-jurisdiction-",
        "pep-relationship-type-",
        "pep-related-name-",
        "pep-start-date-",
        "pep-end-date-",
        "pep-current-",
        "pep-source-funds-detail-",
        "pep-evidence-reference-",
        "pep-notes-",
    ):
        assert field in html


def test_portal_pep_fields_save_resume_and_submit_payload():
    html = (REPO_ROOT / "arie-portal.html").read_text()

    assert "pep_declaration: collectPepDeclarationPayload(personKey, isPep, 'director')" in html
    assert "pep_declaration: collectPepDeclarationPayload(personKey, isPep, 'ubo')" in html
    assert "restorePepDeclarationList(data.directors)" in html
    assert "restorePepDeclarationList(data.ubos)" in html
    assert "restorePepDeclarationFieldsFromPrescreening(prescreeningData)" in html
    assert "restorePepDeclarationList(psDirs)" in html
    assert "restorePepDeclarationList(psUbos)" in html
    for key in (
        "pep_role_type",
        "position_title",
        "pep_country_jurisdiction",
        "relationship_type",
        "related_pep_name",
        "start_date",
        "current_status",
        "source_of_funds_detail",
        "supporting_note_evidence",
    ):
        assert key in html


def test_backoffice_displays_client_officer_and_screening_pep_labels():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()

    assert "renderPepDeclarationDetailsHtml" in html
    assert "buildPrescreeningPartyFallback" in html
    assert "personHasDeclaredOrVerifiedPep" in html
    assert "psData.directors" in html
    assert "Structured PEP declaration" in html
    assert "Client-declared PEP" in html
    assert "Officer-verified PEP" in html
    assert "Screening-confirmed PEP" in html
    assert "PEP role/type" in html
    assert "Position/title" in html
    assert "Supporting evidence/reference" in html


def test_structured_pep_declaration_persists_in_party_json(db):
    from server import store_application_parties

    app_id = "pep-portal-app"
    db.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, country, status) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, "ARF-PEP-PORTAL", "client-pep", "Portal PEP Co", "Mauritius", "draft"),
    )
    declaration = {
        "pep_role_type": "foreign_pep",
        "position_title": "Minister of Finance",
        "pep_country_jurisdiction": "Freedonia",
        "relationship_type": "self",
        "start_date": "2020-01-01",
        "current_status": True,
        "source_of_wealth_detail": "Salary and declared public compensation.",
        "source_of_funds_detail": "Company operating funds.",
        "supporting_note_evidence": "Public register REF-123",
    }

    store_application_parties(
        db,
        app_id,
        directors=[
            {
                "person_key": "dir1",
                "first_name": "Priya",
                "last_name": "Pep",
                "full_name": "Priya Pep",
                "nationality": "Mauritius",
                "is_pep": "Yes",
                "pep_declaration": declaration,
            }
        ],
    )

    row = db.execute("SELECT is_pep, pep_declaration FROM directors WHERE application_id=?", (app_id,)).fetchone()
    stored = json.loads(row["pep_declaration"])
    assert row["is_pep"] == "Yes"
    assert stored["declared_pep"] is True
    assert stored["client_declared_pep"] is True
    assert stored["person_type"] == "director"
    assert stored["person_key"] == "dir1"
    assert stored["pep_role_type"] == "foreign_pep"
    assert stored["position_title"] == "Minister of Finance"
    assert stored["pep_country_jurisdiction"] == "Freedonia"
    assert stored["supporting_note_evidence"] == "Public register REF-123"


def test_declared_pep_drives_high_minimum_final_risk_and_edd_lane():
    from rule_engine import compute_risk_score

    result = compute_risk_score(
        {
            "entity_type": "SME / Private Company",
            "ownership_structure": "Simple - direct identifiable UBOs",
            "country": "Mauritius",
            "sector": "Software / SaaS",
            "monthly_volume": "0-50000",
            "source_of_funds": "Operating revenue",
            "directors": [{"full_name": "Priya Pep", "is_pep": "Yes", "pep_type": "foreign_pep"}],
            "ubos": [{"full_name": "Clean Owner", "is_pep": "No", "ownership_pct": 100}],
        }
    )

    assert result["declared_pep_present"] is True
    assert result["final_risk_level"] in {"HIGH", "VERY_HIGH"}
    assert result["lane"] == "EDD"
    assert "floor_rule_declared_pep" in result["escalations"]


def test_memo_includes_structured_pep_details_and_no_false_no_pep_wording():
    from memo_handler import build_compliance_memo

    app = {
        "id": "app-pep-memo",
        "ref": "ARF-PEP-MEMO",
        "company_name": "PEP Memo Co",
        "brn": "C123",
        "country": "Mauritius",
        "sector": "Software / SaaS",
        "entity_type": "SME",
        "ownership_structure": "Simple ownership",
        "operating_countries": "Mauritius",
        "incorporation_date": "2021-01-01",
        "business_activity": "Software",
        "source_of_funds": "Trading revenue",
        "expected_volume": "USD 50,000",
        "risk_level": "HIGH",
        "risk_score": 60,
        "risk_escalations": "[]",
        "prescreening_data": json.dumps(
            {
                "screening_report": {
                    "screening_mode": "live",
                    "company_screening": {"sanctions": {"matched": False, "results": [], "api_status": "live"}},
                    "director_screenings": [],
                    "ubo_screenings": [],
                    "overall_flags": [],
                    "total_hits": 0,
                }
            }
        ),
    }
    directors = [
        {
            "full_name": "Priya Pep",
            "nationality": "Mauritius",
            "date_of_birth": "1980-01-01",
            "is_pep": "Yes",
            "pep_declaration": {
                "pep_role_type": "foreign_pep",
                "position_title": "Minister of Finance",
                "pep_country_jurisdiction": "Freedonia",
                "relationship_type": "self",
                "start_date": "2020-01-01",
                "current_status": True,
                "source_of_wealth_detail": "Declared salary and investments.",
                "source_of_funds_detail": "Company operating funds.",
                "supporting_note_evidence": "Public register REF-123",
            },
        }
    ]
    memo, _, _, _ = build_compliance_memo(app, directors, [], [])
    text = _memo_officer_text(memo)

    assert "minister of finance" in text
    assert "freedonia" in text
    assert "public register ref-123" in text
    assert "source of wealth" in text
    assert "no pep exposure" not in text
    assert "no declared or detected matches" not in text


def test_pep_declaration_audit_subjects_are_non_pii():
    from server import _pep_declaration_audit_subjects

    subjects = _pep_declaration_audit_subjects(
        directors=[{"person_key": "dir1", "full_name": "Sensitive Name", "is_pep": "Yes"}],
        ubos=[{"person_key": "ubo1", "full_name": "Another Sensitive Name", "is_pep": "No"}],
    )

    assert subjects == ["director:dir1"]
    assert "Sensitive Name" not in json.dumps(subjects)

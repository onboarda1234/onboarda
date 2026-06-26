import io
import json
import zipfile


def _risk_config():
    return {
        "dimensions": [
            {"id": "D1", "weight": 30},
            {"id": "D2", "weight": 25},
            {"id": "D3", "weight": 20},
            {"id": "D4", "weight": 15},
            {"id": "D5", "weight": 10},
        ],
        "thresholds": [
            {"level": "LOW", "min": 0, "max": 39.9},
            {"level": "MEDIUM", "min": 40, "max": 54.9},
            {"level": "HIGH", "min": 55, "max": 69.9},
            {"level": "VERY_HIGH", "min": 70, "max": 100},
        ],
    }


def _low_base_dimensions():
    return {"d1": 1.2, "d2": 1.2, "d3": 1.3, "d4": 2.0, "d5": 1.49}


def _case(app_updates=None, *, risk_dimensions=None, prescreening=None, directors=None, ubos=None):
    app = {
        "id": "app-risk-pdf",
        "ref": "ARF-RISK-PDF",
        "company_name": "Risk PDF Ltd",
        "risk_score": 55.0,
        "risk_level": "HIGH",
        "base_risk_level": "LOW",
        "final_risk_level": "HIGH",
        "onboarding_lane": "EDD",
        "risk_escalations": json.dumps(["floor_rule_edd_routing"]),
        "elevation_reason_text": "EDD routing floor: deterministic routing required EDD (material_screening_concern)",
    }
    if app_updates:
        app.update(app_updates)
    return {
        "application": app,
        "prescreening": prescreening or {},
        "risk_dimensions": risk_dimensions if risk_dimensions is not None else _low_base_dimensions(),
        "risk_config": _risk_config(),
        "directors": directors if directors is not None else [
            {
                "full_name": "No PEP Director",
                "is_pep": "No",
                "pep_declaration": json.dumps({
                    "client_declared_pep": False,
                    "declared_pep": False,
                    "pep_status": "declared_no",
                }),
            }
        ],
        "ubos": ubos if ubos is not None else [],
        "intermediaries": [],
        "documents": [],
        "corrections": [],
        "screening_reviews": [],
        "memo": {},
        "audit": [],
    }


def _provider_pep_prescreening():
    return {
        "screening_report": {
            "director_screenings": [
                {
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "live",
                        "undeclared_pep": True,
                        "results": [{"name": "Possible Match", "is_pep": True}],
                    }
                }
            ],
            "ubo_screenings": [],
        }
    }


def _pdf_text(pdf_bytes):
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return pdf_bytes.decode("latin-1", errors="ignore")


def _render_text(monkeypatch, case):
    import evidence_pack_export as export

    monkeypatch.setattr(export, "weasyprint", None)
    return _pdf_text(export.render_risk_assessment(case))


def test_risk_assessment_pdf_explains_floored_risk(monkeypatch):
    text = _render_text(monkeypatch, _case())

    assert "Base numeric score" in text
    assert "12.3" in text
    assert "Base risk level" in text
    assert "LOW" in text
    assert "Floor/escalation applied" in text
    assert "Yes" in text
    assert "Final/floored score" in text
    assert "55.0" in text
    assert "Final risk classification" in text
    assert "HIGH" in text
    assert "EDD routing floor: deterministic routing required EDD" in text
    assert "material_screening_concern" in text
    assert "The base score reflects the deterministic questionnaire/dimension score before floor rules." in text


def test_provider_only_pep_unresolved_pdf_uses_screening_wording(monkeypatch):
    text = _render_text(
        monkeypatch,
        _case(
            {
                "elevation_reason_text": "",
                "risk_escalations": json.dumps(["material_screening_disposition_floor"]),
            },
            prescreening=_provider_pep_prescreening(),
        ),
    )

    assert "Unresolved provider-detected PEP / screening review required" in text
    assert "declared_pep_present" not in text
    assert "Declared PEP" not in text
    assert "Confirmed PEP" not in text


def test_risk_assessment_pdf_reports_no_floor(monkeypatch):
    text = _render_text(
        monkeypatch,
        _case({
            "risk_score": 12.3,
            "risk_level": "LOW",
            "base_risk_level": "LOW",
            "final_risk_level": "LOW",
            "onboarding_lane": "standard",
            "risk_escalations": json.dumps([]),
            "elevation_reason_text": "",
        }),
    )

    assert "Floor/escalation applied" in text
    assert "No" in text
    assert "Final/floored score" in text
    assert "12.3" in text
    assert "Final risk classification" in text
    assert "LOW" in text


def test_risk_assessment_pdf_handles_missing_base_score_and_dimensions(monkeypatch):
    text = _render_text(
        monkeypatch,
        _case(
            {
                "risk_score": 25,
                "risk_level": "LOW",
                "base_risk_level": "",
                "final_risk_level": "LOW",
                "risk_escalations": json.dumps([]),
                "elevation_reason_text": "",
            },
            risk_dimensions={},
        ),
    )

    assert "Base numeric score" in text
    assert "Not available" in text
    assert "Final risk classification" in text
    assert "LOW" in text


def test_risk_assessment_pdf_suppresses_stale_declared_pep_label(monkeypatch):
    text = _render_text(
        monkeypatch,
        _case(
            {
                "risk_escalations": json.dumps(["floor_rule_declared_pep", "floor_rule_edd_routing"]),
                "elevation_reason_text": (
                    "Declared PEP floor: at least HIGH final risk; "
                    "EDD routing floor: deterministic routing required EDD (material_screening_concern)"
                ),
            },
            prescreening={"risk_factors": "declared_pep_present; material_screening_concern"},
        ),
    )

    assert "declared_pep_present" not in text
    assert "Declared PEP floor" not in text
    assert "EDD routing floor: deterministic routing required EDD" in text
    assert "material_screening_concern" in text
    assert "material_screening_concern" in text


def test_evidence_pack_zip_risk_pdf_contains_required_rows(temp_db, monkeypatch):
    import evidence_pack_export as export
    from db import get_db

    monkeypatch.setattr(export, "weasyprint", None)
    conn = get_db()
    app_id = "app_risk_pdf_pack"
    app_ref = "ARF-RISK-PDF-PACK"
    conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    conn.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status,
             risk_level, base_risk_level, final_risk_level, risk_score, risk_dimensions,
             risk_escalations, elevation_reason_text, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            "testclient001",
            "Risk PDF Pack Ltd",
            "United Kingdom",
            "E-Commerce / Online Retail",
            "Listed Company on Regulated Exchange",
            "pre_approval_review",
            "HIGH",
            "LOW",
            "HIGH",
            55.0,
            json.dumps(_low_base_dimensions()),
            json.dumps(["floor_rule_edd_routing"]),
            "EDD routing floor: deterministic routing required EDD (material_screening_concern)",
            json.dumps(_provider_pep_prescreening()),
        ),
    )
    app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
    zip_bytes, _metadata = export.build_evidence_pack_zip(
        conn,
        app,
        {
            "export_type": "regulator",
            "reason": "Risk PDF floor explanation regression",
            "redaction_level": "full_internal",
            "include_sections": ["risk_assessment"],
        },
        {"sub": "admin001", "name": "Test Admin", "email": "admin@test.local", "role": "admin"},
    )
    conn.close()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        risk_pdf_name = next(name for name in zf.namelist() if name.endswith("03_risk_assessment.pdf"))
        text = _pdf_text(zf.read(risk_pdf_name))

    assert "Base numeric score" in text
    assert "Base risk level" in text
    assert "Floor/escalation applied" in text
    assert "Floor/escalation reason" in text
    assert "Final/floored score" in text
    assert "Final risk classification" in text

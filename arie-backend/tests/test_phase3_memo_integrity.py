import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from memo_handler import build_compliance_memo
from validation_engine import validate_compliance_memo
from tests.conftest import make_base_memo


def _terminal_screening_report(**overrides):
    report = {
        "screening_mode": "live",
        "company_screening": {
            "sanctions": {
                "api_status": "live",
                "matched": False,
                "results": [],
                "source": "sumsub",
            },
        },
        "director_screenings": [{
            "person_name": "Jane Doe",
            "screening": {
                "api_status": "live",
                "matched": False,
                "results": [],
                "source": "sumsub",
            },
        }],
        "ubo_screenings": [],
        "adverse_media_coverage": "none",
    }
    report.update(overrides)
    return report


def _app(**overrides):
    base = {
        "id": "phase3-app",
        "ref": "ARF-PHASE3",
        "company_name": "Phase 3 Test Ltd",
        "brn": "C123456",
        "country": "Mauritius",
        "sector": "Crypto Exchange",
        "entity_type": "SME",
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000 monthly",
        "ownership_structure": "Simple",
        "risk_level": "HIGH",
        "risk_score": 68,
        "assigned_to": "admin001",
        "operating_countries": "Mauritius",
        "incorporation_date": "2021-01-01",
        "business_activity": "Digital asset exchange services",
        "prescreening_data": {"screening_report": _terminal_screening_report()},
    }
    base.update(overrides)
    return base


def _directors():
    return [{"full_name": "Jane Doe", "nationality": "Mauritius", "is_pep": "No"}]


def _ubos():
    return [{"full_name": "Jane Doe", "nationality": "Mauritius", "ownership_pct": 100, "is_pep": "No"}]


def _documents():
    return [{"id": "doc1", "doc_type": "cert_inc", "verification_status": "verified"}]


def test_memo_has_deterministic_risk_evidence_and_no_false_adverse_clear():
    memo, _, _, validation = build_compliance_memo(_app(), _directors(), _ubos(), _documents())

    evidence = memo["metadata"]["risk_evidence"]
    assert evidence["jurisdiction"]["source"] == "FATF/internal jurisdiction tables"
    assert evidence["business"]["rating"] == "HIGH"
    assert evidence["business"]["matched_keywords"] == ["crypto"]
    assert "high_risk_keyword:crypto" in evidence["business"]["triggers"]
    assert evidence["financial_crime"]["adverse_media_terminal"] is False

    fc_content = memo["sections"]["risk_assessment"]["sub_sections"]["financial_crime_risk"]["content"]
    screening_content = memo["sections"]["screening_results"]["content"]
    assert "Deterministic financial-crime rating" in fc_content
    assert "Adverse Media Screening: NOT COMPLETE" in fc_content
    assert "no clean adverse-media conclusion is recorded" in screening_content
    assert "Adverse media screening returned no relevant hits" not in fc_content
    assert memo["metadata"]["adverse_media_state_summary"]["coverage"] == "none"

    caps = {cap["code"]: cap for cap in memo["metadata"]["quality_caps"]}
    assert caps["adverse_media_not_terminal"]["max_score"] == 7.5
    assert validation["quality_score"] <= 7.5
    assert validation["validation_status"] == "pass_with_fixes"


def test_terminal_adverse_media_context_allows_clean_terminal_wording():
    report = _terminal_screening_report(
        adverse_media_coverage="full",
        has_adverse_media_hit=False,
    )
    app = _app(prescreening_data={"screening_report": report})

    memo, _, _, validation = build_compliance_memo(app, _directors(), _ubos(), _documents())

    assert memo["metadata"]["adverse_media_state_summary"]["terminal"] is True
    fc_content = memo["sections"]["risk_assessment"]["sub_sections"]["financial_crime_risk"]["content"]
    assert "terminal full-coverage adverse-media review completed" in fc_content
    assert "adverse_media_not_terminal" not in {cap["code"] for cap in memo["metadata"]["quality_caps"]}
    assert validation["quality_score"] > 7.5


def test_terminal_adverse_media_hit_elevates_fincrime_evidence():
    report = _terminal_screening_report(
        adverse_media_coverage="full",
        has_adverse_media_hit=True,
    )
    app = _app(prescreening_data={"screening_report": report})

    memo, _, _, _ = build_compliance_memo(app, _directors(), _ubos(), _documents())

    fincrime = memo["metadata"]["risk_evidence"]["financial_crime"]
    assert fincrime["rating"] == "HIGH"
    assert "adverse_media_hit" in fincrime["triggers"]
    assert memo["sections"]["risk_assessment"]["sub_sections"]["financial_crime_risk"]["rating"] == "HIGH"


def test_memo_hydrates_parties_from_prescreening_when_party_tables_empty():
    app = _app(
        prescreening_data={
            "directors": [{
                "full_name": "Phase Zero Director",
                "nationality": "Mauritius",
                "is_pep": "No",
            }],
            "parties": {
                "ubos": [{
                    "full_name": "Phase Zero UBO",
                    "nationality": "Mauritius",
                    "ownership_pct": 100,
                    "is_pep": "No",
                }],
            },
            "screening_report": _terminal_screening_report(),
        }
    )

    memo, _, _, _ = build_compliance_memo(app, [], [], _documents())
    ownership = memo["sections"]["ownership_and_control"]["content"]

    assert "1 director(s) and 1 UBO(s)" in ownership
    assert "Phase Zero Director" in ownership
    assert "Phase Zero UBO" in ownership
    assert "0 director(s)" not in ownership
    assert "0 UBO(s)" not in ownership
    party_sources = memo["metadata"]["source_attribution"]["party_sources"]
    assert party_sources["directors_source"] == "prescreening_data"
    assert party_sources["ubos_source"] == "prescreening_data"


def test_memo_does_not_default_missing_canonical_risk_to_medium_50():
    app = _app(risk_level=None, risk_score=None, final_risk_level=None, final_risk_score=None)

    memo, _, _, _ = build_compliance_memo(app, _directors(), _ubos(), _documents())
    executive = memo["sections"]["executive_summary"]["content"]
    decision = memo["sections"]["compliance_decision"]["content"]
    explainability = memo["sections"]["ai_explainability"]["content"]

    assert memo["metadata"]["canonical_risk"]["available"] is False
    assert memo["metadata"]["display_risk_rating"] == "NOT_RATED"
    assert memo["metadata"]["display_risk_score"] is None
    assert "No canonical risk rating or score is recorded" in executive
    assert "MEDIUM — 50/100" not in decision
    assert "Overall risk score: 50/100" not in explainability
    assert "Not yet scored" in explainability


def test_memo_treats_score_level_mismatch_as_unrated():
    app = _app(risk_level="MEDIUM", risk_score=0, final_risk_level=None, final_risk_score=None)

    memo, _, _, _ = build_compliance_memo(app, _directors(), _ubos(), _documents())
    decision = memo["sections"]["compliance_decision"]["content"]

    assert memo["metadata"]["canonical_risk"]["available"] is False
    assert memo["metadata"]["display_risk_rating"] == "NOT_RATED"
    assert memo["metadata"]["display_risk_score"] is None
    assert "MEDIUM — 0/100" not in decision
    assert "not yet risk-rated" in memo["sections"]["executive_summary"]["content"]


def test_pdf_generator_fails_closed_without_canonical_risk(monkeypatch):
    import pdf_generator

    captured = {}

    class FakeHTML:
        def __init__(self, string):
            captured["html"] = string

        def write_pdf(self):
            return b"%PDF-fake"

    class FakeWeasyPrint:
        HTML = FakeHTML

    monkeypatch.setattr(pdf_generator, "_get_weasyprint", lambda: FakeWeasyPrint)
    memo = {
        "sections": {"executive_summary": {"content": "Legacy memo blob."}},
        "metadata": {
            "risk_rating": "MEDIUM",
            "risk_score": 50,
            "approval_recommendation": "REVIEW",
            "confidence_level": 0.7,
        },
    }

    pdf = pdf_generator.generate_memo_pdf(memo, _app())

    assert pdf == b"%PDF-fake"
    assert "NOT YET RATED" in captured["html"]
    assert "Not yet scored" in captured["html"]
    assert ">MEDIUM<" not in captured["html"]
    assert "50/100" not in captured["html"]


def test_screening_source_summary_marks_simulated_report():
    app = _app(
        prescreening_data={
            "screening_report": _terminal_screening_report(
                screening_mode="live",
                company_screening={
                    "sanctions": {
                        "api_status": "simulated",
                        "matched": False,
                        "results": [],
                        "source": "opensanctions",
                    }
                },
                director_screenings=[],
                ubo_screenings=[],
            )
        }
    )

    memo, _, _, _ = build_compliance_memo(app, _directors(), _ubos(), _documents())
    sources = memo["metadata"]["source_attribution"]["screening_sources"]

    assert sources["provider"] == "opensanctions"
    assert sources["mode"] == "simulated"
    assert "configured" not in sources.values()


def test_same_named_ubos_with_distinct_identity_are_preserved():
    app = _app(
        prescreening_data={
            "ubos": [
                {
                    "full_name": "Alex Same",
                    "nationality": "Mauritius",
                    "date_of_birth": "1980-01-01",
                    "ownership_pct": 40,
                },
                {
                    "full_name": "Alex Same",
                    "nationality": "Mauritius",
                    "date_of_birth": "1990-01-01",
                    "ownership_pct": 35,
                },
            ],
            "screening_report": _terminal_screening_report(),
        }
    )

    memo, _, _, _ = build_compliance_memo(app, _directors(), [], _documents())
    ownership = memo["sections"]["ownership_and_control"]["content"]

    assert "2 UBO(s)" in ownership
    assert memo["metadata"]["source_attribution"]["party_sources"]["ubos_count"] == 2


def test_edd_route_rewrites_executive_and_decision_recommendations():
    app = _app(
        country="Iran",
        sector="Crypto Exchange",
        risk_level="HIGH",
        risk_score=82,
        ownership_structure="Opaque nominee structure",
    )

    memo, _, _, _ = build_compliance_memo(app, _directors(), _ubos(), _documents())
    executive = memo["sections"]["executive_summary"]["content"]
    decision_section = memo["sections"]["compliance_decision"]

    assert memo["metadata"]["approval_recommendation"] == "ESCALATE_TO_EDD"
    assert "Recommendation: ESCALATE TO EDD" in executive
    assert executive.count("Recommendation:") == 1
    assert "Recommendation: APPROVAL" not in executive
    assert "APPROVAL WITH CONDITIONS" not in executive
    assert "subject to standard conditions" not in executive
    assert decision_section["decision"] == "ESCALATE_TO_EDD"
    assert "recommended for APPROVAL" not in decision_section["content"]
    assert "APPROVAL WITH CONDITIONS" not in decision_section["content"]
    assert "Enhanced Due Diligence before any approval" in decision_section["content"]


def test_validation_engine_enforces_metadata_quality_caps_on_revalidation():
    memo = make_base_memo({
        "metadata": {
            "quality_caps": [{
                "code": "screening_not_terminal",
                "max_score": 6.4,
                "severity": "warning",
                "reason": "Provider screening is not terminal.",
                "fix": "Complete screening and regenerate.",
            }],
        },
    })

    result = validate_compliance_memo(memo)

    assert result["quality_score"] == 6.4
    assert result["validation_status"] == "pass_with_fixes"
    assert any(i.get("category") == "quality_cap" for i in result["issues"])


def test_memo_fingerprint_is_stable_and_changes_on_source_input_change():
    from server import _memo_generation_fingerprint

    app = _app()
    docs = _documents()
    first = _memo_generation_fingerprint(app, _directors(), _ubos(), docs)
    reordered_app = dict(reversed(list(app.items())))
    second = _memo_generation_fingerprint(reordered_app, list(reversed(_directors())), _ubos(), docs)
    changed = _memo_generation_fingerprint(
        app,
        _directors(),
        _ubos(),
        [dict(docs[0], verification_status="pending")],
    )

    assert first == second
    assert first != changed
    assert first.startswith("memo-input-v1:")


def test_memo_fingerprint_changes_when_screening_review_changes():
    from server import _memo_generation_fingerprint

    base_review = {
        "subject_type": "company",
        "subject_name": "Phase 3 Test Ltd",
        "disposition": "clear",
        "disposition_code": "false_positive",
        "rationale": "Name-only match; no shared identifiers.",
        "updated_at": "2026-05-04T10:00:00Z",
    }
    first = _memo_generation_fingerprint(
        _app(screening_reviews=[base_review]),
        _directors(),
        _ubos(),
        _documents(),
    )
    changed = _memo_generation_fingerprint(
        _app(screening_reviews=[dict(base_review, rationale="Confirmed adverse match.")]),
        _directors(),
        _ubos(),
        _documents(),
    )

    assert first != changed


class _MemoLockDB:
    def __init__(self, *, is_postgres):
        self.is_postgres = is_postgres
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return self

    def fetchone(self):
        return {"id": "phase3-app", "ref": "ARF-PHASE3"}


def test_memo_application_loader_locks_postgres_application_row():
    from server import _locked_memo_application_row

    db = _MemoLockDB(is_postgres=True)

    row = _locked_memo_application_row(db, "ARF-PHASE3")

    assert row["ref"] == "ARF-PHASE3"
    assert len(db.statements) == 1
    assert "FOR UPDATE" in db.statements[0][0]
    assert db.statements[0][1] == ("ARF-PHASE3", "ARF-PHASE3")


def test_memo_application_loader_acquires_sqlite_write_lock_before_read():
    from server import _locked_memo_application_row

    db = _MemoLockDB(is_postgres=False)

    row = _locked_memo_application_row(db, "phase3-app")

    assert row["id"] == "phase3-app"
    assert db.statements[0] == ("BEGIN IMMEDIATE", ())
    assert "FOR UPDATE" not in db.statements[1][0]
    assert db.statements[1][1] == ("phase3-app", "phase3-app")


def test_idempotent_memo_payload_marks_reused_existing_row():
    from server import _memo_payload_if_fingerprint_unchanged

    row = {
        "id": 42,
        "version": 3,
        "memo_data": json.dumps({"metadata": {"memo_integrity_version": "phase3_v1"}}),
        "review_status": "draft",
        "validation_status": "pass_with_fixes",
        "blocked": 0,
        "block_reason": None,
        "quality_score": 7.5,
        "memo_version": "v3",
        "raw_output_hash": "memo-input-v1:abc",
        "created_at": "2026-05-01T10:00:00",
    }

    payload = _memo_payload_if_fingerprint_unchanged(row, "memo-input-v1:abc")

    assert payload["metadata"]["idempotency"]["reused_existing_memo"] is True
    assert payload["metadata"]["idempotency"]["memo_id"] == 42
    assert payload["metadata"]["quality_score"] == 7.5
    assert payload["memo_version"] == "v3"
    assert _memo_payload_if_fingerprint_unchanged(row, "memo-input-v1:other") is None


def test_idempotent_memo_payload_audit_keeps_blocked_state_visible():
    from server import _memo_payload_if_fingerprint_unchanged

    row = {
        "id": 43,
        "version": 1,
        "memo_data": json.dumps({"metadata": {"blocked": True, "block_reason": "EDD required"}}),
        "review_status": "draft",
        "validation_status": "fail",
        "blocked": 1,
        "block_reason": "EDD required",
        "quality_score": 3.5,
        "memo_version": "v1",
        "raw_output_hash": "memo-input-v1:block",
        "created_at": "2026-05-01T10:00:00",
    }

    payload = _memo_payload_if_fingerprint_unchanged(row, "memo-input-v1:block")

    assert payload["metadata"]["idempotency"]["reused_existing_memo"] is True
    assert payload["metadata"]["blocked"] is True
    assert payload["metadata"]["block_reason"] == "EDD required"
    assert payload["validation_status"] == "fail"


def test_memo_fingerprint_keeps_non_json_brace_prefixed_text_stable():
    from server import _normalise_memo_fingerprint_value

    text = "{not json} but a legitimate free-text business activity"

    assert _normalise_memo_fingerprint_value(text) == text


def test_compliance_memo_schema_has_idempotency_columns_for_real_db():
    from db import DBConnection, _get_sqlite_schema

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = DBConnection(raw)
    conn.executescript(_get_sqlite_schema())
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(compliance_memos)").fetchall()}
    conn.close()

    assert {
        "version",
        "raw_output_hash",
        "memo_version",
        "pdf_generated_at",
        "blocked",
        "block_reason",
        "quality_score",
    }.issubset(columns)

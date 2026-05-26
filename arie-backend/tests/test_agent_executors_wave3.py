"""
Wave 3 — Agent 3: FinCrime Screening Interpretation tests.

Covers all 11 workbook-aligned checks:
  Rule:   #1 Sanctions retrieval, #2 PEP retrieval, #3 Adverse media retrieval,
          #4 Exact identity disambiguation
  Hybrid: #5 Near-match disambiguation, #6 FP reduction,
          #7 Severity ranking, #11 Disposition
  AI:     #8 Media relevance, #9 Media materiality, #10 Narrative
"""
import os
import sys
import json
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from supervisor.agent_executors import (
    execute_fincrime_screening,
    _extract_screening_report,
    _retrieve_sanctions_hits,
    _retrieve_pep_hits,
    _retrieve_adverse_media_hits,
    _exact_identity_disambiguation,
    _near_match_identity_disambiguation,
    _false_positive_reduction,
    _severity_ranking,
    _screening_disposition,
    _consolidated_screening_narrative,
    _EXACT_MATCH_THRESHOLD,
    _NEAR_MATCH_LOWER,
    _FP_CONFIDENCE_THRESHOLD,
)


# ── Test fixtures ──────────────────────────────────────────


def _screening_hit(name="John Doe", matched="John Doe", score=90, sanctioned=False, pep=False, topics=None):
    return {
        "matched_name": matched,
        "match_score": score,
        "is_sanctioned": sanctioned,
        "is_pep": pep,
        "topics": topics or [],
        "countries": ["UK"],
        "sanctions_list": "UN Sanctions" if sanctioned else "",
    }


def _person_screening(name, ptype, results, undeclared_pep=False):
    return {
        "person_name": name,
        "person_type": ptype,
        "nationality": "UK",
        "screening": {"matched": len(results) > 0, "results": results, "source": "sumsub"},
        "undeclared_pep": undeclared_pep,
    }


def _make_screening_report(director_screenings=None, ubo_screenings=None, company_sanctions=None):
    return {
        "screened_at": "2026-04-01T10:00:00",
        "director_screenings": director_screenings or [],
        "ubo_screenings": ubo_screenings or [],
        "company_screening": {
            "sanctions": {"results": company_sanctions or [], "source": "sumsub"},
        },
        "overall_flags": [],
        "total_hits": 0,
    }


def _create_test_db(prescreening_data=None, directors=None, ubos=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    db = sqlite3.connect(db_path)
    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY, ref TEXT, company_name TEXT, country TEXT,
        registration_number TEXT, entity_type TEXT, ownership_structure TEXT,
        sector TEXT, risk_level TEXT, risk_score REAL, source_of_funds TEXT,
        expected_volume TEXT, client_id TEXT, status TEXT DEFAULT 'submitted',
        brn TEXT, assigned_to TEXT, prescreening_data TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE directors (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        position TEXT, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE ubos (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        ownership_pct REAL, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE intermediaries (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        entity_name TEXT, jurisdiction TEXT, ownership_pct REAL
    )""")
    db.execute("""CREATE TABLE documents (
        id TEXT PRIMARY KEY, application_id TEXT, document_type TEXT,
        filename TEXT, verification_status TEXT DEFAULT 'pending'
    )""")

    app_id = "wave3-test-001"
    ps_json = json.dumps(prescreening_data or {})
    db.execute(
        "INSERT INTO applications (id, ref, company_name, country, sector, risk_level, risk_score, prescreening_data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (app_id, "APP-W3-001", "TestCo Ltd", "United Kingdom", "Technology", "LOW", 25.0, ps_json)
    )
    for d in (directors or []):
        db.execute(
            "INSERT INTO directors (id, application_id, full_name, nationality, position, is_pep) VALUES (?, ?, ?, ?, ?, ?)",
            (d["id"], app_id, d["full_name"], d.get("nationality", "UK"), d.get("position", "Director"), d.get("is_pep", "No"))
        )
    for u in (ubos or []):
        db.execute(
            "INSERT INTO ubos (id, application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?, ?, ?, ?, ?, ?)",
            (u["id"], app_id, u["full_name"], u.get("nationality", "UK"), u.get("ownership_pct", 100.0), u.get("is_pep", "No"))
        )
    db.commit()
    db.close()
    return db_path, app_id


# ═══════════════════════════════════════════════════════════
# Check #1: Sanctions Hit Retrieval (rule)
# ═══════════════════════════════════════════════════════════

class TestSanctionsRetrieval:
    def test_no_hits(self):
        report = _make_screening_report()
        assert _retrieve_sanctions_hits(report) == []

    def test_director_sanctions_hit(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Alice", "director", [_screening_hit("Alice", "Alice", 95, sanctioned=True)])]
        )
        hits = _retrieve_sanctions_hits(report)
        assert len(hits) == 1
        assert hits[0]["person_name"] == "Alice"
        assert hits[0]["classification"] == "rule"

    def test_company_sanctions_hit(self):
        report = _make_screening_report(
            company_sanctions=[_screening_hit("BadCorp", "BadCorp", 99, sanctioned=True)]
        )
        hits = _retrieve_sanctions_hits(report)
        assert len(hits) == 1
        assert hits[0]["person_type"] == "company"

    def test_non_sanctioned_ignored(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Bob", "director", [_screening_hit("Bob", "Bob", 80, sanctioned=False)])]
        )
        assert _retrieve_sanctions_hits(report) == []


# ═══════════════════════════════════════════════════════════
# Check #2: PEP Hit Retrieval (rule)
# ═══════════════════════════════════════════════════════════

class TestPepRetrieval:
    def test_from_screening_report(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Alice", "director", [_screening_hit("Alice", "Alice", 90, pep=True)])]
        )
        hits = _retrieve_pep_hits(report, [], [])
        assert len(hits) == 1
        assert hits[0]["source"] == "screening_report"

    def test_from_stored_flag(self):
        hits = _retrieve_pep_hits(None, [{"full_name": "Bob", "is_pep": "Yes"}], [])
        assert len(hits) == 1
        assert hits[0]["source"] == "stored_pep_flag"
        assert hits[0]["match_score"] == 100.0

    def test_deduplication(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Alice", "director", [_screening_hit("Alice", "Alice", 90, pep=True)])]
        )
        hits = _retrieve_pep_hits(report, [{"full_name": "Alice", "is_pep": "Yes"}], [])
        assert len(hits) == 1  # no duplicate

    def test_undeclared_pep(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Carol", "director", [_screening_hit("Carol", "Carol", 88, pep=True)], undeclared_pep=True)]
        )
        hits = _retrieve_pep_hits(report, [], [])
        assert hits[0]["undeclared"] is True


# ═══════════════════════════════════════════════════════════
# Check #3: Adverse Media Hit Retrieval (rule)
# ═══════════════════════════════════════════════════════════

class TestAdverseMediaRetrieval:
    def test_media_hit_extracted(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Dave", "director", [_screening_hit("Dave", "Dave", 70, topics=["fraud"])])]
        )
        hits = _retrieve_adverse_media_hits(report)
        assert len(hits) == 1
        assert hits[0]["topics"] == ["fraud"]

    def test_sanctions_not_counted_as_media(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Eve", "director", [_screening_hit("Eve", "Eve", 95, sanctioned=True, topics=["sanction"])])]
        )
        hits = _retrieve_adverse_media_hits(report)
        assert len(hits) == 0

    def test_pep_not_counted_as_media(self):
        report = _make_screening_report(
            director_screenings=[_person_screening("Frank", "director", [_screening_hit("Frank", "Frank", 85, pep=True, topics=["pep"])])]
        )
        hits = _retrieve_adverse_media_hits(report)
        assert len(hits) == 0


# ═══════════════════════════════════════════════════════════
# Check #4: Exact Identity Disambiguation (rule)
# ═══════════════════════════════════════════════════════════

class TestExactDisambiguation:
    def test_high_score_exact_match_confirmed(self):
        hits = [{"person_name": "Alice", "matched_name": "Alice", "match_score": 95}]
        result = _exact_identity_disambiguation(hits, [{"full_name": "Alice"}], [])
        assert result[0]["disambiguation"] == "confirmed_exact"

    def test_low_score_auto_cleared(self):
        hits = [{"person_name": "Bob", "matched_name": "Robert X", "match_score": 30}]
        result = _exact_identity_disambiguation(hits, [{"full_name": "Bob"}], [])
        assert result[0]["disambiguation"] == "auto_cleared"

    def test_mid_score_requires_review(self):
        hits = [{"person_name": "Carol", "matched_name": "Caroline", "match_score": 65}]
        result = _exact_identity_disambiguation(hits, [{"full_name": "Carol"}], [])
        assert result[0]["disambiguation"] == "requires_review"


# ═══════════════════════════════════════════════════════════
# Check #5: Near-Match Disambiguation (hybrid)
# ═══════════════════════════════════════════════════════════

class TestNearMatchDisambiguation:
    def test_gray_zone_scored(self):
        hits = [{"person_name": "Dave", "matched_name": "Dave", "match_score": 75, "disambiguation": "requires_review"}]
        result = _near_match_identity_disambiguation(hits)
        assert result[0]["disambiguation"] == "probable_match"
        assert result[0]["disambiguation_method"] == "hybrid"

    def test_low_gray_zone_probable_fp(self):
        hits = [{"person_name": "Eve", "matched_name": "Eva", "match_score": 55, "disambiguation": "requires_review"}]
        result = _near_match_identity_disambiguation(hits)
        assert result[0]["disambiguation"] == "probable_fp"

    def test_already_resolved_skipped(self):
        hits = [{"person_name": "Frank", "matched_name": "Frank", "match_score": 95, "disambiguation": "confirmed_exact"}]
        result = _near_match_identity_disambiguation(hits)
        assert result[0]["disambiguation"] == "confirmed_exact"


# ═══════════════════════════════════════════════════════════
# Check #6: False-Positive Reduction (hybrid)
# ═══════════════════════════════════════════════════════════

class TestFalsePositiveReduction:
    def test_confirmed_separated(self):
        hits = [
            {"disambiguation": "confirmed_exact", "match_score": 95},
            {"disambiguation": "auto_cleared", "match_score": 30},
        ]
        confirmed, fps, gray = _false_positive_reduction(hits)
        assert len(confirmed) == 1
        assert len(fps) == 1
        assert len(gray) == 0

    def test_gray_zone_separated(self):
        hits = [{"disambiguation": "gray_zone", "match_score": 65}]
        confirmed, fps, gray = _false_positive_reduction(hits)
        assert len(gray) == 1

    def test_low_score_classified_as_fp(self):
        hits = [{"disambiguation": "requires_review", "match_score": 40}]
        confirmed, fps, gray = _false_positive_reduction(hits)
        assert len(fps) == 1
        assert fps[0]["fp_reason"] == "below_confidence_threshold"


# ═══════════════════════════════════════════════════════════
# Check #7: Severity Ranking (hybrid)
# ═══════════════════════════════════════════════════════════

class TestSeverityRanking:
    def test_sanctions_ranked_critical(self):
        hits = [{"is_sanctioned": True, "topics": ["sanction"], "match_score": 95}]
        ranked = _severity_ranking(hits)
        assert ranked[0]["severity"] == "CRITICAL"

    def test_pep_ranked_high(self):
        hits = [{"is_pep": True, "topics": ["pep"], "match_score": 88}]
        ranked = _severity_ranking(hits)
        assert ranked[0]["severity"] == "HIGH"

    def test_ordering_sanctions_before_pep(self):
        hits = [
            {"is_pep": True, "topics": ["pep"], "match_score": 90},
            {"is_sanctioned": True, "topics": ["sanction"], "match_score": 85},
        ]
        ranked = _severity_ranking(hits)
        assert ranked[0]["severity"] == "CRITICAL"
        assert ranked[1]["severity"] == "HIGH"


# ═══════════════════════════════════════════════════════════
# Check #11: Disposition (hybrid)
# ═══════════════════════════════════════════════════════════

class TestDisposition:
    def test_sanctions_reject(self):
        d = _screening_disposition([{"type": "sanctions"}], [], [], [])
        assert d["disposition"] == "REJECT"

    def test_gray_zone_escalate(self):
        d = _screening_disposition([], [], [{"type": "gray"}], [])
        assert d["disposition"] == "ESCALATE"

    def test_pep_edd(self):
        d = _screening_disposition([], [], [], [{"type": "pep"}])
        assert d["disposition"] == "EDD_REQUIRED"

    def test_clean_clear(self):
        d = _screening_disposition([], [], [], [])
        assert d["disposition"] == "CLEAR"
        assert d["requires_human_review"] is False


# ═══════════════════════════════════════════════════════════
# Check #10: Narrative (AI — template fallback)
# ═══════════════════════════════════════════════════════════

class TestNarrative:
    def test_clean_narrative(self):
        n = _consolidated_screening_narrative([], [], [], [], [], [], ["TestCo"], False)
        assert "no sanctions" in n.lower() or "screening clear" in n.lower()

    def test_degraded_mode_noted(self):
        n = _consolidated_screening_narrative([], [], [], [], [], [], ["TestCo"], True)
        assert "degraded" in n.lower()

    def test_sanctions_in_narrative(self):
        n = _consolidated_screening_narrative([{"person_name": "Alice"}], [], [], [{"x": 1}], [], [], ["TestCo"], False)
        assert "SANCTIONS" in n
        assert "Alice" in n


# ═══════════════════════════════════════════════════════════
# Full Executor Integration Tests
# ═══════════════════════════════════════════════════════════

class TestExecutorIntegration:
    def test_clean_screening_no_prescreening(self):
        """Degraded mode — no screening report, no PEP flags."""
        db_path, app_id = _create_test_db(
            directors=[{"id": "d1", "full_name": "Alice Director"}],
            ubos=[{"id": "u1", "full_name": "Bob Owner"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        assert result["status"] == "clean"
        assert result["screening_mode"] == "degraded"
        assert result["disposition"]["disposition"] == "CLEAR"
        assert len(result["checks_performed"]) == 11
        os.unlink(db_path)

    def test_pep_from_stored_flag(self):
        """Degraded mode with stored PEP flag."""
        db_path, app_id = _create_test_db(
            directors=[{"id": "d1", "full_name": "Alice PEP", "is_pep": "Yes"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        assert result["pep_match_found"] is True
        assert result["disposition"]["disposition"] == "EDD_REQUIRED"
        assert result["screening_mode"] == "degraded"
        os.unlink(db_path)

    def test_full_screening_with_report(self):
        """Full mode — prescreening_data has screening report."""
        report = _make_screening_report(
            director_screenings=[
                _person_screening("Alice", "director", [_screening_hit("Alice", "Alice", 92, pep=True)])
            ]
        )
        db_path, app_id = _create_test_db(
            prescreening_data={"screening_report": report},
            directors=[{"id": "d1", "full_name": "Alice"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        assert result["screening_mode"] == "full"
        assert result["pep_match_found"] is True
        assert len(result["pep_results"]) >= 1
        os.unlink(db_path)

    def test_sanctions_escalation(self):
        """Sanctions hit triggers REJECT disposition and escalation."""
        report = _make_screening_report(
            director_screenings=[
                _person_screening("Evil", "director", [_screening_hit("Evil", "Evil", 98, sanctioned=True)])
            ]
        )
        db_path, app_id = _create_test_db(
            prescreening_data={"screening_report": report},
            directors=[{"id": "d1", "full_name": "Evil"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        assert result["sanctions_match_found"] is True
        assert result["escalation_flag"] is True
        assert result["disposition"]["disposition"] == "REJECT"
        os.unlink(db_path)

    def test_checks_performed_has_11_entries(self):
        db_path, app_id = _create_test_db(
            directors=[{"id": "d1", "full_name": "Test"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        assert len(result["checks_performed"]) == 11
        classifications = [c["classification"] for c in result["checks_performed"]]
        assert classifications.count("rule") == 4
        assert classifications.count("hybrid") == 4
        assert classifications.count("ai") == 3
        os.unlink(db_path)

    def test_output_has_required_fields(self):
        db_path, app_id = _create_test_db(
            directors=[{"id": "d1", "full_name": "Test"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        for key in ("status", "confidence_score", "findings", "evidence",
                     "sanctions_results", "pep_results", "adverse_media_results",
                     "sanctions_match_found", "pep_match_found", "adverse_media_found",
                     "screened_entities", "screening_provider", "screening_date",
                     "screening_mode", "checks_performed", "disposition", "narrative",
                     "false_positive_assessment", "confirmed_hits"):
            assert key in result, f"Missing key: {key}"
        os.unlink(db_path)

    def test_false_positive_assessment_structure(self):
        db_path, app_id = _create_test_db(
            directors=[{"id": "d1", "full_name": "Test"}],
        )
        result = execute_fincrime_screening(app_id, {"db_path": db_path})
        fpa = result["false_positive_assessment"]
        assert "total_raw_hits" in fpa
        assert "confirmed" in fpa
        assert "cleared_as_fp" in fpa
        assert "gray_zone" in fpa
        assert fpa["classification"] == "hybrid"
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# prescreening_data Extraction Tests
# ═══════════════════════════════════════════════════════════

class TestExtractScreeningReport:
    def test_valid_json_string(self):
        report = {"screening_report": {"total_hits": 0}}
        app = {"prescreening_data": json.dumps(report)}
        assert _extract_screening_report(app) == {"total_hits": 0}

    def test_dict_input(self):
        app = {"prescreening_data": {"screening_report": {"total_hits": 1}}}
        assert _extract_screening_report(app)["total_hits"] == 1

    def test_empty_string(self):
        assert _extract_screening_report({"prescreening_data": ""}) is None

    def test_no_screening_report_key(self):
        app = {"prescreening_data": json.dumps({"pricing": {}})}
        assert _extract_screening_report(app) is None

    def test_missing_field(self):
        assert _extract_screening_report({}) is None

"""SRP-3 Phase A — RegMind triage score + server-side triage foundations.

Charter (founder): reliable, clean, accurate, officer-friendly. The score is
ORDERING/BANDING ONLY — it never suppresses hits and never feeds risk
scoring, gates, or memos. These tests pin:
* deterministic, versioned scoring with severity ordering
  (sanctions > PEP 1-2 > PEP other > watchlist > adverse media);
* exact-name strength dominating pass-based fallbacks;
* per-hit persistence on both stored shapes;
* DOB shape normalisation for matched profiles (previously a raw string DOB
  failed validation and silently dropped the whole profile);
* server-side row triage: buckets always sum to total, unscored hits are
  reported as unscored (never silently bucketed weak), top hits ranked;
* the frozen Application page is untouched (no triage in its handler,
  stored reports never mutated by queue reads).
"""

import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_complyadvantage.normalizer import (
    MergedMatch,
    TRIAGE_SCORE_VERSION,
    _legacy_screening_result_from_match,
    _provider_match,
    compute_triage_score,
)
from screening_complyadvantage.models.output import (
    CAMediaArticleValue,
    CAMediaIndicator,
    CAPEPIndicator,
    CAPEPValue,
    CAProfile,
    CAProfileCompany,
    CARiskDetail,
    CARiskDetailInner,
    CARiskType,
    CASanctionIndicator,
    CASanctionValue,
    CAWatchlistIndicator,
    CAWatchlistValue,
)
from screening_complyadvantage.orchestrator import (
    _adapt_mesh_alert_profile,
    _adapt_profile_subject,
    _normalise_profile_date_of_birth,
)


def _risk_one(key, label):
    rt = CARiskType(key=key, label=label, name=label)
    if key.startswith("r_pep") or key == "r_rca":
        indicator = CAPEPIndicator(risk_type=rt, value=CAPEPValue.model_validate({"class": key.replace("r_pep_class_", "PEP_CLASS_")}))
    elif key.startswith("r_adverse_media"):
        indicator = CAMediaIndicator(risk_type=rt, value=CAMediaArticleValue.model_validate({"title": "Article"}))
    elif key in ("r_watchlist",):
        indicator = CAWatchlistIndicator(risk_type=rt, value=CAWatchlistValue.model_validate({"list_name": "List"}))
    else:
        indicator = CASanctionIndicator(risk_type=rt, value=CASanctionValue.model_validate({"program": "P"}))
    return CARiskDetail(values=[CARiskDetailInner(risk_type=rt, indicators=[indicator])])


def _profile(match_types=None, media=False):
    data = {
        "identifier": "p1",
        "company": CAProfileCompany(),
        "risk_types": [],
        "risk_indicators": [],
    }
    profile = CAProfile(**data)
    if match_types is not None:
        profile.provider_match_types = list(match_types)
    if media:
        profile.provider_media_evidence = [{"url": "https://x", "title": "T"}]
    return profile


def _match(risk, *, surfaced="strict", match_types=None, media=False):
    return MergedMatch(
        risk=risk,
        surfaced_by_pass=surfaced,
        profile=_profile(match_types=match_types, media=media),
        profile_identifier="p1",
        risk_id="r1",
        alert_id="a1",
    )


class TestTriageScorer:
    def test_deterministic_and_versioned(self):
        match = _match(_risk_one("r_pep_class_1", "PEP Class 1"), match_types=["name_exact"])
        rollups = {"has_pep_hit": True}
        first = compute_triage_score(match, rollups)
        second = compute_triage_score(match, rollups)
        assert first == second
        assert first["version"] == TRIAGE_SCORE_VERSION == "rts-1.0"
        assert first["reasons"]
        assert 1 <= first["score"] <= 100

    def test_severity_ordering(self):
        sanctions = compute_triage_score(
            _match(_risk_one("r_direct_sanctions_exposure", "Sanctions"), surfaced="relaxed"),
            {"has_sanctions_hit": True},
        )["score"]
        pep12 = compute_triage_score(
            _match(_risk_one("r_pep_class_1", "PEP Class 1"), surfaced="relaxed"),
            {"has_pep_hit": True},
        )["score"]
        pep34 = compute_triage_score(
            _match(_risk_one("r_pep_class_4", "PEP Class 4"), surfaced="relaxed"),
            {"has_pep_hit": True},
        )["score"]
        watchlist = compute_triage_score(
            _match(_risk_one("r_watchlist", "Watchlist"), surfaced="relaxed"), {}
        )["score"]
        media = compute_triage_score(
            _match(_risk_one("r_adverse_media_fraud", "Adverse media"), surfaced="relaxed"),
            {"has_adverse_media_hit": True},
        )["score"]
        assert sanctions > pep12 > pep34 > watchlist > media

    def test_exact_name_beats_pass_fallbacks(self):
        risk = _risk_one("r_pep_class_3", "PEP Class 3")
        rollups = {"has_pep_hit": True}
        exact = compute_triage_score(_match(risk, surfaced="relaxed", match_types=["name_exact"]), rollups)
        strict = compute_triage_score(_match(risk, surfaced="strict"), rollups)
        relaxed = compute_triage_score(_match(risk, surfaced="relaxed"), rollups)
        assert exact["score"] > strict["score"] > relaxed["score"]
        assert "exact name match" in exact["reasons"]

    def test_media_evidence_and_rca_add_weight(self):
        risk = _risk_one("r_adverse_media_fraud", "Adverse media")
        base = compute_triage_score(_match(risk), {"has_adverse_media_hit": True})
        with_media = compute_triage_score(_match(risk, media=True), {"has_adverse_media_hit": True})
        with_rca = compute_triage_score(_match(risk), {"has_adverse_media_hit": True, "is_rca": True})
        assert with_media["score"] == base["score"] + 8
        assert with_rca["score"] == base["score"] + 6

    def test_uncategorized_floor(self):
        result = compute_triage_score(_match(CARiskDetail(), surfaced="relaxed"), {})
        assert result["score"] >= 1
        assert "uncategorized provider match" in result["reasons"]

    def test_persisted_on_both_stored_shapes(self):
        match = _match(_risk_one("r_pep_class_1", "PEP Class 1"), match_types=["name_exact"])
        rollups = {"has_pep_hit": True}
        row = _legacy_screening_result_from_match(match, rollups)
        assert row["triage_score"] == compute_triage_score(match, rollups)["score"]
        assert row["triage_score_version"] == TRIAGE_SCORE_VERSION
        assert row["triage_score_reasons"]
        provider = _provider_match(match, include_surfaced_by_pass=True)
        assert provider["triage"]["score"] == row["triage_score"]
        assert provider["triage"]["version"] == TRIAGE_SCORE_VERSION


class TestDobNormalisation:
    def test_forms(self):
        assert _normalise_profile_date_of_birth("1961-03-04") == {
            "year": 1961, "month": 3, "day": 4, "date": "1961-03-04",
        }
        assert _normalise_profile_date_of_birth("1961") == {"year": 1961}
        assert _normalise_profile_date_of_birth(1961) == {"year": 1961}
        assert _normalise_profile_date_of_birth({"year": 1961, "month": 3}) == {"year": 1961, "month": 3}
        assert _normalise_profile_date_of_birth(["unknown", "1961-03-04"]) == {
            "year": 1961, "month": 3, "day": 4, "date": "1961-03-04",
        }
        assert _normalise_profile_date_of_birth("unknown") is None
        assert _normalise_profile_date_of_birth(None, "", []) is None

    def test_person_subject_normalised(self):
        subject = _adapt_profile_subject(
            {"names": {"values": [{"name": "G Murphy"}]}, "date_of_birth": "1961-03-04"},
            company=False,
        )
        assert subject["date_of_birth"] == {"year": 1961, "month": 3, "day": 4, "date": "1961-03-04"}

    def test_string_dob_no_longer_drops_profile(self):
        """Regression: a raw string DOB previously failed CAProfile validation
        and silently dropped the entire matched profile."""
        adapted = _adapt_mesh_alert_profile("r1", {
            "identifier": "prof-1",
            "matching_name": "Gerard Murphy",
            "person": {"names": {"values": [{"name": "Gerard Murphy"}]}, "date_of_birth": "1961-03-04"},
        })
        profile = CAProfile.model_validate(adapted)
        assert profile.person.date_of_birth.year == 1961

    def test_unparseable_dob_omitted_not_guessed(self):
        adapted = _adapt_mesh_alert_profile("r1", {
            "identifier": "prof-1",
            "matching_name": "Gerard Murphy",
            "person": {"names": {"values": [{"name": "Gerard Murphy"}]}, "date_of_birth": "circa nineteen-sixty"},
        })
        profile = CAProfile.model_validate(adapted)
        assert profile.person.date_of_birth is None


class TestServerRowTriage:
    def _row(self, items):
        return {"screening_evidence": {"items": items}}

    def test_buckets_sum_to_total_and_top_ranked(self):
        import server

        items = [
            {"category": "Sanctions", "matched_name": "A", "triage_score": 75, "triage_score_reasons": ["sanctions list match"]},
            {"category": "PEP", "matched_name": "B", "triage_score": 92, "triage_score_reasons": ["PEP class 1-2", "exact name match"]},
            {"category": "Adverse Media", "matched_name": "C", "triage_score": 21},
            {"category": "Watchlist", "matched_name": "D"},
            {"category": "Something Else", "matched_name": "E", "triage_score": 12},
        ]
        triage = server._screening_queue_row_triage(self._row(items))
        assert triage["version"] == "rts-1.0"
        assert triage["total"] == 5
        assert sum(triage["buckets"].values()) == triage["total"]
        assert triage["buckets"] == {"sanctions": 1, "pep": 1, "adverse_media": 1, "watchlist": 1, "other": 1}
        assert triage["unscored_count"] == 1          # D has no score — reported, not bucketed weak
        assert triage["weak_count"] == 2              # C (21) and E (12) under threshold 40
        assert [hit["name"] for hit in triage["top_hits"]] == ["B", "A", "C", "E"]

    def test_empty_row(self):
        import server

        triage = server._screening_queue_row_triage({})
        assert triage["total"] == 0
        assert sum(triage["buckets"].values()) == 0
        assert triage["top_hits"] == []

    def test_queue_payload_attaches_triage_only_in_evidence_mode(self, temp_db):
        import server
        from db import get_db

        db = get_db()
        try:
            db.execute("DELETE FROM applications WHERE ref = ?", ("ARF-SRP3A-001",))
            report = {
                "provider": "complyadvantage", "screened_at": "2026-07-17T00:00:00Z",
                "screening_mode": "live", "total_hits": 1,
                "company_screening": {
                    "provider": "complyadvantage", "api_status": "live", "matched": True,
                    "sanctions": {"api_status": "live", "matched": True, "results": [{
                        "name": "Sanctioned Entity Ltd",
                        "profile_identifier": "019f0000-0000-7000-8000-00000000a001",
                        "match_categories": ["sanctions"],
                        "triage_score": 75, "triage_score_version": "rts-1.0",
                        "triage_score_reasons": ["sanctions list match", "exact name match"],
                    }]},
                },
                "director_screenings": [], "ubo_screenings": [], "intermediary_screenings": [],
                "overall_flags": ["sanctions"],
            }
            db.execute(
                """INSERT INTO applications
                   (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("srp3a000000000001", "ARF-SRP3A-001", "testclient001", "SRP3A Co Ltd",
                 "Mauritius", "Technology", "SME", "in_review",
                 json.dumps({"screening_report": report}), False),
            )
            db.commit()
            stored_before = db.execute(
                "SELECT prescreening_data FROM applications WHERE id = ?", ("srp3a000000000001",)
            ).fetchone()["prescreening_data"]

            user = {"type": "officer", "sub": "admin001"}
            common = dict(show_fixtures=True, limit=50, offset=0, filters={"application_ref": "ARF-SRP3A-"})
            full = server._build_screening_queue_payload(db, user, include_evidence=True, **common)
            assert full["rows"], "expected the seeded subject row"
            triage = full["rows"][0]["triage"]
            assert sum(triage["buckets"].values()) == triage["total"]
            assert triage["buckets"]["sanctions"] >= 1
            assert any(hit["score"] == 75 for hit in triage["top_hits"])

            summary = server._build_screening_queue_payload(db, user, include_evidence=False, **common)
            assert all("triage" not in row for row in summary["rows"])

            # Queue reads never mutate the stored report (frozen-page safety).
            stored_after = db.execute(
                "SELECT prescreening_data FROM applications WHERE id = ?", ("srp3a000000000001",)
            ).fetchone()["prescreening_data"]
            assert stored_after == stored_before
        finally:
            db.close()


class TestFrozenApplicationPageUntouched:
    def test_application_detail_handler_has_no_triage_reference(self):
        import server

        source = inspect.getsource(server.ApplicationDetailHandler)
        assert "triage" not in source.lower()

    def test_scorer_never_feeds_risk_engine(self):
        import rule_engine

        source = inspect.getsource(rule_engine)
        assert "triage_score" not in source

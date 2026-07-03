"""PR-AGENT3-HIT-LEVEL-OUTPUT-1

Unit tests for the hit-level interpretation rows exposed by Agent 3.
These exercise the pure builder (`_agent3_build_screening_interpretation`)
against stored screening results only — no provider calls, no server, no
mutation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


def _ts():
    # Fixed timestamp so repeated builds get byte-identical input (hash stability).
    return "2026-06-30T09:58:18+00:00"


def _app():
    return {"id": "app-hit-1", "ref": "ARF-2026-HIT01", "company_name": "Hit Level Co Ltd"}


def _prescreening_with_hits():
    """A stored screening_report covering every classification branch."""
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 4,
            "overall_flags": ["sanctions", "pep", "adverse_media"],
            "company_screening": {
                "company_name": "Hit Level Co Ltd",
                "matched": True,
                "results": [
                    # sanctions, high score -> high_confidence_match
                    {
                        "name": "Hit Level Holdings",
                        "match_score": 96,
                        "surfaced_by_pass": "both",
                        "category": "sanctions",
                        "sanctions_list": "OFAC SDN",
                        "id": "prov-ref-1",
                    },
                    # adverse media, mid score -> needs_review
                    {
                        "name": "Hit Level adverse media article",
                        "match_score": 72,
                        "surfaced_by_pass": "strict",
                        "category": "adverse_media",
                        "list": "Global Media",
                        "source_url": "https://news.example/hit-level",
                        "source_title": "Hit Level adverse media article",
                        "source_name": "Example News",
                        "media_snippet": "Stored adverse media source text.",
                    },
                    # watchlist, low score -> likely_false_positive
                    {
                        "name": "Unrelated Hitt Level",
                        "match_score": 33,
                        "surfaced_by_pass": "relaxed",
                        "category": "watchlist",
                    },
                    # no numeric score, but strict pass evidence -> needs_review
                    {
                        "name": "No Score Entity",
                        "surfaced_by_pass": "strict",
                        "category": "pep",
                    },
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }


def _build(app=None, prescreening=None, reviews=None):
    return server._agent3_build_screening_interpretation(
        app or _app(),
        prescreening if prescreening is not None else _prescreening_with_hits(),
        reviews or [],
        declared_pep_subjects=[],
    )


def test_hit_rows_present_and_shaped():
    out = _build()
    assert out is not None
    rows = out["hit_rows"]
    assert isinstance(rows, list) and len(rows) == 4
    required = {
        "index", "subject_name", "subject_type", "matched_entity", "provider",
        "list", "categories", "type", "match_score", "surfaced_by_pass", "suggested_status",
        "reason", "evidence_ref",
    }
    for row in rows:
        assert required.issubset(row.keys()), f"missing keys: {required - set(row.keys())}"
        assert row["provider"] == "complyadvantage"
        assert isinstance(row["reason"], str) and row["reason"]


def test_hit_row_status_classification():
    rows = _build()["hit_rows"]
    by_entity = {r["matched_entity"]: r for r in rows}

    assert by_entity["Hit Level Holdings"]["suggested_status"] == server.AGENT3_HIT_STATUS_HIGH_CONFIDENCE
    assert by_entity["Hit Level Holdings"]["suggested_status"] == "high_confidence_match"
    assert "officer identity verification required" in by_entity["Hit Level Holdings"]["reason"].lower()
    assert by_entity["Hit Level Holdings"]["type"] == "sanctions"
    assert by_entity["Hit Level Holdings"]["list"] == "OFAC SDN"
    assert by_entity["Hit Level Holdings"]["evidence_ref"] == "prov-ref-1"
    assert by_entity["Hit Level Holdings"]["surfaced_by_pass"] == "both"

    assert by_entity["Hit Level adverse media article"]["suggested_status"] == server.AGENT3_HIT_STATUS_NEEDS_REVIEW
    assert by_entity["Hit Level adverse media article"]["surfaced_by_pass"] == "strict"
    assert by_entity["Hit Level adverse media article"]["evidence_url"] == "https://news.example/hit-level"
    assert by_entity["Hit Level adverse media article"]["evidence_title"] == "Hit Level adverse media article"
    assert by_entity["Hit Level adverse media article"]["evidence_source"] == "Example News"
    assert "Stored adverse media" in by_entity["Hit Level adverse media article"]["evidence_snippet"]
    assert by_entity["Unrelated Hitt Level"]["suggested_status"] == server.AGENT3_HIT_STATUS_LIKELY_FP
    assert by_entity["Unrelated Hitt Level"]["surfaced_by_pass"] == "relaxed"
    assert by_entity["No Score Entity"]["suggested_status"] == server.AGENT3_HIT_STATUS_NEEDS_REVIEW
    assert by_entity["No Score Entity"]["match_score"] is None
    assert by_entity["No Score Entity"]["surfaced_by_pass"] == "strict"
    assert "strict pass" in by_entity["No Score Entity"]["reason"].lower()
    # synthetic evidence ref when the provider gave none
    assert by_entity["No Score Entity"]["evidence_ref"].startswith("stored-hit-")


def test_status_aligned_with_panel_low_confidence_threshold():
    # A hit in the [50, 70) band is low-confidence at the panel level, so a
    # non-risk hit there must be likely_false_positive (not needs_review).
    status, reason = server._agent3_hit_status_and_reason("watchlist", 60)
    assert status == server.AGENT3_HIT_STATUS_LIKELY_FP
    assert "60%" in reason
    # Exactly at the threshold -> needs review.
    assert server._agent3_hit_status_and_reason("watchlist", 70)[0] == server.AGENT3_HIT_STATUS_NEEDS_REVIEW


def test_low_score_risk_hit_never_downgraded_to_false_positive():
    # The panel always escalates risk categories (PEP -> EDD, adverse -> review,
    # sanctions -> reject), so a low-score risk hit must NOT be labelled a likely
    # false positive — that would contradict the panel disposition.
    for risk_type in ("pep", "adverse_media", "sanctions"):
        status, _ = server._agent3_hit_status_and_reason(risk_type, 40)
        assert status == server.AGENT3_HIT_STATUS_NEEDS_REVIEW, risk_type


def test_hit_row_status_counts_match_rows():
    out = _build()
    counts = out["hit_row_status_counts"]
    assert counts[server.AGENT3_HIT_STATUS_HIGH_CONFIDENCE] == 1
    assert counts[server.AGENT3_HIT_STATUS_NEEDS_REVIEW] == 2
    assert counts[server.AGENT3_HIT_STATUS_LIKELY_FP] == 1
    assert counts.get(server.AGENT3_HIT_STATUS_UNAVAILABLE, 0) == 0
    assert sum(counts.values()) == len(out["hit_rows"])


def test_hit_rows_deterministic_and_hash_stable():
    a = _build()
    b = _build()
    assert a["hit_rows"] == b["hit_rows"]
    # output_hash excludes generated_at; identical input -> identical hash
    assert a["output_hash"] == b["output_hash"]


def test_no_screening_report_yields_no_interpretation():
    # Degraded/no-report path is unchanged: returns None (no hit rows fabricated).
    assert _build(prescreening={}) is None


def test_clean_report_has_empty_hit_rows():
    clean = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 0,
            "overall_flags": [],
            "company_screening": {"company_name": "Hit Level Co Ltd", "matched": False, "results": []},
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }
    out = _build(prescreening=clean)
    assert out is not None
    assert out["hit_rows"] == []
    assert out["hit_row_status_counts"] == {}
    assert out["recommended_disposition"] == server.AGENT3_NO_REPORTABLE_HIT_RECOMMENDATION
    assert out["hit_counts"]["total"] == 0


def test_existing_hit_counts_regression():
    # Adding hit rows must not disturb the existing aggregate counts.
    out = _build()
    counts = out["hit_counts"]
    assert counts["total"] == 4
    assert counts["sanctions"] == 1
    assert counts["pep"] == 1
    assert counts["adverse_media"] == 1


def test_builder_is_pure_no_provider_clients(monkeypatch):
    # The builder must not touch any provider/network client. Poison the ones
    # that would matter and confirm the build still succeeds from stored data.
    for attr in ("run_full_screening", "ComplyAdvantageClient", "SumsubClient"):
        if hasattr(server, attr):
            # Bind attr at definition time (default arg) so the assertion
            # message names the symbol actually invoked, not the last one.
            monkeypatch.setattr(
                server, attr,
                lambda *a, _name=attr, **k: (_ for _ in ()).throw(AssertionError(f"{_name} must not be called")),
                raising=True,
            )
    out = _build()
    assert out is not None and len(out["hit_rows"]) == 4


def test_hit_rows_preserve_provider_pass_without_numeric_score():
    uuid_name = "019f185a-2a5d-7bfb-a85b-c1cfad8e5c5d"
    prescreening = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 3,
            "overall_flags": ["watchlist"],
            "company_screening": {
                "company_name": "Hit Level Co Ltd",
                "matched": True,
                "results": [
                    {"matched_name": uuid_name, "category": "watchlist", "surfaced_by_pass": "strict"},
                    {"name": "Relaxed Only", "category": "watchlist", "surfaced_by_pass": "relaxed"},
                    {"name": "Both Passes", "category": "watchlist", "surfaced_by_pass": "both"},
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }

    rows = _build(prescreening=prescreening)["hit_rows"]
    by_entity = {row["matched_entity"]: row for row in rows}
    assert by_entity[uuid_name]["match_score"] is None
    assert by_entity[uuid_name]["surfaced_by_pass"] == "strict"
    assert by_entity[uuid_name]["suggested_status"] == server.AGENT3_HIT_STATUS_NEEDS_REVIEW
    assert "strict pass" in by_entity[uuid_name]["reason"].lower()
    assert by_entity["Relaxed Only"]["surfaced_by_pass"] == "relaxed"
    assert "relaxed pass" in by_entity["Relaxed Only"]["reason"].lower()
    assert by_entity["Both Passes"]["surfaced_by_pass"] == "both"
    assert "strict + relaxed" in by_entity["Both Passes"]["reason"].lower()

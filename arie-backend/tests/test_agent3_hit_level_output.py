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
                        "category": "sanctions",
                        "sanctions_list": "OFAC SDN",
                        "id": "prov-ref-1",
                    },
                    # adverse media, mid score -> needs_review
                    {
                        "name": "Hit Level adverse media article",
                        "match_score": 72,
                        "category": "adverse_media",
                        "list": "Global Media",
                    },
                    # watchlist, low score -> likely_false_positive
                    {
                        "name": "Unrelated Hitt Level",
                        "match_score": 33,
                        "category": "watchlist",
                    },
                    # no score -> unavailable
                    {
                        "name": "No Score Entity",
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
        "list", "categories", "type", "match_score", "suggested_status",
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

    assert by_entity["Hit Level adverse media article"]["suggested_status"] == server.AGENT3_HIT_STATUS_NEEDS_REVIEW
    assert by_entity["Unrelated Hitt Level"]["suggested_status"] == server.AGENT3_HIT_STATUS_LIKELY_FP
    assert by_entity["No Score Entity"]["suggested_status"] == server.AGENT3_HIT_STATUS_UNAVAILABLE
    assert by_entity["No Score Entity"]["match_score"] is None
    # synthetic evidence ref when the provider gave none
    assert by_entity["No Score Entity"]["evidence_ref"].startswith("stored-hit-")


def test_hit_row_status_counts_match_rows():
    out = _build()
    counts = out["hit_row_status_counts"]
    assert counts[server.AGENT3_HIT_STATUS_HIGH_CONFIDENCE] == 1
    assert counts[server.AGENT3_HIT_STATUS_NEEDS_REVIEW] == 1
    assert counts[server.AGENT3_HIT_STATUS_LIKELY_FP] == 1
    assert counts[server.AGENT3_HIT_STATUS_UNAVAILABLE] == 1
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
    # existing behaviour preserved
    assert out["recommended_disposition"] == "Clear"
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
            monkeypatch.setattr(
                server, attr,
                lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"{attr} must not be called")),
                raising=True,
            )
    out = _build()
    assert out is not None and len(out["hit_rows"]) == 4

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


def _row(name, *, category=None, source_ref=None, **extra):
    row = {
        "name": name,
        "match_score": None,
        "surfaced_by_pass": "strict",
    }
    if category is not None:
        row["category"] = category
    if source_ref:
        row["id"] = source_ref
    row.update(extra)
    return row


def _rows(prefix, count, *, category=None):
    return [
        _row(f"{prefix} {idx + 1}", category=category, source_ref=f"{prefix.lower().replace(' ', '-')}-{idx + 1}")
        for idx in range(count)
    ]


def _arf_920615_like_prescreening(*, total_hits=88):
    """Multi-subject fixture mirroring the count shape from ARF-2026-920615."""
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": total_hits,
            "overall_flags": ["pep"],
            "company_screening": {
                "company_name": "ALLICA BANK LIMITED",
                "matched": True,
                "results": _rows("Company other", 6, category="other"),
            },
            "director_screenings": [
                {
                    "person_name": "Richard DAVIES",
                    "screening": {
                        "matched": True,
                        "results": _rows("Richard PEP", 10, category="pep")
                        + _rows("Richard other", 42, category="other"),
                    },
                },
                {
                    "person_name": "Nivedita KRISHNAMURTHY",
                    "screening": {
                        "matched": True,
                        "results": _rows("Nivedita PEP", 2, category="pep")
                        + _rows("Nivedita other", 8, category="other"),
                    },
                },
                {
                    "person_name": "Amy OTHER",
                    "screening": {"matched": True, "results": _rows("Amy other", 8, category="other")},
                },
                {
                    "person_name": "John OTHER",
                    "screening": {"matched": True, "results": _rows("John other", 2, category="other")},
                },
                {
                    "person_name": "Patrice OTHER",
                    "screening": {"matched": True, "results": _rows("Patrice other", 2, category="other")},
                },
                {
                    "person_name": "Tracy OTHER",
                    "screening": {"matched": True, "results": _rows("Tracy other", 2, category="other")},
                },
            ],
            "ubo_screenings": [
                {
                    "person_name": "UBO OTHER",
                    "screening": {"matched": True, "results": _rows("UBO other", 4, category="other")},
                }
            ],
            "intermediary_screenings": [
                {
                    "company_name": "Warwick Capital Partners LLP",
                    "screening": {
                        "matched": True,
                        "results": [
                            _row(
                                "Warwick intermediary 1",
                                category="other",
                                match_category="other",
                                is_adverse_media=False,
                                risk_type_labels=["Provider risk match - review context"],
                            ),
                            _row(
                                "Warwick intermediary 2",
                                category="other",
                                match_category="other",
                                is_adverse_media=False,
                                risk_labels=["Unclassified provider risk"],
                            ),
                        ],
                    },
                }
            ],
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
    assert out["screening_result_terminal"] is True
    assert "Agent recommendation:" not in out["summary"]
    assert "This is an advisory screening interpretation, not an approval decision." not in out["summary"]


def test_existing_hit_counts_regression():
    # Adding hit rows must not disturb the existing aggregate counts.
    out = _build()
    counts = out["hit_counts"]
    assert counts["total"] == 4
    assert counts["sanctions"] == 1
    assert counts["pep"] == 1
    assert counts["adverse_media"] == 1
    # The fixture's 4th hit is category="watchlist" — it now counts as watchlist
    # (a first-class bucket), not "other/uncategorized".
    assert counts["watchlist"] == 1
    assert counts["other"] == 0
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]


def test_multicategory_sanctions_pep_row_uses_sanctions_primary_bucket():
    prescreening = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 1,
            "company_screening": {
                "company_name": "Multi Category Co",
                "matched": True,
                "results": [
                    _row("Sanctions and PEP match", categories=["sanctions", "pep"]),
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }
    out = _build(prescreening=prescreening)
    counts = out["hit_counts"]

    assert counts["total"] == 1
    assert counts["sanctions"] == 1
    assert counts["pep"] == 0
    assert counts["adverse_media"] == 0
    assert counts["other"] == 0
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]
    assert set(out["hit_rows"][0]["categories"]) == {"sanctions", "pep"}
    assert out["severity"] == "Critical"
    assert "1 provider result row(s): 1 sanctions, 0 PEP, 0 provider screening adverse-media row(s), 0 watchlist, and 0 other/uncategorized row(s)" in out["summary"]


def test_multicategory_pep_adverse_media_row_uses_pep_primary_bucket():
    prescreening = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 1,
            "company_screening": {
                "company_name": "PEP Adverse Co",
                "matched": True,
                "results": [
                    _row("PEP and adverse media match", categories=["pep", "adverse_media"]),
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }
    out = _build(prescreening=prescreening)
    counts = out["hit_counts"]

    assert counts["total"] == 1
    assert counts["sanctions"] == 0
    assert counts["pep"] == 1
    assert counts["adverse_media"] == 0
    assert counts["other"] == 0
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]
    assert set(out["hit_rows"][0]["categories"]) == {"pep", "adverse_media"}
    assert out["severity"] == "High"
    assert "1 provider result row(s): 0 sanctions, 1 PEP, 0 provider screening adverse-media row(s), 0 watchlist, and 0 other/uncategorized row(s)" in out["summary"]
    assert "higher-priority headline buckets" in out["adverse_media_relevance"]


def test_multisubject_counts_reconcile_and_intermediary_rows_are_not_adverse_media():
    out = _build(app={"id": "arf-920615", "ref": "ARF-2026-920615"}, prescreening=_arf_920615_like_prescreening())
    counts = out["hit_counts"]

    assert counts["total"] == 88
    assert counts["sanctions"] == 0
    assert counts["pep"] == 12
    assert counts["adverse_media"] == 0
    assert counts["other"] == 76
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]

    intermediary_rows = [row for row in out["hit_rows"] if row["matched_entity"].startswith("Warwick intermediary")]
    assert len(intermediary_rows) == 2
    assert all("adverse_media" not in row["categories"] for row in intermediary_rows)
    assert all(row["type"] == "other" for row in intermediary_rows)

    assert "88 provider result row(s)" in out["summary"]
    assert "0 sanctions, 12 PEP, 0 provider screening adverse-media row(s), 0 watchlist, and 76 other/uncategorized row(s)" in out["summary"]
    assert "76 other/uncategorized provider result row(s) need identity disambiguation." in out["key_concerns"]
    assert not any("adverse media hit" in concern.lower() for concern in out["key_concerns"])
    assert out["adverse_media_relevance"] == "No provider screening adverse-media rows found in stored screening results."


def test_total_hits_slack_is_absorbed_by_other_bucket_without_negative_counts():
    out = _build(app={"id": "arf-920615-slack", "ref": "ARF-2026-920615"}, prescreening=_arf_920615_like_prescreening(total_hits=90))
    counts = out["hit_counts"]

    assert len(out["hit_rows"]) == 88
    assert counts["total"] == 90
    assert counts["pep"] == 12
    assert counts["adverse_media"] == 0
    assert counts["other"] == 78
    assert counts["other"] >= 0
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]
    assert "78 other/uncategorized provider result row(s) need identity disambiguation." in out["key_concerns"]


def test_explicit_adverse_media_fields_still_classify_as_provider_screening_adverse_media():
    prescreening = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 3,
            "company_screening": {
                "company_name": "Adverse Co",
                "matched": True,
                "results": [
                    _row("Explicit adverse bool", is_adverse_media=True),
                    _row("Negative news label", risk_type_labels=["negative_news"]),
                    _row("Adverse media tokens", match_categories=["adverse media"]),
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }
    out = _build(prescreening=prescreening)
    counts = out["hit_counts"]

    assert counts["adverse_media"] == 3
    assert counts["other"] == 0
    assert all("adverse_media" in row["categories"] for row in out["hit_rows"])


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


def test_agent3_does_not_render_raw_provider_score_as_percentage():
    prescreening = {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": _ts(),
            "total_hits": 2,
            "company_screening": {
                "company_name": "Raw Score Co Ltd",
                "matched": True,
                "results": [
                    {
                        "name": "Raw Score 0.7",
                        "provider_match_score_raw": 0.7,
                        "provider_match_types": ["exact_match"],
                        "category": "watchlist",
                        "surfaced_by_pass": "strict",
                    },
                    {
                        "name": "Raw Score 1.7",
                        "provider_match_score_raw": 1.7,
                        "provider_match_types": ["exact_match"],
                        "category": "watchlist",
                        "surfaced_by_pass": "relaxed",
                    },
                ],
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }

    rows = _build(prescreening=prescreening)["hit_rows"]

    assert [row["match_score"] for row in rows] == [None, None]
    assert "strict pass" in rows[0]["reason"].lower()
    assert "relaxed pass" in rows[1]["reason"].lower()
    assert "0.7%" not in rows[0]["reason"]
    assert "1.7%" not in rows[1]["reason"]


# ---------------------------------------------------------------------------
# PR-A: false-green terminal derivation (incomplete screens must NOT be terminal)
# ---------------------------------------------------------------------------

def test_screening_report_state_terminal_for_clean_completed_report():
    st = server._agent3_screening_report_state({"company_screening_state": "completed_clear"})
    assert st["terminal"] is True
    assert st["pending_degraded_reason"] == ""


def test_screening_report_state_not_terminal_when_company_state_pending():
    st = server._agent3_screening_report_state({"company_screening_state": "pending_provider"})
    assert st["terminal"] is False


def test_screening_report_state_not_terminal_when_company_state_not_started():
    st = server._agent3_screening_report_state({"company_screening_state": "not_started"})
    assert st["terminal"] is False


def test_screening_report_state_not_terminal_when_any_subject_non_terminal():
    st = server._agent3_screening_report_state({
        "company_screening_state": "completed_clear",
        "any_non_terminal_subject": True,
    })
    assert st["terminal"] is False


def test_screening_report_state_terminal_when_state_key_absent():
    # No state signal at all -> nothing says "in progress" -> terminal (unchanged legacy behaviour).
    st = server._agent3_screening_report_state({})
    assert st["terminal"] is True


def test_screening_report_state_surfaces_nested_company_pending_reason():
    st = server._agent3_screening_report_state({
        "company_screening": {"pending_reason": "workflow_errored"},
    })
    assert st["pending_degraded_reason"] == "workflow_errored"
    assert st["terminal"] is False


def test_screening_report_state_surfaces_nested_subject_pending_reason():
    st = server._agent3_screening_report_state({
        "director_screenings": [{"screening": {"pending_reason": "workflow_poll_timeout"}}],
    })
    assert st["pending_degraded_reason"] == "workflow_poll_timeout"
    assert st["terminal"] is False


# PR-C: watchlist is a first-class category/count (not "other/uncategorized")
# ---------------------------------------------------------------------------

def _single_category_prescreening(results):
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "total_hits": len(results),
            "company_screening": {
                "company_name": "WL Co",
                "matched": bool(results),
                "results": results,
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }


def test_watchlist_category_is_first_class_not_other():
    out = _build(prescreening=_single_category_prescreening([
        {"name": "WL Match", "category": "watchlist"},
        {"name": "Warn Match", "category": "warning"},
    ]))
    counts = out["hit_counts"]
    assert counts["watchlist"] == 2
    assert counts["other"] == 0
    assert counts["sanctions"] + counts["pep"] + counts["adverse_media"] + counts["watchlist"] + counts["other"] == counts["total"]
    for row in out["hit_rows"]:
        assert "watchlist" in row["categories"]
        assert row["type"] == "watchlist"
    assert "2 watchlist" in out["summary"]


def test_uncategorized_hit_is_other_not_watchlist():
    out = _build(prescreening=_single_category_prescreening([
        {"name": "No category provided"},
    ]))
    counts = out["hit_counts"]
    assert counts["other"] == 1
    assert counts["watchlist"] == 0
    assert all(row["type"] == "other" for row in out["hit_rows"])
    assert all("watchlist" not in row["categories"] for row in out["hit_rows"])

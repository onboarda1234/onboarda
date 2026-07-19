"""SRP-4 Phase C — Agent 3 triage narrative (advisory-only, rts-1.0).

Pins the founder-approved Phase C contract:

* `_agent3_triage_narrative` is a deterministic pure function over stored
  hit dicts: priority hits sorted by rts score descending (input order on
  ties), listed cap of 5 with an honest total `priority_count`, weak tail
  below `_TRIAGE_WEAK_THRESHOLD`, unscored hits reported separately —
  never guessed, never bucketed weak.
* Returns None when no hit carries a stored score, so legacy/pre-rts
  interpretations keep exactly today's payload shape (key absent).
* Band words match the Phase B UI bands: >= 85 "strong", >= 70 "moderate",
  threshold..69 numeric-only (empty band word).
* The narrative NEVER uses percentage/probability/confidence vocabulary —
  the rts score is a ranking, not a provider confidence.
* Advisory-only invariants survive: no provider calls, no risk/decision
  mutation; the narrative participates in output_hash.
* Hit rows pass through the three stored triage fields verbatim.
* `_agent3_hit_status_and_reason` wording no longer phrases provider match
  scores as confidence percentages — with classification OUTCOMES unchanged.
* The back-office panel renders the narrative block only when
  `triage_narrative` is present, escapes all dynamic values, and contains
  no "%" literal.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"

BANNED_VOCABULARY = ("%", "percent", "confidence", "probability")


# ---------------------------------------------------------------------------
# Builder-level fixtures (internal hit dicts, post _agent3_collect_hits shape)
# ---------------------------------------------------------------------------

def _hit(subject="Subject", matched="Matched Entity", score=None, version="rts-1.0",
         reasons=None, categories=None, surfaced="strict"):
    return {
        "subject_name": subject,
        "matched_name": matched,
        "triage_score": score,
        "triage_score_version": version if score is not None else "",
        "triage_score_reasons": list(reasons or []),
        "categories": list(categories or ["watchlist"]),
        "surfaced_by_pass": surfaced,
    }


def _sample_hits():
    """3 scored priority hits (92/74/55), 2 weak (20), 1 unscored."""
    return [
        _hit("Acme Holdings Ltd", "ACME HOLDING", 92,
             reasons=["Sanctions list match", "Exact name match"],
             categories=["sanctions"]),
        _hit("Jane Director", "Jane R Director", 74,
             reasons=["PEP class 1-2 match"], categories=["pep"]),
        _hit("Acme Holdings Ltd", "Acme Media Report", 55,
             reasons=["Adverse media match"], categories=["adverse_media"]),
        _hit("Weak One", "Weak Match A", 20, reasons=["Name-only match"]),
        _hit("Weak Two", "Weak Match B", 20, reasons=["Name-only match"]),
        _hit("Legacy Subject", "Legacy Hit", None),
    ]


# ---------------------------------------------------------------------------
# Interpretation-level fixtures (stored screening_report shape)
# ---------------------------------------------------------------------------

def _result(name, *, score=None, triage=None, reasons=None, category="watchlist", ref=None):
    row = {"name": name, "category": category}
    if score is not None:
        row["match_score"] = score
    if triage is not None:
        row["triage_score"] = triage
        row["triage_score_version"] = "rts-1.0"
        row["triage_score_reasons"] = list(reasons or [])
    if ref:
        row["id"] = ref
    return row


def _prescreening(results):
    return {
        "screening_report": {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": "2026-07-01T00:00:00+00:00",
            "total_hits": len(results),
            "overall_flags": [],
            "company_screening": {
                "company_name": "Phase C Co Ltd",
                "matched": bool(results),
                "results": results,
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
    }


def _build(prescreening):
    return server._agent3_build_screening_interpretation(
        {"id": "app-phasec-1", "ref": "ARF-2026-PHC01", "company_name": "Phase C Co Ltd"},
        prescreening,
        [],
        declared_pep_subjects=[],
    )


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------

class TestTriageNarrativeBuilder:
    def test_deterministic(self):
        first = server._agent3_triage_narrative(_sample_hits())
        second = server._agent3_triage_narrative(_sample_hits())
        assert first == second
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_returns_none_when_nothing_scored(self):
        assert server._agent3_triage_narrative([]) is None
        assert server._agent3_triage_narrative([_hit(score=None), _hit(score=None)]) is None
        assert server._agent3_triage_narrative(None) is None
        # Bool is not a triage score.
        assert server._agent3_triage_narrative([_hit(score=True)]) is None

    def test_priority_descending_with_input_order_tie_break(self):
        hits = [
            _hit("First 70", "First 70", 70),
            _hit("Ninety", "Ninety", 90),
            _hit("Second 70", "Second 70", 70),
            _hit("Fifty", "Fifty", 50),
        ]
        narrative = server._agent3_triage_narrative(hits)
        names = [entry["matched_name"] for entry in narrative["priority_hits"]]
        assert names == ["Ninety", "First 70", "Second 70", "Fifty"]
        scores = [entry["score"] for entry in narrative["priority_hits"]]
        assert scores == sorted(scores, reverse=True)

    def test_listed_cap_of_five_with_honest_priority_count(self):
        hits = [_hit(f"Subject {idx}", f"Match {idx}", 90 - idx) for idx in range(7)]
        narrative = server._agent3_triage_narrative(hits)
        assert narrative["priority_count"] == 7
        assert len(narrative["priority_hits"]) == 5
        assert "Review these 7 hits first" in narrative["headline"]
        assert "Only the top 5 are listed here; all 7" in narrative["narrative"]

    def test_weak_and_unscored_counts(self):
        narrative = server._agent3_triage_narrative(_sample_hits())
        assert narrative["priority_count"] == 3
        assert narrative["weak_tail_count"] == 2
        assert narrative["unscored_count"] == 1
        assert narrative["weak_threshold"] == server._TRIAGE_WEAK_THRESHOLD
        assert narrative["version"] == "rts-1.0"
        assert (
            "The remaining 2 hit(s) rank below the weak threshold (40) "
            "and are grouped in the weak tail." in narrative["narrative"]
        )
        assert (
            "1 hit(s) were screened before triage scoring existed and are "
            "unscored — review them individually." in narrative["narrative"]
        )

    def test_singular_headline(self):
        narrative = server._agent3_triage_narrative([_hit(score=80)])
        assert narrative["headline"] == "Review this 1 hit first, ranked by RegMind triage."
        assert narrative["priority_count"] == 1

    def test_band_boundaries_match_phase_b_ui(self):
        narrative = server._agent3_triage_narrative([
            _hit("A", "At 85", 85),
            _hit("B", "At 84", 84),
            _hit("C", "At 70", 70),
            _hit("D", "At 69", 69),
            _hit("E", "At 40", 40),
        ])
        bands = {entry["matched_name"]: entry["band"] for entry in narrative["priority_hits"]}
        assert bands["At 85"] == "strong"
        assert bands["At 84"] == "moderate"
        assert bands["At 70"] == "moderate"
        assert bands["At 69"] == ""          # 40-69: numeric only, no band word
        assert bands["At 40"] == ""
        assert server._AGENT3_TRIAGE_STRONG == 85
        assert server._AGENT3_TRIAGE_MODERATE == 70

    def test_narrative_names_top_hits_with_band_and_reasons(self):
        narrative = server._agent3_triage_narrative(_sample_hits())
        text = narrative["narrative"]
        # Numbered, in rank order, band word + score, first-2 reasons verbatim
        # (first letter lowercased), joined with "; ".
        assert (
            "1. Acme Holdings Ltd — matched 'ACME HOLDING' (strong, triage 92): "
            "sanctions list match; exact name match." in text
        )
        assert "2. Jane Director — matched 'Jane R Director' (moderate, triage 74)" in text
        # Leading acronyms stay verbatim — never mangled to "pEP ...".
        assert "(moderate, triage 74): PEP class 1-2 match." in text
        assert "pEP" not in text
        # 40-69: number only — no band word inside the parenthetical.
        assert "3. Acme Holdings Ltd — matched 'Acme Media Report' (triage 55)" in text
        assert text.index("1. Acme") < text.index("2. Jane") < text.index("3. Acme")

    def test_reasons_capped_at_two_in_priority_hits(self):
        hits = [_hit(score=90, reasons=["One", "Two", "Three", "Four"])]
        narrative = server._agent3_triage_narrative(hits)
        assert narrative["priority_hits"][0]["reasons"] == ["One", "Two"]

    def test_threshold_read_from_module_constant(self, monkeypatch):
        hits = [_hit("A", "A", 65), _hit("B", "B", 45)]
        default = server._agent3_triage_narrative(hits)
        assert default["priority_count"] == 2
        assert default["weak_tail_count"] == 0
        monkeypatch.setattr(server, "_TRIAGE_WEAK_THRESHOLD", 60)
        raised = server._agent3_triage_narrative(hits)
        assert raised["weak_threshold"] == 60
        assert raised["priority_count"] == 1
        assert raised["weak_tail_count"] == 1
        assert "below the weak threshold (60)" in raised["narrative"]

    def test_banned_vocabulary_never_appears(self):
        narrative = server._agent3_triage_narrative(_sample_hits())
        rendered = json.dumps(narrative, sort_keys=True).lower()
        for banned in BANNED_VOCABULARY:
            assert banned not in rendered, f"banned vocabulary {banned!r} in narrative output"
        # And explicitly on the officer-facing text fields.
        for field in (narrative["headline"], narrative["narrative"]):
            for banned in BANNED_VOCABULARY:
                assert banned not in field.lower()
        for entry in narrative["priority_hits"]:
            for reason in entry["reasons"]:
                for banned in BANNED_VOCABULARY:
                    assert banned not in reason.lower()

    def test_all_scored_below_threshold_still_returns_honest_narrative(self):
        narrative = server._agent3_triage_narrative([_hit(score=10), _hit(score=20)])
        assert narrative is not None
        assert narrative["priority_count"] == 0
        assert narrative["priority_hits"] == []
        assert narrative["weak_tail_count"] == 2
        assert "review the weak tail individually" in narrative["headline"]


# ---------------------------------------------------------------------------
# Interpretation integration
# ---------------------------------------------------------------------------

class TestInterpretationIntegration:
    def _scored_prescreening(self):
        return _prescreening([
            _result("Scored Sanctions", score=92, triage=92,
                    reasons=["Sanctions list match", "Exact name match"],
                    category="sanctions", ref="prov-c-1"),
            _result("Scored Watchlist", score=44, triage=55,
                    reasons=["Name-only match"], ref="prov-c-2"),
            _result("Unscored Legacy", score=30, ref="prov-c-3"),
        ])

    def _unscored_prescreening(self):
        return _prescreening([
            _result("Scored Sanctions", score=92, category="sanctions", ref="prov-c-1"),
            _result("Scored Watchlist", score=44, ref="prov-c-2"),
            _result("Unscored Legacy", score=30, ref="prov-c-3"),
        ])

    def test_narrative_present_only_when_scored_hits_exist(self):
        scored = _build(self._scored_prescreening())
        assert "triage_narrative" in scored
        narrative = scored["triage_narrative"]
        assert narrative["priority_count"] == 2
        assert narrative["unscored_count"] == 1
        assert narrative["weak_tail_count"] == 0
        assert narrative["version"] == "rts-1.0"

        legacy = _build(self._unscored_prescreening())
        assert "triage_narrative" not in legacy  # key absent, not None

    def test_advisory_invariants_survive(self):
        out = _build(self._scored_prescreening())
        assert out["provider_call_made"] is False
        assert out["risk_or_decision_mutation"] is False

    def test_output_hash_differs_between_scored_and_unscored_variants(self):
        scored = _build(self._scored_prescreening())
        unscored = _build(self._unscored_prescreening())
        assert scored["output_hash"] != unscored["output_hash"]
        # Determinism: rebuilding the scored variant reproduces the hash
        # (output_hash excludes generated_at).
        assert scored["output_hash"] == _build(self._scored_prescreening())["output_hash"]

    def test_hit_rows_carry_triage_fields(self):
        out = _build(self._scored_prescreening())
        rows = {row["matched_entity"]: row for row in out["hit_rows"]}
        sanctions = rows["Scored Sanctions"]
        assert sanctions["triage_score"] == 92
        assert sanctions["triage_score_version"] == "rts-1.0"
        assert sanctions["triage_score_reasons"] == ["Sanctions list match", "Exact name match"]
        assert sanctions["audit_trace"]["triage_score_version"] == "rts-1.0"
        legacy = rows["Unscored Legacy"]
        assert legacy["triage_score"] is None
        assert legacy["triage_score_version"] == ""
        assert legacy["triage_score_reasons"] == []
        assert legacy["audit_trace"]["triage_score_version"] == ""

    def test_narrative_never_changes_statuses_or_disposition(self):
        scored = _build(self._scored_prescreening())
        unscored = _build(self._unscored_prescreening())
        assert scored["recommended_disposition"] == unscored["recommended_disposition"]
        assert scored["severity"] == unscored["severity"]
        assert (
            [row["suggested_status"] for row in scored["hit_rows"]]
            == [row["suggested_status"] for row in unscored["hit_rows"]]
        )

    def test_reasons_pass_through_capped_and_string_only(self):
        prescreening = _prescreening([
            _result("Many Reasons", triage=90,
                    reasons=["R1", "", "  ", "R2", 42, "R3", "R4", "R5", "R6", "R7"]),
        ])
        out = _build(prescreening)
        row = out["hit_rows"][0]
        assert row["triage_score_reasons"] == ["R1", "R2", "R3", "R4", "R5", "R6"]


# ---------------------------------------------------------------------------
# Wording cleanup — outcomes unchanged, vocabulary fixed
# ---------------------------------------------------------------------------

class TestHitStatusWordingCleanup:
    STATUS_MATRIX = [
        ("sanctions", 95, server.AGENT3_HIT_STATUS_HIGH_CONFIDENCE),
        ("sanctions", 90, server.AGENT3_HIT_STATUS_HIGH_CONFIDENCE),
        ("sanctions", 89, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("sanctions", 10, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("pep", 95, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("pep", 40, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("adverse_media", 30, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("watchlist", 69, server.AGENT3_HIT_STATUS_LIKELY_FP),
        ("watchlist", 70, server.AGENT3_HIT_STATUS_NEEDS_REVIEW),
        ("other", 50, server.AGENT3_HIT_STATUS_LIKELY_FP),
    ]

    @pytest.mark.parametrize("primary_type,score,expected", STATUS_MATRIX)
    def test_status_outcomes_unchanged(self, primary_type, score, expected):
        status, reason = server._agent3_hit_status_and_reason(primary_type, score)
        assert status == expected
        assert "provider match score" in reason.lower()
        assert "%" not in reason
        assert "confidence" not in reason.lower()

    def test_thresholds_unchanged(self):
        assert server._AGENT3_HIT_LOW_CONFIDENCE == 70.0
        assert server._AGENT3_HIT_HIGH_CONFIDENCE_SANCTIONS == 90.0

    def test_no_score_reasons_reworded(self):
        status, reason = server._agent3_hit_status_and_reason("watchlist", None)
        assert status == server.AGENT3_HIT_STATUS_UNAVAILABLE
        assert "No provider match score recorded" in reason
        assert "confidence" not in reason.lower()
        status, reason = server._agent3_hit_status_and_reason("watchlist", None, "strict")
        assert status == server.AGENT3_HIT_STATUS_NEEDS_REVIEW
        assert "No numeric provider match score recorded" in reason
        assert "strict pass" in reason
        assert "confidence" not in reason.lower()

    def test_sanctions_reason_flags_unconfirmed_scale(self):
        _, reason = server._agent3_hit_status_and_reason("sanctions", 95)
        assert "scale unconfirmed by provider" in reason
        assert "officer identity verification required" in reason.lower()

    def test_panel_false_positive_copy_no_longer_says_low_confidence(self):
        out = _build(_prescreening([
            _result("Low Score Only", score=30, ref="prov-lo-1"),
        ]))
        assert out["recommended_disposition"] == "False positive likely"
        assert (
            "False positive likely based on stored low provider match scores."
            in out["false_positive_assessment"]
        )
        assert "low-confidence" not in out["false_positive_assessment"]


# ---------------------------------------------------------------------------
# Static UI pins
# ---------------------------------------------------------------------------

def _html():
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _function_region(html, name, next_name):
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def _strip_js_comments(source):
    """Drop // line comments so vocabulary pins only see executable code and
    string literals — i.e. what can actually reach an officer's screen."""
    return "\n".join(
        line.split("//", 1)[0] if "//" in line and "://" not in line else line
        for line in source.splitlines()
    )


def _assert_no_banned_probability_vocabulary(region_source):
    """PR #797 review finding: officer-facing copy (including tooltips) must
    never frame the triage score as a percentage/confidence/probability —
    not even to deny it. Comments may explain the rule; rendered strings
    may not use the words."""
    code = _strip_js_comments(region_source).lower()
    for banned in ("%", "percent", "confidence", "probability"):
        assert banned not in code, f"banned vocabulary {banned!r} in rendered helper code"


class TestBackofficeTriageNarrativeStatic:
    def test_narrative_block_exists_and_is_gated(self):
        html = _html()
        panel = _function_region(
            html, "renderAgent3ScreeningInterpretationPanel", "generateAgent3ScreeningInterpretation"
        )
        # Render-if-present: older stored interpretations lack triage_narrative.
        assert "output.triage_narrative && typeof output.triage_narrative === 'object'" in panel
        assert "agent3TriageNarrativeHtml(output.triage_narrative)" in panel
        # The advisory label survives Phase C.
        assert "Advisory — decisions are made by officers." in panel

    def test_narrative_helper_contract(self):
        html = _html()
        helper = _function_region(
            html, "agent3TriageNarrativeHtml", "renderAgent3ScreeningInterpretationPanel"
        )
        assert "Where to start — RegMind triage (advisory)" in helper
        # Counts come from the server dict — never recomputed client-side.
        assert "narrative.priority_hits" in helper
        assert "narrative.weak_tail_count" in helper
        assert "narrative.unscored_count" in helper
        assert "narrative.weak_threshold" in helper
        assert "narrative.headline" in helper
        # Band word for strong/moderate, bare number otherwise; RegMind triage
        # label context — never a percentage.
        assert "band === 'strong'" in helper
        assert "band === 'moderate'" in helper
        assert "RegMind triage" in helper
        assert "%" not in helper
        _assert_no_banned_probability_vocabulary(helper)
        # Every dynamic value flows through escapeHtml.
        assert "escapeHtml(String(narrative.headline))" in helper
        assert "escapeHtml(hit.subject_name || 'Unknown subject')" in helper
        assert "escapeHtml(hit.matched_name || 'Stored hit')" in helper
        assert "escapeHtml(chipLabel)" in helper
        assert "escapeHtml(reasons.join('; '))" in helper
        assert "escapeHtml(String(narrative.weak_tail_count))" in helper
        assert "escapeHtml(String(narrative.unscored_count))" in helper
        # Weak-tail and unscored copy render server counts.
        assert "and are grouped in the weak tail." in helper
        assert "review them individually." in helper

    def test_phase_b_score_block_tooltip_has_no_banned_vocabulary(self):
        # The Phase B score-block tooltip was reworded alongside the Phase C
        # finding — keep both helpers clean.
        html = _html()
        block = _function_region(
            html, "screeningTriageScoreBlock", "screeningReviewReportIsBlindPreEnrichment"
        )
        _assert_no_banned_probability_vocabulary(block)

    def test_narrative_helper_makes_no_api_calls(self):
        html = _html()
        helper = _function_region(
            html, "agent3TriageNarrativeHtml", "renderAgent3ScreeningInterpretationPanel"
        )
        assert "boApiCall(" not in helper
        assert "fetch(" not in helper


# ---------------------------------------------------------------------------
# Phase F (F1) — narrative grouping of near-identical priority hits
# ---------------------------------------------------------------------------

def _mass_hit(i, score=58, matched="WIRECARD"):
    return _hit(
        "Wirecard AG", matched, score,
        reasons=["Adverse media", "Exact name match", "Article evidence"],
        categories=["adverse_media"],
    )


class TestTriageNarrativeGrouping:
    """Phase F: hits sharing (matched_name lowercase, score, reason-set)
    collapse into ONE narrative entry; distinct hits stay individual;
    standouts lead the narrative; homogeneous masses stop producing a
    'Review these N hits first' headline."""

    def _mixed_hits(self):
        hits = [_mass_hit(i) for i in range(198)]
        hits.append(_hit(
            "Wirecard AG", "Wirecard AG Warning List", 53,
            reasons=["Watchlist warning match"], categories=["watchlist"],
        ))
        hits.append(_hit("Wirecard AG", "Weak Match A", 20, reasons=["Name-only match"]))
        hits.append(_hit("Wirecard AG", "Weak Match B", 20, reasons=["Name-only match"]))
        return hits

    def test_homogeneous_mass_collapses_to_one_group_entry(self):
        narrative = server._agent3_triage_narrative(self._mixed_hits())
        entries = narrative["entries"]
        assert len(entries) == 2
        kinds = [entry["kind"] for entry in entries]
        assert kinds == ["hit", "group"]
        group = entries[1]
        assert group["count"] == 198
        assert group["matched_name"] == "wirecard"
        assert group["score"] == 58
        assert group["reasons"] == ["Adverse media", "Exact name match", "Article evidence"]

    def test_standout_leads_then_mass_then_weak_tail(self):
        narrative = server._agent3_triage_narrative(self._mixed_hits())
        text = narrative["narrative"]
        assert (
            "1. Wirecard AG — matched 'Wirecard AG Warning List' (triage 53): "
            "watchlist warning match." in text
        )
        assert (
            "2. Wirecard AG — 198 adverse-media matches on 'wirecard', all "
            "triage 58 (adverse media; exact name match; article evidence) — "
            "no single hit stands out." in text
        )
        assert text.index("Warning List") < text.index("198 adverse-media")
        assert "weak tail" in text
        assert "Review these" not in narrative["headline"]
        assert "stand out" in narrative["headline"]

    def test_fully_homogeneous_headline_is_structural(self):
        narrative = server._agent3_triage_narrative([_mass_hit(i) for i in range(50)])
        assert narrative["priority_count"] == 50
        assert len(narrative["entries"]) == 1
        assert narrative["entries"][0]["kind"] == "group"
        assert "Review these" not in narrative["headline"]
        assert "no single hit stands out" in narrative["headline"]

    def test_distinct_hits_keep_pre_grouping_sentences_and_headline(self):
        narrative = server._agent3_triage_narrative(_sample_hits())
        assert all(entry["kind"] == "hit" for entry in narrative["entries"])
        assert narrative["headline"] == "Review these 3 hits first, ranked by RegMind triage."
        assert (
            "1. Acme Holdings Ltd — matched 'ACME HOLDING' (strong, triage 92): "
            "sanctions list match; exact name match." in narrative["narrative"]
        )

    def test_priority_hits_payload_shape_preserved(self):
        narrative = server._agent3_triage_narrative(self._mixed_hits())
        # Data preserved: priority_hits keeps the pre-grouping per-hit shape.
        assert len(narrative["priority_hits"]) == 5
        assert all(
            set(hit.keys()) == {
                "subject_name", "matched_name", "score", "band",
                "categories", "reasons", "surfaced_by_pass",
            }
            for hit in narrative["priority_hits"]
        )
        assert narrative["priority_count"] == 199

    def test_grouped_output_is_deterministic_and_clean(self):
        first = server._agent3_triage_narrative(self._mixed_hits())
        second = server._agent3_triage_narrative(self._mixed_hits())
        assert first == second
        blob = json.dumps(first, sort_keys=True).lower()
        for banned in BANNED_VOCABULARY:
            assert banned not in blob

"""Regression tests for the 2026-07-19 screening truth-flow fix batch.

Three production defects were found on staging during the post-seeding
combined validation run (live ComplyAdvantage Mesh sandbox), each pinned with
raw artifact JSON from ARF-QAFIX-001 / ARF-QAFIX-006:

1. Mesh's live conflict wording ("external identifier ... in use and belongs
   to the customer identifier <uuid>") was not classified by
   ``_customer_identifier_conflict`` (token set knew only already/duplicate/
   exists/assigned) — so the re-screen degraded generically instead of
   fail-closing as an identifier conflict.
2. ``_combine_reports`` rolled the company state up through a match/clear
   binary that could never express pending/errored, and
   ``any_non_terminal_subject`` never counted the company block — an errored
   entity screen rendered as "Clear — provider screening completed with no
   hits".
3. Live payloads carry categories ONLY in ``provider_aml_types_raw`` (typed
   indicator models absent, ``risk`` empty), so every rollup was False and
   every rts score fell to the uncategorized floor (adverse media scored
   48 = 5+35+8 instead of 58 = 15+35+8) — fixed by the aml-type fallback and
   versioned as rts-1.1 (weights unchanged).
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_complyadvantage.adapter import _combine_reports
from screening_complyadvantage.normalizer import (
    MergedMatch,
    TRIAGE_SCORE_VERSION,
    compute_match_rollups,
    compute_triage_score,
)
from screening_complyadvantage.models.output import (
    CAProfile,
    CAProfileCompany,
    CARiskDetail,
)
from screening_complyadvantage.orchestrator import (
    _attach_conflict_existing_customers,
    _customer_identifier_conflict,
)


# Exact live error observed on staging 2026-07-19 (ARF-QAFIX-001 strict pass).
LIVE_CONFLICT_ERROR = (
    "external identifier f1xedqa000000001:company:name-"
    "45f4518c2a65295c0fcbca48035a438d:strict in use and belongs to the "
    "customer identifier 019f6bc5-ec91-74e8-9964-8482200ed051"
)


def _workflow_raw(error_message):
    return {
        "status": "ERRORED",
        "workflow_type": "create-and-screen",
        "step_details": {
            "customer-creation": {
                "status": "ERRORED",
                "error_message": error_message,
                "step_output": {
                    "customer_identifier": "019f6bc5-ec91-74e8-9964-8482200ed051",
                    "external_identifier": "f1xedqa000000001:company:name-x:strict",
                },
            }
        },
    }


class TestBug1ConflictWording:
    def test_live_in_use_wording_classifies(self):
        assert _customer_identifier_conflict(_workflow_raw(LIVE_CONFLICT_ERROR)) is True

    def test_belongs_to_wording_classifies(self):
        raw = _workflow_raw(
            "external_identifier belongs to another customer record"
        )
        assert _customer_identifier_conflict(raw) is True

    def test_historic_already_assigned_still_classifies(self):
        raw = _workflow_raw("external identifier already assigned")
        assert _customer_identifier_conflict(raw) is True

    def test_unrelated_error_does_not_classify(self):
        raw = _workflow_raw("screening configuration missing for workspace")
        assert _customer_identifier_conflict(raw) is False

    def test_existing_customer_uuids_harvested_per_pass(self):
        strict = types.SimpleNamespace(
            identifier_conflict=True,
            customer_response=types.SimpleNamespace(
                identifier="019f6bc5-ec91-74e8-9964-8482200ed051"
            ),
        )
        relaxed = types.SimpleNamespace(
            identifier_conflict=False,
            customer_response=types.SimpleNamespace(identifier="should-not-appear"),
        )
        report = {}
        _attach_conflict_existing_customers(report, strict=strict, relaxed=relaxed)
        assert report["customer_identifier_conflict_existing_customers"] == {
            "strict": "019f6bc5-ec91-74e8-9964-8482200ed051"
        }

    def test_no_harvest_leaves_report_untouched(self):
        none_found = types.SimpleNamespace(
            identifier_conflict=True, customer_response=None
        )
        report = {}
        _attach_conflict_existing_customers(
            report, strict=none_found, relaxed=none_found
        )
        assert "customer_identifier_conflict_existing_customers" not in report


def _errored_company_report(director_state=None):
    """Mirror of the ARF-QAFIX-001 stored-report shape (errored entity)."""
    report = {
        "company_screening": {
            "screening_state": "pending_provider",
            "api_status": "pending",
            "pending_reason": "workflow_errored",
            "matched": False,
            "results": [],
            "sanctions": {"api_status": "live", "matched": False, "results": []},
        },
        "has_company_screening_hit": False,
        # The live errored report carries coverage "full" (verified in the
        # ARF-QAFIX-001 artifact) — which is exactly why the block was picked
        # up and the binary rollup mislabelled it clear.
        "company_screening_coverage": "full",
        "overall_flags": [],
        "degraded_sources": ["complyadvantage_workflow_errored"],
        "total_hits": 0,
        "director_screenings": [],
        "ubo_screenings": [],
        "intermediary_screenings": [],
        "provider_specific": {},
    }
    if director_state is not None:
        report["director_screenings"] = [
            {"name": "Director A", "screening_state": director_state}
        ]
    return report


class TestBug2ErroredStateRollup:
    def test_errored_company_never_rolls_up_as_clear(self):
        combined = _combine_reports([_errored_company_report()])
        assert combined["company_screening_state"] == "pending_provider"
        assert combined["company_screening_state"] != "completed_clear"

    def test_company_block_counts_toward_non_terminal(self):
        # The fail-open variant: ONLY the company errored, every person
        # terminal — the report must still read non-terminal.
        combined = _combine_reports(
            [_errored_company_report(director_state="completed_clear")]
        )
        assert combined["any_non_terminal_subject"] is True

    def test_terminal_company_keeps_match_binary(self):
        report = _errored_company_report()
        report["company_screening"]["screening_state"] = "completed_clear"
        report["company_screening"]["api_status"] = "live"
        report["company_screening"].pop("pending_reason")
        report["degraded_sources"] = []
        combined = _combine_reports([report])
        assert combined["company_screening_state"] == "completed_clear"

    def test_rule_engine_holds_risk_on_this_report(self):
        # Belt and braces: the exact staging shape must keep engaging the
        # risk-lowering hold (degraded_sources non-empty).
        import rule_engine

        combined = _combine_reports([_errored_company_report()])
        assert rule_engine._screening_report_is_non_terminal(combined) is True

    def test_entity_sanctions_record_inherits_parent_non_terminal_state(self):
        # server-side: the queue's entity resolver reads the sanctions
        # sub-record; a leftover api_status "live" on an errored parent must
        # not read as a terminal clear answer.
        import server

        company = _errored_company_report()["company_screening"]
        record = server._entity_sanctions_record(company)
        assert record["screening_state"] == "pending_provider"
        assert record["api_status"] == "pending"
        assert record["pending_reason"] == "workflow_errored"

    def test_entity_sanctions_record_unchanged_when_parent_terminal(self):
        import server

        company = {
            "screening_state": "completed_clear",
            "api_status": "live",
            "sanctions": {"api_status": "live", "matched": False, "results": []},
        }
        record = server._entity_sanctions_record(company)
        assert record == {"api_status": "live", "matched": False, "results": []}


class TestConflictPropagationToCombinedReport:
    """The per-subject conflict boolean and harvested UUIDs must survive
    _combine_reports — the review page's conflict-variant honesty banner
    checks report.customer_identifier_conflict === true on the STORED
    (combined) report, and the SRP-2a recovery reads the harvested UUIDs
    from it."""

    def _conflicted_subject_report(self):
        report = _errored_company_report()
        report["degraded_sources"] = [
            "complyadvantage_customer_identifier_conflict"
        ]
        report["customer_identifier_conflict"] = True
        report["customer_identifier_conflict_existing_customers"] = {
            "strict": "019f6bc5-ec91-74e8-9964-8482200ed051"
        }
        report["provider_specific"] = {
            "complyadvantage": {"screening_subject": {"kind": "entity"}}
        }
        return report

    def test_conflict_boolean_survives_combine(self):
        combined = _combine_reports([self._conflicted_subject_report()])
        assert combined["customer_identifier_conflict"] is True

    def test_harvested_uuids_survive_combine_keyed_by_subject(self):
        combined = _combine_reports([self._conflicted_subject_report()])
        assert combined["customer_identifier_conflict_existing_customers"] == {
            "entity": {"strict": "019f6bc5-ec91-74e8-9964-8482200ed051"}
        }

    def test_clean_report_carries_neither_field(self):
        combined = _combine_reports([_errored_company_report()])
        assert "customer_identifier_conflict" not in combined or not combined[
            "customer_identifier_conflict"
        ]
        assert (
            "customer_identifier_conflict_existing_customers" not in combined
        )


def _live_shape_match(aml_types, *, match_types=("exact_match",), media=False):
    """A match shaped like the live ARF-QAFIX-006 payloads: typed indicators
    absent (empty risk), categories only in provider_aml_types_raw."""
    profile = CAProfile(
        identifier="p-live",
        company=CAProfileCompany(),
        risk_types=[],
        risk_indicators=[],
    )
    profile.provider_aml_types_raw = list(aml_types)
    profile.provider_match_types = list(match_types)
    if media:
        profile.provider_media_evidence = [
            {"url": "https://example.org/a", "title": "Article"}
        ]
    return MergedMatch(
        risk=CARiskDetail(values=[]),
        surfaced_by_pass="both",
        profile=profile,
        profile_identifier="p-live",
        risk_id="r-live",
        alert_id="a-live",
    )


class TestBug3AmlTypeCategoryFallback:
    def test_version_is_rts_1_1(self):
        assert TRIAGE_SCORE_VERSION == "rts-1.1"

    def test_rollups_see_adverse_media_from_aml_types(self):
        match = _live_shape_match(
            ["adverse-media-v2-fraud-linked", "adverse-media-v2-regulatory"]
        )
        rollups = compute_match_rollups(match)
        assert rollups["has_adverse_media_hit"] is True

    def test_live_adverse_media_exact_with_evidence_scores_58(self):
        match = _live_shape_match(
            ["adverse-media-v2-fraud-linked", "adverse-media-v2-regulatory"],
            media=True,
        )
        result = compute_triage_score(match, compute_match_rollups(match))
        assert result["score"] == 58  # 15 media + 35 exact + 8 evidence
        assert "adverse media" in result["reasons"]
        assert "exact name match" in result["reasons"]
        assert "article evidence attached" in result["reasons"]
        assert "uncategorized provider match" not in result["reasons"]
        assert result["version"] == "rts-1.1"

    def test_live_warning_exact_scores_53(self):
        match = _live_shape_match(["warning"])
        result = compute_triage_score(match, compute_match_rollups(match))
        assert result["score"] == 53  # 18 watchlist + 35 exact
        assert "watchlist entry" in result["reasons"]
        assert "uncategorized provider match" not in result["reasons"]

    def test_truly_uncategorized_keeps_floor(self):
        match = _live_shape_match([])
        result = compute_triage_score(match, compute_match_rollups(match))
        assert "uncategorized provider match" in result["reasons"]
        assert result["score"] == 40  # 5 floor + 35 exact

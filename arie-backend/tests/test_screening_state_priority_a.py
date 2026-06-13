"""
Priority A — Screening State Semantics: truthful, fail-closed remediation.
==========================================================================

Audit-evidence regression tests for the canonical screening state model.
These tests lock in the controls-truthfulness fix:

* Sumsub subjects in init / pending / created / error / not_configured
  states must NOT render as "Clear" or "No Match".
* Non-terminal provider states must NOT be consumed downstream as if
  screening is complete.
* Self-declared PEP signals must remain visible as a separate case
  signal even before provider confirmation.
* "Not configured" must be explicit and must never be mistaken for
  "clear".
* Memo language must not imply completed screening unless the
  provider-backed result is real and terminal.
* Existing terminal-clear flows must continue to behave correctly.

If any of these tests start failing, the screening layer is regressing
back to a state that creates false reassurance for compliance officers.
That is a production-trust controls regression and must be treated as a
blocker, not a wording issue.
"""

import json

import pytest


# ── Module under test: canonical state model ──────────────────────────


class TestCanonicalStateModel:
    def test_pending_api_status_is_not_terminal(self):
        from screening_state import (
            derive_screening_state,
            is_terminal,
            is_reassuring,
        )
        for api in ("pending", "init", "created", "queued", "onHold"):
            state = derive_screening_state(
                {"matched": False, "results": [], "api_status": api}
            )
            assert state == "pending_provider", api
            assert not is_terminal(state)
            assert not is_reassuring(state)

    def test_not_configured_is_explicit(self):
        from screening_state import derive_screening_state, is_reassuring
        state = derive_screening_state(
            {"matched": False, "results": [], "api_status": "not_configured"}
        )
        assert state == "not_configured"
        assert not is_reassuring(state)

    def test_failed_states_are_explicit(self):
        from screening_state import derive_screening_state, is_reassuring
        for api in ("error", "unavailable", "blocked"):
            state = derive_screening_state(
                {"matched": False, "results": [], "api_status": api}
            )
            assert state == "failed", api
            assert not is_reassuring(state)
        # Source-driven failed
        state = derive_screening_state(
            {"matched": False, "results": [], "source": "unavailable"}
        )
        assert state == "failed"

    def test_terminal_clear_only_for_live(self):
        from screening_state import derive_screening_state, is_reassuring
        state = derive_screening_state(
            {"matched": False, "results": [], "api_status": "live"}
        )
        assert state == "completed_clear"
        assert is_reassuring(state)

    def test_simulated_is_not_treated_as_terminal(self):
        # Simulated runs are non-production and must never be a basis for
        # compliance reassurance.
        from screening_state import derive_screening_state, is_reassuring
        state = derive_screening_state(
            {"matched": False, "results": [], "api_status": "simulated"}
        )
        assert state != "completed_clear"
        assert not is_reassuring(state)

    def test_truth_states_distinguish_provider_modes_from_clear(self):
        from screening_state import derive_screening_truth

        cases = [
            ("simulated", "simulated_fallback"),
            ("sandbox", "sandbox_provider"),
            ("pending", "pending"),
            ("not_configured", "not_configured"),
            ("failed", "failed"),
        ]
        for api_status, expected_state in cases:
            truth = derive_screening_truth(
                {"matched": False, "results": [], "source": api_status, "api_status": api_status},
                name=f"case_{api_status}",
                required=True,
            )
            assert truth["canonical_state"] == expected_state
            assert truth["provider_mode"] == expected_state
            assert truth["terminal"] is False
            assert truth["defensible_clear"] is False
            assert truth["legacy_status"] != "clear"

        clear = derive_screening_truth(
            {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
            required=True,
        )
        assert clear["canonical_state"] == "completed_clear"
        assert clear["provider_mode"] == "live_provider"
        assert clear["terminal"] is True
        assert clear["defensible_clear"] is True

        match = derive_screening_truth(
            {
                "matched": True,
                "results": [{"name": "Watchlist Hit", "is_sanctioned": True}],
                "source": "sumsub",
                "api_status": "live",
            },
            required=True,
        )
        assert match["canonical_state"] == "completed_match"
        assert match["screening_result"] == "match"
        assert match["defensible_clear"] is False
        assert match["approval_blocking"] is True

        cleared_match = derive_screening_truth(
            {
                "matched": True,
                "results": [{"name": "False Positive", "is_sanctioned": True}],
                "source": "sumsub",
                "api_status": "live",
                "review_disposition": "false_positive_cleared",
                "review_disposition_code": "false_positive_cleared",
                "review_rationale": "Officer confirmed identity mismatch against provider evidence.",
                "review_evidence_reference": "Provider case CA-FP-001 and passport copy.",
                "reviewer_id": "co001",
                "reviewed_at": "2026-04-22T10:00:00Z",
                "audit_confirmed": True,
            },
            required=True,
        )
        assert cleared_match["canonical_state"] == "completed_match"
        assert cleared_match["formally_cleared_match"] is True
        assert cleared_match["approval_blocking"] is False

        no_evidence_reference_clear = derive_screening_truth(
            {
                "matched": True,
                "results": [{"name": "False Positive", "is_sanctioned": True}],
                "source": "sumsub",
                "api_status": "live",
                "review_disposition": "false_positive_cleared",
                "review_disposition_code": "false_positive_cleared",
                "review_rationale": "Officer confirmed this provider hit is not the subject.",
                "reviewer_id": "co001",
                "reviewed_at": "2026-04-22T10:00:00Z",
                "audit_confirmed": True,
            },
            required=True,
        )
        assert no_evidence_reference_clear["formally_cleared_match"] is True
        assert no_evidence_reference_clear["approval_blocking"] is False

    def test_legacy_status_value_never_clear_for_pending(self):
        from screening_state import legacy_status_value
        assert legacy_status_value("pending_provider", False) == "pending"
        assert legacy_status_value("pending_provider", None) == "pending"
        assert legacy_status_value("not_started", False) == "pending"
        assert legacy_status_value("not_configured", None) == "not_configured"
        assert legacy_status_value("failed", None) == "unavailable"
        assert legacy_status_value("completed_clear", False) == "clear"
        assert legacy_status_value("completed_match", True) == "match"

    def test_state_label_surfaces_declared_pep(self):
        from screening_state import state_label
        # Declared PEP must be visible even when provider is pending.
        assert "Declared PEP" in state_label("pending_provider", declared_pep=True)
        # Declared PEP must remain visible even when provider is clear.
        assert "Declared PEP" in state_label("completed_clear", declared_pep=True)
        # Without declared PEP, label is plain.
        assert state_label("completed_clear", declared_pep=False) == "No Match"


class TestScreeningTerminalitySummary:
    def test_clean_terminal_clear_is_not_material_screening_concern(self):
        from screening_state import build_screening_terminality_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "any_pep_hits": False,
            "any_sanctions_hits": False,
            "has_adverse_media_hit": None,
            "has_company_screening_hit": False,
            "total_hits": 0,
            "any_non_terminal_subject": False,
            "company_screening_state": "completed_clear",
            "company_screening": {
                "source": "complyadvantage",
                "api_status": "live",
                "matched": False,
                "results": [],
            },
            "director_screenings": [
                {
                    "person_name": "Clean Director",
                    "has_pep_hit": False,
                    "has_sanctions_hit": False,
                    "has_adverse_media_hit": None,
                    "provider_detected_pep": False,
                    "screening_state": "completed_clear",
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "live",
                        "matched": False,
                        "results": [],
                    },
                }
            ],
            "ubo_screenings": [],
        }

        summary = build_screening_terminality_summary(report)
        assert summary["terminal"] is True
        assert summary["has_terminal_match"] is False
        assert summary["has_non_terminal"] is False

    def test_non_material_provider_profile_does_not_become_material_match(self):
        from screening_state import build_screening_terminality_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "any_pep_hits": False,
            "any_sanctions_hits": False,
            "has_adverse_media_hit": None,
            "has_company_screening_hit": False,
            "total_hits": 1,
            "any_non_terminal_subject": False,
            "company_screening_state": "completed_clear",
            "company_screening": {
                "source": "complyadvantage",
                "api_status": "live",
                "matched": False,
                "results": [],
            },
            "director_screenings": [
                {
                    "person_name": "False Positive Profile",
                    "has_pep_hit": False,
                    "has_sanctions_hit": False,
                    "has_adverse_media_hit": None,
                    "provider_detected_pep": False,
                    "screening_state": "completed_match",
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "live",
                        "matched": True,
                        "results": [{"name": "False Positive Profile", "match_category": "other"}],
                    },
                }
            ],
            "ubo_screenings": [],
        }

        summary = build_screening_terminality_summary(report)
        assert summary["terminal"] is True
        assert summary["has_terminal_match"] is False
        assert summary["has_non_terminal"] is False

    def test_non_terminal_screening_is_not_material_screening_concern(self):
        from screening_state import build_screening_terminality_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "total_hits": 0,
            "any_non_terminal_subject": True,
            "director_screenings": [
                {
                    "person_name": "Pending Director",
                    "screening_state": "pending_provider",
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "pending",
                        "matched": False,
                        "results": [],
                    },
                }
            ],
            "ubo_screenings": [],
        }

        summary = build_screening_terminality_summary(report)
        assert summary["terminal"] is False
        assert summary["has_terminal_match"] is False
        assert summary["has_non_terminal"] is True

    def test_truth_summary_blocks_sandbox_and_simulated_from_defensible_clear(self):
        from screening_state import build_screening_truth_summary

        for api_status, expected_state in (("sandbox", "sandbox_provider"), ("simulated", "simulated_fallback")):
            report = {
                "screened_at": "2026-05-10T10:00:00Z",
                "company_screening": {
                    "found": True,
                    "source": "opencorporates",
                    "sanctions": {
                        "matched": False,
                        "results": [],
                        "source": api_status,
                        "api_status": api_status,
                    },
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "kyc_applicants": [],
            }
            summary = build_screening_truth_summary(report)
            assert summary["canonical_state"] == expected_state
            assert summary["provider_mode"] == expected_state
            assert summary["terminal"] is False
            assert summary["defensible_clear"] is False
            assert summary["approval_blocking"] is True
            assert summary["approval_ready"] is False
            assert summary["screening_gate_ready"] is False
            assert summary["approval_blocked_reasons"]

    def test_pending_possible_match_metadata_is_not_terminal_match(self):
        from screening_state import build_screening_terminality_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "any_pep_hits": True,
            "any_sanctions_hits": False,
            "has_adverse_media_hit": None,
            "total_hits": 1,
            "any_non_terminal_subject": True,
            "director_screenings": [
                {
                    "person_name": "Possible Pending Match",
                    "has_pep_hit": True,
                    "has_sanctions_hit": False,
                    "has_adverse_media_hit": None,
                    "provider_detected_pep": True,
                    "screening_state": "pending_provider",
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "pending",
                        "matched": True,
                        "results": [{"name": "Possible Pending Match", "is_pep": True}],
                    },
                }
            ],
            "ubo_screenings": [],
        }

        summary = build_screening_terminality_summary(report)
        assert summary["terminal"] is False
        assert summary["has_non_terminal"] is True

    def test_completed_match_review_false_positive_clearance_unblocks_truth_summary(self):
        from screening_state import build_screening_truth_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "company_screening": {
                "found": True,
                "sanctions": {
                    "matched": True,
                    "results": [{"name": "Watchlist Hit", "is_sanctioned": True}],
                    "source": "sumsub",
                    "api_status": "live",
                },
            },
            "director_screenings": [],
            "ubo_screenings": [],
        }
        reviews = [{
            "subject_type": "entity",
            "subject_name": "Reviewed Match Ltd",
            "disposition": "cleared",
            "disposition_code": "false_positive_cleared",
            "rationale": "Officer compared provider details and confirmed this is a false positive.",
            "notes": "Provider case CA-FP-TRUTH-001 and registry extract retained.",
            "evidence_reference": "Provider case CA-FP-TRUTH-001 and registry extract retained.",
            "reviewer_name": "Compliance Officer",
            "created_at": "2026-05-10T11:00:00Z",
            "audit_confirmed": True,
            "requires_four_eyes": False,
        }]

        summary = build_screening_truth_summary(
            report,
            {"company_name": "Reviewed Match Ltd"},
            reviews,
        )

        assert summary["canonical_state"] == "completed_match"
        assert summary["has_formally_cleared_match"] is True
        assert summary["has_uncleared_completed_match"] is False
        assert summary["approval_blocking"] is False
        assert summary["defensible_clear"] is True
        assert summary["screening_gate_ready"] is True
        assert summary["approval_ready"] is True
        assert summary["approval_blocked_reasons"] == []

    def test_uncleared_completed_match_is_not_approval_ready_when_blocking(self):
        from screening_state import build_screening_truth_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "company_screening": {
                "found": True,
                "sanctions": {
                    "matched": True,
                    "results": [{"name": "Watchlist Hit", "is_sanctioned": True}],
                    "source": "complyadvantage",
                    "api_status": "live",
                },
            },
            "director_screenings": [],
            "ubo_screenings": [],
        }

        summary = build_screening_truth_summary(
            report,
            {"company_name": "Uncleared Match Ltd"},
            [],
        )

        assert summary["canonical_state"] == "completed_match"
        assert summary["screening_terminal"] is True
        assert summary["screening_provider_clear"] is False
        assert summary["defensible_clear"] is False
        assert summary["approval_blocking"] is True
        assert summary["approval_ready"] is False
        assert summary["approval_gate_ready"] is False
        assert summary["screening_gate_ready"] is False
        assert summary["has_uncleared_completed_match"] is True
        assert summary["approval_blocked_reasons"]

    @pytest.mark.parametrize(
        "disposition,code",
        [
            ("escalated", "true_match"),
            ("escalated", "material_concern"),
            ("follow_up_required", "needs_more_information"),
            ("escalated", "escalated_to_edd"),
        ],
    )
    def test_completed_match_blocking_dispositions_remain_blocking(self, disposition, code):
        from screening_state import build_screening_truth_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "company_screening": {
                "found": True,
                "sanctions": {
                    "matched": True,
                    "results": [{"name": "Watchlist Hit", "is_sanctioned": True}],
                    "source": "sumsub",
                    "api_status": "live",
                },
            },
            "director_screenings": [],
            "ubo_screenings": [],
        }
        reviews = [{
            "subject_type": "entity",
            "subject_name": "Reviewed Match Ltd",
            "disposition": disposition,
            "disposition_code": code,
            "rationale": "Officer disposition keeps this match unresolved for approval.",
            "notes": "Provider case CA-BLOCK-001 retained.",
            "reviewer_name": "Compliance Officer",
            "created_at": "2026-05-10T11:00:00Z",
            "requires_four_eyes": False,
        }]

        summary = build_screening_truth_summary(
            report,
            {"company_name": "Reviewed Match Ltd"},
            reviews,
        )

        assert summary["canonical_state"] == "completed_match"
        assert summary["approval_blocking"] is True
        assert summary["approval_ready"] is False
        assert summary["screening_gate_ready"] is False
        assert summary["has_uncleared_completed_match"] is True

    def test_terminal_material_match_is_preserved(self):
        from screening_state import build_screening_terminality_summary

        report = {
            "screened_at": "2026-05-10T10:00:00Z",
            "any_pep_hits": True,
            "any_sanctions_hits": False,
            "total_hits": 1,
            "any_non_terminal_subject": False,
            "director_screenings": [
                {
                    "person_name": "Provider PEP",
                    "has_pep_hit": True,
                    "has_sanctions_hit": False,
                    "has_adverse_media_hit": None,
                    "screening_state": "completed_match",
                    "screening": {
                        "source": "complyadvantage",
                        "api_status": "live",
                        "matched": True,
                        "results": [{"name": "Provider PEP", "is_pep": True}],
                    },
                }
            ],
            "ubo_screenings": [],
        }

        summary = build_screening_terminality_summary(report)
        assert summary["terminal"] is True
        assert summary["has_terminal_match"] is True
        assert summary["has_non_terminal"] is False


# ── Normalizer: pending must not look clear ───────────────────────────


class TestNormalizerSemantics:
    def _raw_with_state(self, person_api_status, company_api_status):
        return {
            "screened_at": "2026-04-22T00:00:00",
            "company_screening": {
                "found": True,
                "source": "opencorporates",
                "api_status": "live",
                "sanctions": {
                    "matched": False, "results": [],
                    "source": "sumsub", "api_status": company_api_status,
                },
            },
            "director_screenings": [
                {
                    "person_name": "Test Director",
                    "person_type": "director",
                    "nationality": "MU",
                    "declared_pep": "No",
                    "screening": {
                        "matched": False, "results": [],
                        "source": "sumsub", "api_status": person_api_status,
                    },
                }
            ],
            "ubo_screenings": [],
            "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
            "kyc_applicants": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

    def test_pending_person_does_not_look_clear(self):
        from screening_normalizer import normalize_screening_report
        norm = normalize_screening_report(self._raw_with_state("pending", "live"))
        d = norm["director_screenings"][0]
        assert d["has_pep_hit"] is None
        assert d["has_sanctions_hit"] is None
        assert d["screening_state"] == "pending_provider"
        assert norm["any_non_terminal_subject"] is True

    def test_not_configured_company_stays_explicit(self):
        from screening_normalizer import normalize_screening_report
        norm = normalize_screening_report(self._raw_with_state("live", "not_configured"))
        assert norm["company_screening_state"] == "not_configured"
        assert norm["has_company_screening_hit"] is None
        assert norm["any_non_terminal_subject"] is True

    def test_failed_company_stays_explicit(self):
        from screening_normalizer import normalize_screening_report
        norm = normalize_screening_report(self._raw_with_state("live", "error"))
        assert norm["company_screening_state"] == "failed"
        assert norm["has_company_screening_hit"] is None

    def test_terminal_clear_still_works(self):
        from screening_normalizer import normalize_screening_report
        norm = normalize_screening_report(self._raw_with_state("live", "live"))
        d = norm["director_screenings"][0]
        assert d["has_pep_hit"] is False
        assert d["has_sanctions_hit"] is False
        assert d["screening_state"] == "completed_clear"
        assert norm["company_screening_state"] == "completed_clear"
        assert norm["has_company_screening_hit"] is False
        assert norm["any_non_terminal_subject"] is False

    def test_declared_pep_persists_through_pending(self):
        from screening_normalizer import normalize_screening_report
        raw = self._raw_with_state("pending", "live")
        raw["director_screenings"][0]["declared_pep"] = "Yes"
        norm = normalize_screening_report(raw)
        d = norm["director_screenings"][0]
        # Declared PEP signal must be preserved.
        assert d["declared_pep"] == "Yes"
        # And requires_review must be True even though provider state is
        # not yet terminal.
        assert d["requires_review"] is True


# ── Screening queue serializer: pending must not render as clear ──────


def _seed_app(db, app_id, ref, prescreening):
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, ref, "client_x", ref + " Co", "Mauritius", "Technology",
         "SME", "pricing_review", json.dumps(prescreening)),
    )


class TestScreeningQueueSerializer:
    def test_pending_person_is_not_rendered_as_clear(self, db, temp_db):
        from server import _build_screening_queue_payload
        _seed_app(db, "app_pending_person", "ARF-PEND-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": "live"},
                },
                "director_screenings": [
                    {
                        "person_name": "Pending Person",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {"matched": False, "results": [],
                                      "source": "sumsub", "api_status": "pending"},
                    }
                ],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) "
                   "VALUES (?, ?, ?, ?)",
                   ("app_pending_person", "Pending Person", "MU", "No"))
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        person = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-PEND-1" and r["subject_name"] == "Pending Person")
        # Critical: must NOT collapse pending into clear / No Match.
        assert person["watchlist_status"] == "pending"
        assert person["pep_screening_status"] == "pending"
        assert person["screening_state"] == "pending_provider"
        assert person["status_key"] == "screening_in_progress"
        assert person["status_label"] == "Screening In Progress"
        assert person["review_required"] is False

    def test_not_configured_company_is_rendered_explicitly(self, db, temp_db):
        from server import _build_screening_queue_payload
        _seed_app(db, "app_ncfg", "ARF-NCFG-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": "not_configured"},
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        entity = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-NCFG-1" and r["subject_type"] == "entity")
        assert entity["watchlist_status"] == "not_configured"
        assert entity["screening_state"] == "not_configured"
        assert entity["status_key"] == "failed"
        assert entity["status_label"] == "Failed"
        assert entity["review_required"] is True

    @pytest.mark.parametrize(
        "api_status,expected_key,expected_label,expected_mode",
        [
            ("simulated", "screening_in_progress", "Screening In Progress", "simulated_fallback"),
            ("sandbox", "screening_in_progress", "Screening In Progress", "sandbox_provider"),
        ],
    )
    def test_simulated_and_sandbox_company_labels_are_explicit(
        self, db, temp_db, api_status, expected_key, expected_label, expected_mode
    ):
        from server import _build_screening_queue_payload
        _seed_app(db, f"app_{api_status}", f"ARF-{api_status.upper()}-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": api_status,
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": api_status},
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        entity = next(r for r in payload["rows"]
                      if r["application_ref"] == f"ARF-{api_status.upper()}-1"
                      and r["subject_type"] == "entity")
        assert entity["status_key"] == expected_key
        assert entity["status_label"] == expected_label
        assert entity["provider_mode"] == expected_mode
        assert entity["defensible_clear"] is False
        assert entity["review_required"] is False

    def test_failed_provider_for_person_is_rendered_as_unavailable(self, db, temp_db):
        from server import _build_screening_queue_payload
        _seed_app(db, "app_failed", "ARF-FAIL-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": "live"},
                },
                "director_screenings": [
                    {
                        "person_name": "Failed Person",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {"matched": False, "results": [],
                                      "source": "sumsub", "api_status": "error"},
                    }
                ],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) "
                   "VALUES (?, ?, ?, ?)",
                   ("app_failed", "Failed Person", "MU", "No"))
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        person = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-FAIL-1" and r["subject_name"] == "Failed Person")
        assert person["watchlist_status"] == "unavailable"
        assert person["screening_state"] == "failed"
        assert person["status_key"] == "failed"
        assert person["status_label"] == "Failed"
        assert person["review_required"] is True

    def test_declared_pep_visible_before_provider_terminal(self, db, temp_db):
        from server import _build_screening_queue_payload
        _seed_app(db, "app_dp_pending", "ARF-DPP-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": "live"},
                },
                "director_screenings": [
                    {
                        "person_name": "Declared Pending PEP",
                        "person_type": "director",
                        "declared_pep": "Yes",
                        "screening": {"matched": False, "results": [],
                                      "source": "sumsub", "api_status": "pending"},
                    }
                ],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) "
                   "VALUES (?, ?, ?, ?)",
                   ("app_dp_pending", "Declared Pending PEP", "MU", "Yes"))
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        person = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-DPP-1"
                      and r["subject_name"] == "Declared Pending PEP")
        # Both signals must be visible:
        assert person["pep_declared_status"] == "declared"
        assert person["screening_state"] == "pending_provider"
        # Status must not be "Awaiting Screening" or "Clear"; declared PEP
        # makes the row officer-reviewable while raw context keeps the
        # provider pending detail visible.
        assert person["status_key"] == "review_required"
        assert person["status_label"] == "Review Required"
        assert person["review_required"] is True
        assert "Declared PEP" in person["entity_context"]

    def test_terminal_clear_low_risk_path_still_works(self, db, temp_db):
        from server import _build_screening_queue_payload
        _seed_app(db, "app_clean", "ARF-CLEAN-1", {
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": "live"},
                },
                "director_screenings": [
                    {
                        "person_name": "Clean Director",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {"matched": False, "results": [],
                                      "source": "sumsub", "api_status": "live"},
                    }
                ],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        })
        db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) "
                   "VALUES (?, ?, ?, ?)",
                   ("app_clean", "Clean Director", "MU", "No"))
        db.commit()

        payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
        person = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-CLEAN-1"
                      and r["subject_name"] == "Clean Director")
        assert person["watchlist_status"] == "clear"
        assert person["pep_screening_status"] == "clear"
        assert person["screening_state"] == "completed_clear"
        assert person["status_key"] == "clear"
        assert person["status_label"] == "Clear"
        assert person["review_required"] is False

        entity = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-CLEAN-1" and r["subject_type"] == "entity")
        assert entity["watchlist_status"] == "clear"
        assert entity["screening_state"] == "completed_clear"
        assert entity["status_key"] == "clear"


# ── Memo handler: must not overclaim screening completion ─────────────


def _build_memo_inputs(api_status, declared_pep="No", matched=False, match_cleared=False):
    """Construct minimal app/directors/ubos/documents for memo build."""
    results = []
    if matched:
        results = [{
            "name": "Watchlist Hit",
            "is_sanctioned": True,
            "match_categories": ["sanctions"],
        }]
    sanctions_record = {
        "matched": matched,
        "results": results,
        "source": "sumsub",
        "api_status": api_status,
    }
    if match_cleared:
        sanctions_record.update({
            "review_disposition": "false_positive_cleared",
            "review_disposition_code": "false_positive_cleared",
            "review_rationale": "Officer confirmed identity mismatch against provider evidence.",
            "review_evidence_reference": "Provider case CA-MEMO-001 and registry evidence.",
            "reviewer_id": "co001",
            "reviewed_at": "2026-04-22T10:00:00Z",
            "audit_confirmed": True,
        })
    app = {
        "id": "app_memo",
        "ref": "ARF-MEMO",
        "company_name": "Memo Test Co",
        "brn": "C12345",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "ownership_structure": "Single tier",
        "operating_countries": "Mauritius",
        "incorporation_date": "2020-01-01",
        "business_activity": "Software",
        "source_of_funds": "Trading revenue",
        "expected_volume": "USD 100,000",
        "risk_level": "LOW",
        "risk_score": 25,
        "risk_escalations": "[]",
        "assigned_to": "Officer A",
        "prescreening_data": json.dumps({
            "screening_report": {
                "screened_at": "2026-04-22T00:00:00",
                "screening_mode": "live",
                "company_screening": {
                    "found": True, "source": "opencorporates",
                    "sanctions": sanctions_record,
                },
                "director_screenings": [
                    {
                        "person_name": "Memo Director",
                        "person_type": "director",
                        "declared_pep": declared_pep,
                        "screening": {"matched": False, "results": [],
                                      "source": "sumsub", "api_status": api_status},
                    }
                ],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 1 if matched else 0,
                "any_sanctions_hits": matched,
                "has_company_screening_hit": matched,
            }
        }),
    }
    directors = [{
        "full_name": "Memo Director", "nationality": "Mauritius",
        "is_pep": declared_pep, "ownership_pct": 0,
    }]
    ubos = [{
        "full_name": "Memo UBO", "nationality": "Mauritius",
        "is_pep": "No", "ownership_pct": 100,
    }]
    documents = []
    return app, directors, ubos, documents


def _flatten_text(memo):
    """Concatenate all string content from the memo for substring asserts."""
    chunks = []

    def _walk(x):
        if isinstance(x, str):
            chunks.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(memo)
    return "\n".join(chunks).lower()


def _flatten_sections_text(memo):
    return _flatten_text(memo.get("sections", {}))


def _executive_summary_text(memo):
    return (memo.get("sections", {}).get("executive_summary", {}).get("content") or "").lower()


class TestMemoTruthfulness:
    def test_completed_match_memo_renders_match_not_clean(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("live", matched=True)
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "completed_match"
        assert summary["screening_result"] == "match"
        assert summary["defensible_clear"] is False
        assert summary["approval_blocking"] is True

        text = _flatten_sections_text(memo)
        assert "match" in text
        assert "escalation" in text or "officer review" in text
        for forbidden in (
            "no matches were returned",
            "no matches returned",
            "clean sanctions screening",
            "clean screening results",
            "screening results, verified documentation",
        ):
            assert forbidden not in text

        executive = _executive_summary_text(memo)
        for forbidden in ("no material concerns", "clean", "low-risk profile"):
            assert forbidden not in executive
        assert "match" in executive
        assert "material screening concern" in executive or "officer review" in executive or "escalation" in executive

    @pytest.mark.parametrize(
        "api_status,expected_phrase",
        [
            ("simulated", "simulated"),
            ("sandbox", "sandbox"),
            ("pending", "not yet returned a terminal provider result"),
            ("not_configured", "not configured"),
            ("failed", "failed or was unavailable"),
        ],
    )
    def test_unsafe_screening_executive_summary_never_uses_clean_low_risk_wording(
        self, api_status, expected_phrase
    ):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs(api_status)
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        executive = _executive_summary_text(memo)
        assert expected_phrase in executive
        for forbidden in ("no material concerns", "clean", "low-risk profile"):
            assert forbidden not in executive

    def test_clean_terminal_clear_executive_summary_may_use_clean_low_risk_wording(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("live")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "completed_clear"
        assert summary["defensible_clear"] is True
        assert summary["approval_blocking"] is False
        executive = _executive_summary_text(memo)
        assert "low-risk profile" in executive
        assert "clean sanctions screening" in executive

    def test_formally_cleared_completed_match_executive_summary_is_not_unresolved_escalation(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("live", matched=True, match_cleared=True)
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "completed_match"
        assert summary["has_formally_cleared_match"] is True
        assert summary["has_uncleared_completed_match"] is False
        assert summary["approval_blocking"] is False
        executive = _executive_summary_text(memo)
        assert "formally cleared" in executive
        assert "unresolved screening escalation" in executive
        assert "requiring officer review" not in executive
        assert "escalation before approval reliance" not in executive
        assert "no material concerns" not in executive
        assert "low-risk profile" not in executive

    def test_db_screening_review_false_positive_clearance_flows_into_memo_truth(self):
        from memo_handler import build_compliance_memo

        app, directors, ubos, docs = _build_memo_inputs("live", matched=True)
        app["screening_reviews"] = [{
            "subject_type": "entity",
            "subject_name": "Memo Test Co",
            "disposition": "cleared",
            "disposition_code": "false_positive_cleared",
            "rationale": "Officer confirmed this provider hit belongs to a different entity after registry review.",
            "notes": "Provider case CA-MEMO-REVIEW-001 and registry extract retained.",
            "evidence_reference": "Provider case CA-MEMO-REVIEW-001 and registry extract retained.",
            "reviewer_name": "Compliance Officer",
            "created_at": "2026-05-10T11:00:00Z",
            "audit_confirmed": True,
            "requires_four_eyes": False,
        }]

        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "completed_match"
        assert summary["has_formally_cleared_match"] is True
        assert summary["approval_blocking"] is False
        text = _flatten_sections_text(memo)
        assert "formally cleared" in text
        assert "not a no-match result" in text

    def test_memo_does_not_claim_completed_screening_when_pending(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("pending")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["terminal"] is False
        assert summary["has_non_terminal"] is True
        assert summary["company_state"] == "pending_provider"
        assert summary["person_states"] == ["pending_provider"]

        text = _flatten_text(memo)
        # Must explicitly call out non-terminal screening.
        assert "not complete" in text or "not yet complete" in text
        # Must NOT make a "clean sanctions screening" claim while pending.
        assert "clean sanctions screening" not in text

    def test_simulated_memo_is_non_reliance_and_not_approval_recommendation(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("simulated")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "simulated_fallback"
        assert summary["defensible_clear"] is False
        assert memo["metadata"]["approval_recommendation"] == "REVIEW"
        assert memo["metadata"]["decision_label"] == "SCREENING RESOLUTION REQUIRED"

        text = _flatten_sections_text(memo)
        assert "simulated" in text
        assert "not production-live" in text or "not approval recommendation" in text
        assert "not recommended for approval" in text
        assert "this application is recommended for approval" not in text
        assert "clean sanctions screening" not in text

    def test_screening_truth_block_does_not_soften_reject_or_edd_escalation(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("simulated")
        app["risk_level"] = "VERY_HIGH"
        app["risk_score"] = 95

        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        assert memo["metadata"]["approval_recommendation"] in {"REJECT", "ESCALATE_TO_EDD"}
        assert memo["metadata"]["approval_recommendation"] != "REVIEW"
        assert memo["metadata"]["decision_label"] != "SCREENING RESOLUTION REQUIRED"
        text = _flatten_sections_text(memo)
        assert "simulated" in text
        assert "this application is recommended for approval" not in text

    def test_sandbox_memo_is_not_production_live(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("sandbox")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["canonical_state"] == "sandbox_provider"
        assert summary["defensible_clear"] is False
        text = _flatten_sections_text(memo)
        assert "sandbox" in text
        assert "not production-live" in text
        assert "clean sanctions screening" not in text

    @pytest.mark.parametrize(
        "api_status,expected_phrase",
        [
            ("pending", "pending"),
            ("not_configured", "not configured"),
            ("failed", "failed"),
        ],
    )
    def test_non_terminal_memo_wording_matches_canonical_state(self, api_status, expected_phrase):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs(api_status)
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        text = _flatten_sections_text(memo)
        assert expected_phrase in text
        assert "clean sanctions screening" not in text
        assert "this application is recommended for approval" not in text

    def test_memo_does_not_claim_completed_screening_when_not_configured(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("not_configured")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["terminal"] is False
        assert summary["has_not_configured"] is True
        text = _flatten_text(memo)
        assert "not configured" in text
        assert "clean sanctions screening" not in text

    def test_memo_clean_terminal_still_claims_completion(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("live")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["terminal"] is True
        assert summary["has_non_terminal"] is False
        assert summary["defensible_clear"] is True
        text = _flatten_text(memo)
        # Standard clean-claim phrasing remains for true terminal-clear.
        assert "clean sanctions screening" in text
        assert "sanctions screening not complete" not in text
        assert "pep screening not complete" not in text

    def test_memo_preserves_declared_pep_when_provider_pending(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("pending", declared_pep="Yes")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["declared_pep_count"] == 1
        # The PEP must still be reflected in key_findings.
        kf = " ".join(memo["metadata"]["key_findings"]).lower()
        assert "memo director" in kf and "pep" in kf

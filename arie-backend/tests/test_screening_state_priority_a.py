"""
Priority A — Screening State Semantics: truthful, fail-closed remediation.
==========================================================================

Audit-evidence regression tests for the canonical screening state model.
These tests lock in the controls-truthfulness fix:

* Sumsub subjects in init / pending / created / error / not_configured
  states must NOT render as "Clear" or "No Provider Match".
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
        assert state_label("completed_clear", declared_pep=False) == "No Provider Match"


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
        # Critical: must NOT collapse pending into clear / No Provider Match.
        assert person["watchlist_status"] == "pending"
        assert person["pep_screening_status"] == "pending"
        assert person["screening_state"] == "pending_provider"
        assert person["status_key"] == "screening_pending"
        assert person["status_label"] == "Screening Pending Provider"
        assert person["review_required"] is True

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
        assert entity["status_key"] == "screening_not_configured"
        assert entity["status_label"] == "Screening Not Configured"
        assert entity["review_required"] is True

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
        assert person["status_key"] == "screening_unavailable"
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
        # Status must not be "Awaiting Screening" — it must call out
        # the declared PEP AND the pending provider state.
        assert "Declared PEP" in person["status_label"]
        assert "Pending" in person["status_label"]
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
        assert person["status_key"] == "screened_no_match"
        assert person["status_label"] == "No Provider Match"
        assert person["review_required"] is False

        entity = next(r for r in payload["rows"]
                      if r["application_ref"] == "ARF-CLEAN-1" and r["subject_type"] == "entity")
        assert entity["watchlist_status"] == "clear"
        assert entity["screening_state"] == "completed_clear"
        assert entity["status_key"] == "screened_no_match"


# ── Memo handler: must not overclaim screening completion ─────────────


def _build_memo_inputs(api_status, declared_pep="No"):
    """Construct minimal app/directors/ubos/documents for memo build."""
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
                    "sanctions": {"matched": False, "results": [],
                                  "source": "sumsub", "api_status": api_status},
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
                "total_hits": 0,
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


class TestMemoTruthfulness:
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
        text = _flatten_text(memo)
        # Standard clean-claim phrasing remains for true terminal-clear.
        assert "clean sanctions screening" in text
        assert "not complete" not in text

    def test_memo_preserves_declared_pep_when_provider_pending(self):
        from memo_handler import build_compliance_memo
        app, directors, ubos, docs = _build_memo_inputs("pending", declared_pep="Yes")
        memo, _, _, _ = build_compliance_memo(app, directors, ubos, docs)

        summary = memo["metadata"]["screening_state_summary"]
        assert summary["declared_pep_count"] == 1
        # The PEP must still be reflected in key_findings.
        kf = " ".join(memo["metadata"]["key_findings"]).lower()
        assert "memo director" in kf and "pep" in kf

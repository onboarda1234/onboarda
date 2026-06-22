"""
Tests for PR-CM-APPROVAL-PRECONDITIONS-1.

Covers the approval gate added to Change Management:
- maker/checker (tier1/tier2 non-waivable; tier3 self-approve allowed)
- screening / risk precondition results block approval until recorded
- screening result must be EVIDENCE-BACKED (no blank "recorded" marker)
- a recorded unresolved/indeterminate screening match blocks approval (non-waivable)
- risk result requires a risk level
- stale clearance (content change invalidates a recorded result)
- SCO/Admin override of waivable blockers (reason mandatory), never maker/checker
- both approval paths gated (approve_change_request + update_change_request_status)
- audit rows for recorded / blocked / override
"""

import secrets
from datetime import datetime, timezone
from decimal import Decimal


def _get_cm():
    import change_management as cm
    return cm


class _DBWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False

    def execute(self, sql, params=None):
        return self._conn.execute(sql, params) if params else self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


CREATOR = {"sub": "co-creator", "name": "Creator CO", "role": "co"}
SCO = {"sub": "sco-checker", "name": "SCO Checker", "role": "sco"}
ADMIN = {"sub": "admin-checker", "name": "Admin Checker", "role": "admin"}
ANALYST = {"sub": "analyst-1", "name": "Analyst", "role": "analyst"}

# Explicit evidence an officer can attest to when no persisted report is referenced.
CLEAN_SCREEN = {"screening_ref": "ext-screen-1", "screened_at": "2026-01-01T00:00:00Z", "unresolved_match": False}
RISK_RESULT = {"risk_level": "MEDIUM"}


def _setup_app(raw_db, with_screen_report=False, risk_level="MEDIUM"):
    app_id = f"test-cm-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@t.com", "h", "Co"),
    )
    prescreen = ""
    if with_screen_report:
        import json
        prescreen = json.dumps({"screening_report": {"total_hits": 0, "sanctions": {"matched": False}}})
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "Co Ltd", "GB", "Tech", "Limited Company",
         "approved", risk_level, prescreen),
    )
    raw_db.commit()
    return app_id


def _make_cr(cm, wdb, app_id, materiality="tier1", creator=CREATOR):
    ct = {"tier1": "company_details", "tier2": "company_details", "tier3": "contact_detail_update"}[materiality]
    item = {"change_type": ct, "field_name": "company_name" if ct == "company_details" else "contact_email",
            "old_value": "Co Ltd", "new_value": "Co2 Ltd", "materiality": materiality}
    return cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice", "r", [item], creator)


def _to_pending(cm, wdb, req_id, user=CREATOR):
    cm.submit_change_request(wdb, req_id, user)
    cm.update_change_request_status(wdb, req_id, "triage_in_progress", user)
    cm.update_change_request_status(wdb, req_id, "ready_for_review", user)
    cm.update_change_request_status(wdb, req_id, "approval_pending", user)


def _record_both(cm, wdb, req_id, user=SCO):
    cm.record_precondition_result(wdb, req_id, "screening", user, result=CLEAN_SCREEN)
    cm.record_precondition_result(wdb, req_id, "risk", user, result=RISK_RESULT)


# ---------------------------------------------------------------------------
# Maker/checker
# ---------------------------------------------------------------------------

class TestMakerChecker:
    def test_tier1_creator_cannot_approve_own(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=SCO)
        _to_pending(cm, wdb, req["id"], SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert not ok
        assert "maker_checker_same_user" in err

    def test_tier2_creator_cannot_approve_own(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier2", creator=SCO)
        _to_pending(cm, wdb, req["id"], SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert not ok
        assert "maker_checker_same_user" in err

    def test_tier3_creator_can_self_approve(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier3", creator=SCO)
        _to_pending(cm, wdb, req["id"], SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], SCO)
        assert ok, err

    def test_maker_checker_not_overridable(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=SCO)
        _to_pending(cm, wdb, req["id"], SCO)
        _record_both(cm, wdb, req["id"], ADMIN)
        ok, err = cm.approve_change_request(
            wdb, req["id"], SCO,
            override_codes=["maker_checker_same_user"], override_reason="please",
        )
        assert not ok
        assert "maker_checker_same_user" in err


# ---------------------------------------------------------------------------
# Screening / risk preconditions
# ---------------------------------------------------------------------------

class TestPreconditions:
    def test_screening_and_risk_block_until_recorded(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert not ok
        assert "screening_required_uncleared" in err
        assert "risk_review_required_uncleared" in err

    def test_recorded_then_second_approver_succeeds(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        _record_both(cm, wdb, req["id"], SCO)
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert ok, err

    def test_patch_to_approved_is_also_gated(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        ok, err = cm.update_change_request_status(wdb, req["id"], "approved", ADMIN)
        assert not ok
        assert "blocked by preconditions" in err.lower()

    def test_stale_clearance_blocks(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        _record_both(cm, wdb, req["id"], SCO)
        # Mutate request content after clearance → invalidates the recorded results.
        db.execute(
            """INSERT INTO change_request_items (id, request_id, change_type, field_name, old_value, new_value, materiality, created_at)
               VALUES (?, ?, 'company_details', 'sector', 'Tech', 'Crypto', 'tier1', datetime('now'))""",
            (req["id"] + "-IX", req["id"]),
        )
        db.commit()
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert not ok
        assert "stale" in err.lower()


# ---------------------------------------------------------------------------
# Evidence-backed screening (the control that matters)
# ---------------------------------------------------------------------------

class TestScreeningEvidence:
    def test_screening_record_fails_without_evidence(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        # App has NO persisted screening report and no explicit evidence supplied.
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(wdb, req["id"], "screening", SCO)
        assert not ok
        assert "screening_result_evidence_missing" in err

    def test_approval_blocked_when_screening_never_recorded(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        cm.record_precondition_result(wdb, req["id"], "risk", SCO, result=RISK_RESULT)
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert not ok
        assert "screening_required_uncleared" in err
        # Non-waivable: cannot override a screening that was never performed.
        ok2, err2 = cm.approve_change_request(
            wdb, req["id"], ADMIN,
            override_codes=["screening_required_uncleared"], override_reason="x",
        )
        assert not ok2
        assert "screening_required_uncleared" in err2

    def test_persisted_clean_report_allows_record_and_clears(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        # App carries a persisted clean screening report AND a persisted risk level.
        req = _make_cr(cm, wdb, _setup_app(db, with_screen_report=True, risk_level="MEDIUM"), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        # NO explicit result objects — both results are derived from the persisted
        # application evidence (screening_report + applications.risk_level).
        ok, err = cm.record_precondition_result(wdb, req["id"], "screening", SCO)
        assert ok, err
        ok_r, err_r = cm.record_precondition_result(wdb, req["id"], "risk", SCO)
        assert ok_r, err_r
        # The recorded results reflect the real persisted evidence.
        detail = cm.get_change_request_detail(wdb, req["id"])
        import json
        results = json.loads(detail["precondition_results"]) if isinstance(detail.get("precondition_results"), str) else detail.get("precondition_results")
        assert results["screening"]["unresolved_match"] is False
        assert results["risk"]["risk_level"] == "MEDIUM"
        ok2, err2 = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert ok2, err2

    def test_persisted_risk_level_records_without_explicit_result(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db, risk_level="HIGH"), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", SCO)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        import json
        results = json.loads(detail["precondition_results"]) if isinstance(detail.get("precondition_results"), str) else detail.get("precondition_results")
        assert results["risk"]["risk_level"] == "HIGH"

    def test_persisted_risk_snapshot_sanitizes_non_json_primitives(self, db, monkeypatch):
        cm = _get_cm(); wdb = _DBWrapper(db)
        app_id = _setup_app(db, risk_level="LOW")
        req = _make_cr(cm, wdb, app_id, "tier1", creator=CREATOR)

        def odd_persisted_snapshot(_db, _app_id):
            assert _app_id == app_id
            return {
                "risk_level": b"VERY HIGH",
                "risk_score": Decimal("42.5"),
                "risk_computed_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            }

        monkeypatch.setattr(cm, "_app_risk_snapshot", odd_persisted_snapshot)
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", SCO)
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        import json
        results = json.loads(detail["precondition_results"]) if isinstance(detail.get("precondition_results"), str) else detail.get("precondition_results")
        assert results["risk"]["risk_level"] == "VERY_HIGH"
        assert results["risk"]["risk_score"] == 42.5
        assert results["risk"]["risk_computed_at"] == "2026-01-02T03:04:05+00:00"

    def test_persisted_risk_odd_shape_rejected_without_exception(self, db, monkeypatch):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db, risk_level="LOW"), "tier1", creator=CREATOR)

        class OddRiskLevel:
            def __str__(self):
                return "not-a-risk-level"

        monkeypatch.setattr(cm, "_app_risk_snapshot", lambda _db, _app_id: {"risk_level": OddRiskLevel()})
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", SCO)
        assert not ok
        assert "risk_result_invalid_level" in err

    def test_explicit_json_safe_risk_evidence_still_records(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db, risk_level=None), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(
            wdb, req["id"], "risk", SCO,
            result={"risk_level": "VERY HIGH", "risk_score": 71, "risk_computed_at": "2026-01-02T03:04:05Z"},
        )
        assert ok, err
        detail = cm.get_change_request_detail(wdb, req["id"])
        import json
        results = json.loads(detail["precondition_results"]) if isinstance(detail.get("precondition_results"), str) else detail.get("precondition_results")
        assert results["risk"]["risk_level"] == "VERY_HIGH"
        assert results["risk"]["risk_score"] == 71
        assert results["risk"]["risk_computed_at"] == "2026-01-02T03:04:05Z"

    def test_recorded_unresolved_match_blocks_and_not_overridable(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        # A real but adverse screening result: there IS an unresolved match.
        ok, err = cm.record_precondition_result(
            wdb, req["id"], "screening", SCO,
            result={"screening_ref": "ext-1", "screened_at": "2026-01-01T00:00:00Z", "unresolved_match": True},
        )
        assert ok, err
        cm.record_precondition_result(wdb, req["id"], "risk", SCO, result=RISK_RESULT)
        ok2, err2 = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert not ok2
        assert "screening_unresolved_match" in err2
        # Non-waivable.
        ok3, err3 = cm.approve_change_request(
            wdb, req["id"], ADMIN,
            override_codes=["screening_unresolved_match"], override_reason="please",
        )
        assert not ok3
        assert "screening_unresolved_match" in err3

    def test_indeterminate_match_blocks_non_waivable(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        cm.record_precondition_result(wdb, req["id"], "risk", SCO, result=RISK_RESULT)
        # Seed a 'recorded' screening result with an INDETERMINATE (null) match.
        # record_precondition_result() rejects this path at the API, so write it
        # directly to prove the approval gate still blocks on an indeterminate result.
        import json
        sig = cm._request_content_signature(wdb, req["id"])
        results = {"screening": {"result": "recorded", "unresolved_match": None,
                                 "content_sig": sig, "screening_ref": "x", "recorded_by": "sco"}}
        db.execute("UPDATE change_requests SET precondition_results = ? WHERE id = ?",
                   (json.dumps(results), req["id"]))
        db.commit()
        ok, err = cm.approve_change_request(wdb, req["id"], ADMIN)
        assert not ok
        assert "screening_result_indeterminate" in err
        # Non-waivable.
        ok2, err2 = cm.approve_change_request(
            wdb, req["id"], ADMIN,
            override_codes=["screening_result_indeterminate"], override_reason="x",
        )
        assert not ok2
        assert "screening_result_indeterminate" in err2

    def test_screening_report_with_ref_but_no_result_is_indeterminate(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        # Persisted report has a ref but NO total_hits / unresolved_matches /
        # sanctions.matched / status — i.e. no determinate signal.
        app_id = f"test-cm-{secrets.token_hex(4)}"
        cid = f"cl-{secrets.token_hex(4)}"
        import json
        db.execute("INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
                   (cid, f"{cid}@t.com", "h", "Co"))
        db.execute(
            """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, prescreening_data)
               VALUES (?, ?, ?, 'Co', 'GB', 'Tech', 'Limited Company', 'approved', 'MEDIUM', ?)""",
            (app_id, f"APP-{secrets.token_hex(4)}", cid, json.dumps({"screening_report": {"report_id": "abc123"}})),
        )
        db.commit()
        req = _make_cr(cm, wdb, app_id, "tier1", creator=CREATOR)
        # No explicit unresolved_match supplied → snapshot is indeterminate → record rejected.
        ok, err = cm.record_precondition_result(wdb, req["id"], "screening", SCO)
        assert not ok
        assert "screening_result_evidence_missing" in err or "indeterminate" in err.lower()

    def test_risk_record_requires_level(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        # App with no computed risk level, and no explicit risk_level supplied.
        req = _make_cr(cm, wdb, _setup_app(db, risk_level=None), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", SCO)
        assert not ok
        assert "risk_result_evidence_missing" in err

    def test_risk_record_rejects_unknown_risk_level(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", SCO, result={"risk_level": "banana"})
        assert not ok
        assert "risk_result_invalid_level" in err

    def test_cannot_record_precondition_on_terminal_request(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        _record_both(cm, wdb, req["id"], SCO)
        cm.approve_change_request(wdb, req["id"], ADMIN)  # → approved (terminal-for-recording)
        ok, err = cm.record_precondition_result(wdb, req["id"], "screening", SCO, result=CLEAN_SCREEN)
        assert not ok
        assert "precondition_locked" in err


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------

class TestOverride:
    def _risk_only_pending(self, cm, wdb, app):
        # tier1 CR, screening recorded clean, risk left outstanding (waivable).
        req = _make_cr(cm, wdb, app, "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        cm.record_precondition_result(wdb, req["id"], "screening", SCO, result=CLEAN_SCREEN)
        return req

    def test_sco_override_risk_with_reason_succeeds(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = self._risk_only_pending(cm, wdb, _setup_app(db))
        ok, err = cm.approve_change_request(
            wdb, req["id"], ADMIN,
            override_codes=["risk_review_required_uncleared"],
            override_reason="risk team unavailable; documented rationale",
        )
        assert ok, err

    def test_override_without_reason_blocked(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = self._risk_only_pending(cm, wdb, _setup_app(db))
        ok, err = cm.approve_change_request(
            wdb, req["id"], ADMIN,
            override_codes=["risk_review_required_uncleared"],
        )
        assert not ok
        assert "override_reason is required" in err.lower()

    def test_co_cannot_override(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        # tier2 so CO is allowed to approve by role, but not to override.
        req = _make_cr(cm, wdb, _setup_app(db), "tier2", creator=SCO)
        _to_pending(cm, wdb, req["id"], SCO)
        cm.record_precondition_result(wdb, req["id"], "screening", ADMIN, result=CLEAN_SCREEN)
        ok, err = cm.approve_change_request(
            wdb, req["id"], CREATOR,  # co
            override_codes=["risk_review_required_uncleared"],
            override_reason="x",
        )
        assert not ok
        assert "may not override" in err.lower()


# ---------------------------------------------------------------------------
# Recording guard + audit
# ---------------------------------------------------------------------------

class TestRecordingAndAudit:
    def test_analyst_cannot_record(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        ok, err = cm.record_precondition_result(wdb, req["id"], "risk", ANALYST, result=RISK_RESULT)
        assert not ok
        assert "not permitted" in err.lower() or "analyst" in err.lower()

    def test_audit_rows_recorded_blocked_override(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        events = []

        def audit(user, action, target, detail, **kw):
            events.append(action)

        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        _to_pending(cm, wdb, req["id"])
        # blocked
        cm.approve_change_request(wdb, req["id"], ADMIN, log_audit_fn=audit)
        assert "CM Approval Blocked" in events
        # recorded (clean screening, evidence-backed)
        cm.record_precondition_result(wdb, req["id"], "screening", SCO, result=CLEAN_SCREEN, log_audit_fn=audit)
        assert "CM Precondition Recorded" in events
        # override risk (still outstanding) → override applied + approved
        ok, err = cm.approve_change_request(
            wdb, req["id"], ADMIN, log_audit_fn=audit,
            override_codes=["risk_review_required_uncleared"], override_reason="documented",
        )
        assert ok, err
        assert "CM Approval Override" in events


# ---------------------------------------------------------------------------
# Detail surface
# ---------------------------------------------------------------------------

class TestDetailApproval:
    def test_detail_exposes_preconditions_met_and_blockers(self, db):
        cm = _get_cm(); wdb = _DBWrapper(db)
        req = _make_cr(cm, wdb, _setup_app(db), "tier1", creator=CREATOR)
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["approval"]["preconditions_met"] is False
        codes = [b["code"] for b in detail["approval"]["blockers"]]
        assert "screening_required_uncleared" in codes
        assert "risk_review_required_uncleared" in codes

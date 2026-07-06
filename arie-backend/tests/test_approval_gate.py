import json
import uuid
from datetime import datetime, timedelta, timezone


def _insert_application_and_memo(
    db,
    *,
    validation_status="pass",
    supervisor_status="CONSISTENT",
    review_status="approved",
    risk_level="MEDIUM",
    status="submitted_to_compliance",
    prescreening_data=None,
    onboarding_lane=None,
    final_risk_level=None,
    base_risk_level=None,
    pre_approval_decision=None,
    memo_data=None,
    approval_reason=None,
    documents_ready=True,
):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-approval-gate-{suffix}"
    app_ref = f"ARF-APPROVAL-GATE-{suffix}"
    now = datetime.now(timezone.utc)
    screened_at = now.strftime("%Y-%m-%dT%H:%M:%S")
    valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-approval-gate-{suffix}",
            "Approval Gate Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            status,
            risk_level,
            80 if risk_level in ("HIGH", "VERY_HIGH") else 45,
            json.dumps(prescreening_data if prescreening_data is not None else {
                "screening_report": {
                    "screening_mode": "live",
                    "screened_at": screened_at,
                    "company_screening": {
                        "provider": "complyadvantage",
                        "source": "complyadvantage",
                        "api_status": "live",
                        "matched": False,
                        "results": [],
                        "provider_references": {"case_id": "ca-clean-approval-gate"},
                    },
                    "company_registry": {"api_status": "live"},
                    "ip_geolocation": {"api_status": "live"},
                },
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            }),
        ),
    )
    db.execute(
        """
        UPDATE applications
           SET onboarding_lane = COALESCE(?, onboarding_lane),
               final_risk_level = COALESCE(?, final_risk_level),
               base_risk_level = COALESCE(?, base_risk_level),
               pre_approval_decision = COALESCE(?, pre_approval_decision)
         WHERE id = ?
        """,
        (
            onboarding_lane,
            final_risk_level,
            base_risk_level,
            pre_approval_decision,
            app_id,
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, approval_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps(memo_data or {"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            review_status,
            8.5,
            validation_status,
            supervisor_status,
            approval_reason if approval_reason is not None else (
                "Fixture approval reason" if review_status == "approved" else None
            ),
        ),
    )
    if documents_ready:
        verified_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        for doc_type in (
            "cert_inc",
            "memarts",
            "reg_sh",
            "reg_dir",
            "fin_stmt",
            "poa",
            "board_res",
            "structure_chart",
        ):
            doc_id = f"doc-approval-gate-{suffix}-{doc_type}"
            db.execute(
                """
                INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, slot_key,
                 verification_status, verification_results, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?)
                """,
                (
                    doc_id,
                    app_id,
                    doc_type,
                    f"{doc_type}.pdf",
                    f"/tmp/{doc_type}.pdf",
                    f"entity:{doc_type}",
                    json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": verified_at}),
                    verified_at,
                ),
            )
            db.execute(
                """
                INSERT INTO agent_executions
                (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
                VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
                """,
                (app_id, doc_id, json.dumps([{"result": "pass"}])),
            )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app)


def _edd_routing(*triggers):
    return {
        "policy_version": "edd_routing_policy_v1",
        "route": "edd",
        "triggers": list(triggers),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def _memo_with_edd_route(*triggers, mandatory_escalation=False):
    return {
        "ai_source": "deterministic",
        "supervisor": {
            "verdict": "CONSISTENT",
            "can_approve": not mandatory_escalation,
            "mandatory_escalation": mandatory_escalation,
            "mandatory_escalation_reasons": list(triggers) if mandatory_escalation else [],
        },
        "metadata": {
            "ai_source": "deterministic",
            "edd_routing": _edd_routing(*triggers),
            "supervisor": {
                "verdict": "CONSISTENT",
                "can_approve": not mandatory_escalation,
                "mandatory_escalation": mandatory_escalation,
                "mandatory_escalation_reasons": list(triggers) if mandatory_escalation else [],
            },
        },
    }


def _insert_edd_case(
    db,
    app,
    *,
    triggers,
    stage="edd_approved",
    decision="edd_approved",
    findings=True,
    audit=True,
    senior_approval=True,
):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    notes = json.dumps([
        {
            "ts": now,
            "source": "policy_routing",
            "policy_version": "edd_routing_policy_v1",
            "triggers": list(triggers),
            "note": "EDD case created by routing policy actuation",
        }
    ])
    db.execute(
        """
        INSERT INTO edd_cases (
            application_id, client_name, risk_level, risk_score, stage,
            assigned_officer, senior_reviewer, trigger_source, trigger_notes,
            edd_notes, decision, decision_reason, decided_by, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app["id"],
            app["company_name"],
            app.get("final_risk_level") or app.get("risk_level") or "HIGH",
            app.get("risk_score") or 80,
            stage,
            "co001",
            "sco001" if senior_approval else None,
            "screening_update",
            "Auto-routed to EDD by policy edd_routing_policy_v1 | triggers: " + ", ".join(triggers),
            notes,
            decision,
            "EDD approved after senior review" if senior_approval else None,
            "sco001" if senior_approval else None,
            now if senior_approval else None,
        ),
    )
    case_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    if findings:
        db.execute(
            """
            INSERT INTO edd_findings (
                edd_case_id, findings_summary, key_concerns,
                mitigating_evidence, recommended_outcome
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                case_id,
                "Structured EDD findings confirm residual risk is acceptable after senior review.",
                json.dumps(["EDD-triggering factor reviewed"]),
                json.dumps(["Enhanced evidence accepted and senior approval recorded"]),
                "approve",
            ),
        )
    if audit:
        db.execute(
            """
            INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sco001",
                "Senior Officer",
                "sco",
                "EDD Closure (dual-control)",
                app["ref"],
                json.dumps({"edd_case_id": case_id, "decision": decision, "closed_by": "sco001"}),
                "127.0.0.1",
            ),
        )
    db.commit()
    return case_id


def _insert_enhanced_requirement(
    db,
    app_id,
    *,
    status="generated",
    mandatory=True,
    blocking=True,
    waivable=True,
    waived_by=None,
    waived_at=None,
    waiver_reason=None,
    requirement_key=None,
):
    suffix = uuid.uuid4().hex[:8]
    key = requirement_key or f"approval_gate_requirement_{suffix}"
    db.execute(
        """
        INSERT INTO application_enhanced_requirements (
            application_id, trigger_key, trigger_label, trigger_category,
            requirement_key, requirement_label, requirement_description,
            audience, requirement_type, subject_scope, blocking_approval,
            waivable, waiver_roles, mandatory, status, generation_source,
            trigger_reason, trigger_context, active, waived_by, waived_at,
            waiver_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "high_or_very_high_risk",
            "HIGH / VERY_HIGH risk",
            "risk",
            key,
            "Enhanced approval gate evidence",
            "Evidence required for enhanced review approval gate tests.",
            "client",
            "document",
            "application",
            1 if blocking else 0,
            1 if waivable else 0,
            json.dumps(["admin", "sco"]),
            1 if mandatory else 0,
            status,
            "test",
            "Approval gate test trigger",
            "{}",
            1,
            waived_by,
            waived_at,
            waiver_reason,
        ),
    )
    db.commit()


def test_validate_approval_requires_explicit_validation_pass(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(
        db,
        validation_status="pass_with_fixes",
        approval_reason="",
    )
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "approval_reason" in message


def test_validate_approval_requires_explicit_supervisor_consistent(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(
        db,
        supervisor_status="CONSISTENT_WITH_WARNINGS",
        approval_reason="",
    )
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "approval_reason" in message


def test_validate_approval_allows_explicit_positive_states(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, validation_status="pass", supervisor_status="CONSISTENT", review_status="approved")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True
    assert message == ""


def test_validate_approval_blocks_stale_screening_truth(db):
    from security_hardening import ApprovalGateValidator

    now = datetime.now(timezone.utc)
    app = _insert_application_and_memo(
        db,
        prescreening_data={
            "screening_report": {
                "screening_mode": "live",
                "screened_at": (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%S"),
                "sanctions": {"api_status": "live", "matched": False},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live"},
            },
            "screening_valid_until": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_validity_days": 90,
        },
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "expired" in message.lower()
    assert "re-screen" in message.lower()


def test_mandatory_enhanced_requirements_block_until_accepted(db):
    from security_hardening import ApprovalGateValidator

    for status in ("generated", "requested", "uploaded", "under_review", "rejected"):
        app = _insert_application_and_memo(db)
        _insert_enhanced_requirement(db, app["id"], status=status, mandatory=True, blocking=False)

        can_approve, message = ApprovalGateValidator.validate_approval(app, db)

        assert can_approve is False
        assert "Enhanced Review requirements remain unresolved" in message
        assert status in message


def test_blocking_enhanced_requirements_block_until_accepted(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, app["id"], status="uploaded", mandatory=False, blocking=True)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "blocking unresolved=1" in message


def test_accepted_or_validly_waived_enhanced_requirements_allow_approval(db):
    from security_hardening import ApprovalGateValidator

    accepted_app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, accepted_app["id"], status="accepted")

    can_approve, message = ApprovalGateValidator.validate_approval(accepted_app, db)
    assert can_approve is True, message

    waived_app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(
        db,
        waived_app["id"],
        status="waived",
        waived_by="sco001",
        waived_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        waiver_reason="Senior waiver documented for approval gate test.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(waived_app, db)
    assert can_approve is True, message


def test_optional_nonblocking_enhanced_requirement_does_not_block(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, app["id"], status="generated", mandatory=False, blocking=False)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_cancelled_enhanced_requirement_does_not_block(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, risk_level="HIGH", status="edd_required")
    _insert_enhanced_requirement(db, app["id"], status="cancelled", mandatory=True, blocking=True)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_invalid_enhanced_requirement_waiver_blocks(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    db.execute(
        """
        INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("co001", "co001@example.com", "x", "Compliance Officer", "co", "active"),
    )
    db.execute("UPDATE users SET role='co' WHERE id='co001'")
    db.commit()
    _insert_enhanced_requirement(
        db,
        app["id"],
        status="waived",
        waived_by="co001",
        waived_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        waiver_reason="CO waiver should not satisfy approval control.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "invalid waivers=1" in message


def test_high_risk_missing_enhanced_requirements_blocks(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, risk_level="HIGH", status="edd_required")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Enhanced review requirements are missing or not generated" in message


def _insert_completed_edd_approval_app(db, *, triggers, sector="Financial Services"):
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=_memo_with_edd_route(*triggers),
    )
    db.execute("UPDATE applications SET sector = ? WHERE id = ?", (sector, app["id"]))
    db.commit()
    app = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone())
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")
    _insert_edd_case(db, app, triggers=triggers)
    return dict(db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone())


def test_final_approval_allows_completed_pep_edd_after_kyc_submitted(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_completed_edd_approval_app(
        db,
        triggers=[
            "declared_pep_present",
            "high_or_very_high_risk",
            "edd_flag:floor_rule_edd_routing",
        ],
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message
    audit = db.execute(
        "SELECT detail FROM audit_log WHERE action = 'Approval Gate EDD Completion Satisfied' "
        "AND target = ? ORDER BY id DESC LIMIT 1",
        (app["ref"],),
    ).fetchone()
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["edd_completion_satisfied"] is True
    assert detail["approved_application_status"] == "kyc_submitted"
    assert detail["missing_triggers"] == []


def test_final_approval_allows_completed_crypto_edd_after_kyc_submitted(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_completed_edd_approval_app(
        db,
        sector="Crypto / VASP",
        triggers=[
            "crypto_or_virtual_asset_sector",
            "high_risk_sector",
            "high_or_very_high_risk",
            "edd_flag:floor_rule_high_risk_sector",
        ],
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message
    audit = db.execute(
        "SELECT detail FROM audit_log WHERE action = 'Approval Gate EDD Completion Satisfied' "
        "AND target = ? ORDER BY id DESC LIMIT 1",
        (app["ref"],),
    ).fetchone()
    assert audit is not None
    assert json.loads(audit["detail"])["edd_completion_satisfied"] is True


def test_final_approval_allows_routine_onboarding_edd_route_when_enhanced_requirements_resolved(db):
    from security_hardening import ApprovalGateValidator

    triggers = ["declared_pep_present", "high_or_very_high_risk"]
    memo_data = _memo_with_edd_route(*triggers)
    memo_data["metadata"]["edd_routing"]["source"] = "memo_generation"
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=memo_data,
    )
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_final_approval_blocks_explicit_edd_route_when_edd_case_missing(db):
    from security_hardening import ApprovalGateValidator

    triggers = ["edd_flag:screening_escalated_to_edd"]
    memo_data = _memo_with_edd_route(*triggers)
    memo_data["metadata"]["edd_routing"]["source"] = "screening_update"
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=memo_data,
    )
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "EDD completion is not satisfied" in message
    assert "no_approved_edd_case" in message


def test_final_approval_blocks_edd_route_when_findings_incomplete(db):
    from security_hardening import ApprovalGateValidator

    triggers = ["declared_pep_present", "high_or_very_high_risk"]
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=_memo_with_edd_route(*triggers),
    )
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")
    _insert_edd_case(db, app, triggers=triggers, findings=False)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "findings_incomplete" in message


def test_final_approval_blocks_edd_route_when_edd_rejected(db):
    from security_hardening import ApprovalGateValidator

    triggers = ["declared_pep_present", "high_or_very_high_risk"]
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=_memo_with_edd_route(*triggers),
    )
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")
    _insert_edd_case(db, app, triggers=triggers, stage="edd_rejected", decision="edd_rejected")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "no_approved_edd_case" in message


def test_final_approval_blocks_edd_route_when_new_trigger_not_covered(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=_memo_with_edd_route("declared_pep_present", "material_screening_concern"),
    )
    _insert_enhanced_requirement(db, app["id"], status="accepted", requirement_key="edd_evidence_package")
    _insert_edd_case(db, app, triggers=["declared_pep_present", "high_or_very_high_risk"])

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "trigger_coverage_missing" in message
    assert "material_screening" in message


def test_final_approval_blocks_edd_route_when_enhanced_requirements_incomplete(db):
    from security_hardening import ApprovalGateValidator

    triggers = ["declared_pep_present", "high_or_very_high_risk"]
    app = _insert_application_and_memo(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="LOW",
        status="kyc_submitted",
        onboarding_lane="EDD",
        pre_approval_decision="PRE_APPROVE",
        memo_data=_memo_with_edd_route(*triggers),
    )
    _insert_enhanced_requirement(db, app["id"], status="uploaded", requirement_key="edd_evidence_package")
    _insert_edd_case(db, app, triggers=triggers)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "enhanced_requirements_unresolved" in message


def test_final_approval_blocks_stale_edd_memo_before_completion_gate(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_completed_edd_approval_app(
        db,
        triggers=["declared_pep_present", "high_or_very_high_risk"],
    )
    db.execute(
        """
        UPDATE compliance_memos
           SET is_stale = 1,
               stale_reason = 'Screening disposition changed after memo approval.',
               stale_trigger = 'screening_disposition_changed',
               stale_marked_at = CURRENT_TIMESTAMP
         WHERE application_id = ?
        """,
        (app["id"],),
    )
    db.commit()

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Compliance memo is stale" in message


def _completed_match_prescreening():
    now = datetime.now(timezone.utc)
    return {
        "company_name": "Approval Gate Test Ltd",
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "company_screening": {
                "found": True,
                "provider": "complyadvantage",
                "source": "complyadvantage",
                "api_status": "live",
                "matched": True,
                "results": [{"name": "Potential Watchlist Match", "is_sanctioned": True}],
                "provider_references": {"case_id": "ca-gate-001"},
                "sanctions": {
                    "api_status": "live",
                    "source": "complyadvantage",
                    "provider": "complyadvantage",
                    "matched": True,
                    "results": [{"name": "Potential Watchlist Match", "is_sanctioned": True}],
                },
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
        "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": 90,
    }


def _insert_screening_review(
    db,
    app_id,
    *,
    app_ref,
    disposition,
    disposition_code,
    rationale="Officer reviewed provider profile and evidence before disposition.",
    notes="Provider case CA-GATE-001 and registry evidence retained.",
):
    db.execute(
        """
        INSERT INTO screening_reviews (
            application_id, subject_type, subject_name, disposition, notes,
            disposition_code, rationale, requires_four_eyes, reviewer_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "entity",
            "Approval Gate Test Ltd",
            disposition,
            notes,
            disposition_code,
            rationale,
            0,
            "Compliance Officer",
        ),
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "co001",
            "Compliance Officer",
            "co",
            "Screening Review",
            app_ref,
            json.dumps({
                "subject_type": "entity",
                "subject_name": "Approval Gate Test Ltd",
                "disposition": disposition,
                "disposition_code": disposition_code,
                "evidence_reference": "Provider case CA-GATE-001 and registry evidence retained.",
            }, sort_keys=True),
            "127.0.0.1",
        ),
    )
    db.commit()


def _insert_four_eyes_screening_review(
    db,
    app_id,
    *,
    app_ref,
    first_reviewer_id="co001",
    first_reviewer_name="Compliance Officer",
    second_reviewer_id=None,
    second_reviewer_name=None,
):
    db.execute(
        """
        INSERT INTO screening_reviews (
            application_id, subject_type, subject_name, disposition, notes,
            disposition_code, rationale, sensitivity_flags, requires_four_eyes,
            reviewer_id, reviewer_name, second_reviewer_id, second_reviewer_name,
            second_disposition_code, second_rationale, second_reviewed_at
        )
        VALUES (?, ?, ?, 'cleared', ?, 'false_positive_cleared', ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "entity",
            "Approval Gate Test Ltd",
            "Provider case CA-GATE-4EYES and registry evidence retained.",
            "Officer confirmed the provider hit belongs to another legal entity after registry comparison.",
            json.dumps(["provider_hit"]),
            first_reviewer_id,
            first_reviewer_name,
            second_reviewer_id,
            second_reviewer_name,
            "false_positive_cleared" if second_reviewer_id else None,
            "Independent SCO review confirms false-positive clearance." if second_reviewer_id else None,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if second_reviewer_id else None,
        ),
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            first_reviewer_id,
            first_reviewer_name,
            "co",
            "Screening Review",
            app_ref,
            json.dumps({
                "subject_type": "entity",
                "subject_name": "Approval Gate Test Ltd",
                "disposition": "cleared",
                "disposition_code": "false_positive_cleared",
                "evidence_reference": "Provider case CA-GATE-4EYES and registry evidence retained.",
            }, sort_keys=True),
            "127.0.0.1",
        ),
    )
    db.commit()


def test_completed_match_without_disposition_blocks_approval(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "prohibited fail-closed" in message
    assert "sanctions_or_watchlist_hit" in message


def test_screening_second_review_pending_blocks_approval(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(db, app["id"], app_ref=app["ref"])

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "screening_second_review_pending" in message
    assert "SCO/admin review is required" in message


def test_screening_second_review_blocker_payload_is_officer_readable(db):
    from security_hardening import collect_approval_gate_blockers

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(db, app["id"], app_ref=app["ref"])

    blockers = collect_approval_gate_blockers(app, db)
    blocker = next(b for b in blockers if b.get("code") == "screening_second_review_pending")

    assert blocker["title"] == "Screening second review pending"
    assert blocker["required_reviewer_role"] == "SCO/admin"
    assert blocker["tab"] == "screening"
    assert blocker["anchorId"] == "detail-screening-review"
    assert blocker["action_key"] == "screening.resolve"


def test_submitted_to_compliance_route_reason_is_humanized_for_officers(db):
    from security_hardening import (
        can_decide_application,
        classify_approval_route,
        collect_approval_gate_blockers,
    )

    app = _insert_application_and_memo(db, status="submitted_to_compliance", risk_level="MEDIUM")
    route = classify_approval_route(app, db)

    blocker = next(
        item
        for item in collect_approval_gate_blockers(app, db)
        if item.get("id") == "risk_escalation_required"
    )
    assert "Submitted to Compliance" in blocker["description"]
    assert "officer_submitted_to_compliance" not in blocker["description"]
    assert blocker["metadata"]["escalation_reasons"] == ["officer_submitted_to_compliance"]

    allowed, code, reason, meta = can_decide_application(
        {"sub": "co001", "role": "co"},
        app,
        "approve",
        risk_level="MEDIUM",
        approval_route=route,
    )
    assert allowed is False
    assert code == 403
    assert "Submitted to Compliance" in reason
    assert "officer_submitted_to_compliance" not in reason
    assert meta["approval_route_escalation_reasons"] == ["officer_submitted_to_compliance"]


def test_same_user_first_and_second_screening_review_blocks_approval(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        first_reviewer_id="sco001",
        first_reviewer_name="Senior Compliance Officer",
        second_reviewer_id="sco001",
        second_reviewer_name="Senior Compliance Officer",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "screening_second_review_pending" in message


def test_co_second_reviewer_does_not_satisfy_screening_four_eyes_gate(db):
    from security_hardening import ApprovalGateValidator

    db.execute(
        """
        INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status)
        VALUES ('co_second_gate', 'co-second-gate@test.local', 'hash', 'Second CO', 'co', 'active')
        """
    )
    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        second_reviewer_id="co_second_gate",
        second_reviewer_name="Second CO",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "screening_second_review_pending" in message


def test_sco_second_reviewer_satisfies_screening_four_eyes_gate(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        second_reviewer_id="sco001",
        second_reviewer_name="Senior Compliance Officer",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_completed_match_false_positive_clearance_allows_screening_gate(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        disposition="cleared",
        disposition_code="false_positive_cleared",
        rationale="Officer confirmed the provider hit belongs to another legal entity after registry comparison.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_completed_match_true_match_disposition_remains_blocking(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        disposition="escalated",
        disposition_code="true_match",
        rationale="Officer confirmed the provider hit appears to match the entity and must remain blocked.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Screening truth gate failed" in message

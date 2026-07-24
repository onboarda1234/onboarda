import json
import os
import re


PORTAL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
BACKOFFICE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
SERVER_PATH = os.path.join(os.path.dirname(__file__), "..", "server.py")
SECURITY_HARDENING_PATH = os.path.join(os.path.dirname(__file__), "..", "security_hardening.py")
REPAIR_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "repair_missing_risk_scores.py")


def _portal_html():
    with open(PORTAL_PATH, encoding="utf-8") as f:
        return f.read()


def _backoffice_html():
    with open(BACKOFFICE_PATH, encoding="utf-8") as f:
        return f.read()


def _server_source():
    with open(SERVER_PATH, encoding="utf-8") as f:
        return f.read()


def _security_hardening_source():
    with open(SECURITY_HARDENING_PATH, encoding="utf-8") as f:
        return f.read()


def _repair_script_source():
    with open(REPAIR_SCRIPT_PATH, encoding="utf-8") as f:
        return f.read()


class TestPortalRiskSourceOfTruth:
    def test_resume_reapplies_authoritative_server_risk_after_saved_session_restore(self):
        html = _portal_html()

        assert "setCurrentRiskFromApp(app);" in html
        assert re.search(
            r"restoreDraftFromData\(savedSession\.form_data,\s*\{\s*allowRiskRestore:\s*false\s*\}\)",
            html,
        )
        saved_restore_pos = html.index("restoreDraftFromData(savedSession.form_data")
        reapply_pos = html.index("setCurrentRiskFromApp(app);", saved_restore_pos)
        assert reapply_pos > saved_restore_pos

    def test_restore_draft_sanitizes_risk_fields_by_default(self):
        html = _portal_html()

        assert "function sanitizeDraftRestoreData(data, options)" in html
        assert "delete sanitized.computedRiskLevel;" in html
        assert "delete sanitized.computedRiskScore;" in html
        assert "if (options && options.allowRiskRestore === true)" in html

    def test_save_resume_no_longer_persists_computed_risk_state(self):
        html = _portal_html()
        collect_start = html.index("function collectFormData()")
        collect_end = html.index("async function saveDraft()", collect_start)
        collect_src = html[collect_start:collect_end]

        assert "appRef: appRef" in collect_src
        assert "prescreening: {}" in collect_src
        assert "computedRiskLevel" not in collect_src
        assert "computedRiskScore" not in collect_src

    def test_missing_server_risk_renders_neutral_status_not_low_zero(self):
        html = _portal_html()

        assert "Application status unavailable. Please contact our team if this continues." in html
        assert "Risk unavailable — recalculation required" not in html
        assert "function hasAuthoritativeRisk(app)" in html
        assert "function renderRiskDisplay(appOrState)" in html
        assert "app.has_authoritative_risk === false" in html
        assert "app.risk_level || 'LOW'" not in html
        assert "Number(app.risk_score || 0)" not in html
        assert "(computedRiskScore || 0).toFixed(1)" not in html


class TestBackofficeRiskSourceOfTruth:
    def test_backoffice_uses_authoritative_risk_display_helpers(self):
        html = _backoffice_html()

        assert "function buildRiskDisplayState(source)" in html
        assert "function riskBadgeForRecord(record)" in html
        assert "function formatRiskScoreForRecord(record)" in html
        assert "source.has_authoritative_risk === false" in html
        assert "Risk unavailable — recalculation required" in html
        assert "risk_rating || 'MEDIUM'" not in html
        assert "risk_score || 'N/A'" not in html
        assert "riskBadge(app.risk)" not in html
        assert "riskBadge(c.risk_level)" not in html

    def test_backoffice_edd_detail_distinguishes_low_risk_from_edd_required(self):
        html = _backoffice_html()

        assert "function eddTriggerText(caseRow)" in html
        assert "caseRow.edd_trigger_flags || caseRow.eddTriggerFlags" in html
        assert "return 'Trigger reason unavailable';" in html
        assert '<span class="label">Risk Level</span>' in html
        assert '<span class="label">Risk Score</span>' in html
        assert '<span class="label">Case Type</span><span class="value" style="font-weight:700;">Formal Investigation Case</span>' in html
        assert "Formal narrative investigation. Routine onboarding Enhanced Review Requirements remain in KYC Documents." in html
        assert '<span class="label">Trigger</span>' in html

    def test_backoffice_risk_card_uses_only_authoritative_backend_evidence(self):
        html = _backoffice_html()
        start = html.index("function riskExecutiveStoredOutcomeCodes(risk)")
        end = html.index("\nfunction setMemoDownloadState", start)
        risk_card = html[start:end]

        assert "Overall risk" in risk_card
        assert "Approval route" in risk_card
        assert "Evidence at a glance" in risk_card
        assert "risk-executive-dashboard" in risk_card
        assert "Configuration:" in risk_card
        assert "factor_evidence" in risk_card
        assert "weighted_factor_contribution" in risk_card
        assert "dimension_computation_evidence" in risk_card
        assert "composite_contribution" in risk_card
        assert "computation_evidence" in risk_card
        assert "app.sector" not in risk_card
        assert "app.country" not in risk_card
        assert "app.entityType" not in risk_card
        assert "score * weight * 0.25" not in risk_card
        assert ".reduce(" not in risk_card
        assert "Latest recomputed score" not in risk_card
        assert "Weighted average" not in risk_card
        assert "Formula:" not in risk_card


class TestBackendRiskIntegrityMetadata:
    def test_high_risk_application_stays_authoritative(self):
        from server import _decorate_application_risk_integrity

        app = {"status": "pricing_review", "risk_level": "HIGH", "risk_score": 72}
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is True
        assert decorated["risk_integrity_warnings"] == []

    def test_backoffice_snapshot_prefers_authoritative_final_risk(self):
        from server import _application_risk_snapshot

        level, score = _application_risk_snapshot({
            "status": "pricing_review",
            "risk_level": "LOW",
            "final_risk_level": "MEDIUM",
            "risk_score": 40,
        })

        assert level == "MEDIUM"
        assert score == 40

    def test_memo_risk_context_prefers_authoritative_final_risk(self):
        from memo_handler import _risk_display_context

        display = _risk_display_context({
            "risk_level": "LOW",
            "final_risk_level": "MEDIUM",
            "risk_score": 40,
        })

        assert display["available"] is True
        assert display["level"] == "MEDIUM"
        assert "MEDIUM" in display["summary"]

    def test_very_high_risk_application_stays_authoritative(self):
        from server import _decorate_application_risk_integrity

        app = {"status": "pre_approval_review", "risk_level": "VERY_HIGH", "risk_score": 88}
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is True
        assert decorated["risk_integrity_warnings"] == []

    def test_edd_required_with_valid_risk_is_not_marked_unavailable(self):
        from server import _decorate_application_risk_integrity

        app = {
            "status": "edd_required",
            "risk_level": "HIGH",
            "risk_score": 67,
            "decision_notes": json.dumps({
                "decision": "escalate_edd",
                "edd_trigger_flags": ["officer_escalate_edd"],
            }),
        }
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is True
        assert "officer_escalate_edd" in decorated["edd_trigger_flags"]
        assert decorated["risk_integrity_warnings"] == []

    def test_missing_risk_on_non_draft_is_integrity_warning(self):
        from server import _RISK_UNAVAILABLE_WARNING, _decorate_application_risk_integrity

        app = {"status": "submitted", "risk_level": None, "risk_score": None}
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is False
        assert _RISK_UNAVAILABLE_WARNING in decorated["risk_integrity_warnings"]

    def test_edd_required_low_zero_gets_trigger_metadata_not_silent_low_zero(self):
        from server import _EDD_ZERO_SCORE_WARNING, _decorate_application_risk_integrity

        app = {
            "status": "edd_required",
            "risk_level": "LOW",
            "risk_score": 0,
            "decision_notes": json.dumps({
                "decision": "escalate_edd",
                "edd_trigger_flags": ["officer_escalate_edd"],
            }),
        }
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is False
        assert "officer_escalate_edd" in decorated["edd_trigger_flags"]
        assert _EDD_ZERO_SCORE_WARNING in decorated["risk_integrity_warnings"]

    def test_legitimate_low_zero_score_remains_authoritative_outside_edd(self):
        from server import _application_risk_integrity_error, _decorate_application_risk_integrity

        app = {"status": "submitted", "risk_level": "LOW", "risk_score": 0}
        decorated = _decorate_application_risk_integrity(app)

        assert decorated["has_authoritative_risk"] is True
        assert decorated["risk_integrity_warnings"] == []
        assert _application_risk_integrity_error(app, "approve application") is None

    def test_non_low_zero_score_fails_integrity_gate(self):
        from server import _RISK_UNAVAILABLE_WARNING, _application_risk_integrity_error

        app = {"status": "submitted", "risk_level": "HIGH", "risk_score": 0}
        error = _application_risk_integrity_error(app, "approve application")

        assert error
        assert _RISK_UNAVAILABLE_WARNING in error

    def test_backend_decision_paths_call_risk_integrity_gate(self):
        src = _server_source()
        approval_src = _security_hardening_source()

        assert '_application_risk_integrity_error(app, "record pre-approval decision")' in src
        assert '_application_risk_integrity_error(app, "generate compliance memo")' in src
        assert '_application_risk_integrity_error(app_row, "approve compliance memo")' in src
        assert '_application_risk_integrity_error(app, "submit final decision")' in src
        assert '_application_risk_integrity_error(app_for_risk, "advance EDD case")' in src
        assert '_application_risk_integrity_error(app, "accept pricing")' in src
        assert '_application_risk_integrity_error(risk_app_for_submission, "submit KYC documents")' in src
        assert '_approval_risk_integrity_error(app, "approve application")' in approval_src
        assert 'risk_level = app["risk_level"] or "MEDIUM"' not in src
        assert 'risk_score = app["risk_score"] or 0' not in src

    def test_backend_pdf_export_checks_memo_risk_staleness_before_rendering(self):
        src = _server_source()
        pdf_start = src.index("class MemoPDFDownloadHandler")
        pdf_end = src.index("class MemoSupervisorHandler", pdf_start)
        pdf_src = src[pdf_start:pdf_end]

        assert "_ensure_memo_fresh_or_mark_stale(" in pdf_src
        assert 'context="memo_pdf_export"' in pdf_src
        assert "PDF export blocked: Compliance memo is stale" in pdf_src
        assert "authoritative_case_risk" in pdf_src

    def test_reports_export_effective_final_risk_level(self):
        src = _server_source()

        assert "COALESCE(a.final_risk_level, a.risk_level) AS risk_level" in src
        assert '("risk_level", "COALESCE(a.final_risk_level, a.risk_level) = ?")' in src

    def test_shared_approval_gate_rejects_missing_authoritative_risk(self):
        from security_hardening import _approval_risk_integrity_error

        app = {
            "status": "compliance_review",
            "risk_level": None,
            "risk_score": None,
        }
        error = _approval_risk_integrity_error(app, "approve application")

        assert error
        assert "Risk unavailable" in error
        assert "Cannot approve application" in error

    def test_shared_approval_gate_allows_legitimate_low_zero_outside_edd(self):
        from security_hardening import _approval_risk_integrity_error

        app = {
            "status": "compliance_review",
            "risk_level": "LOW",
            "risk_score": 0,
        }

        assert _approval_risk_integrity_error(app, "approve application") is None


class TestRepairScriptRiskZeroContract:
    def test_repair_script_documents_zero_score_contract_and_outputs_dry_run_detail(self):
        src = _repair_script_source()

        assert "risk_score=0 can be a legitimate deterministic LOW score" in src
        assert "historical bug also" in src
        assert "statuses_affected" in src
        assert "safe_to_apply" in src
        assert "no_op_validation_cases" in src
        assert "proposed_changes" in src
        assert "unrecomputable" in src

    def test_repair_script_supports_safe_targeted_apply_options(self):
        src = _repair_script_source()

        assert "--dry-run" in src
        assert "--exclude-ambiguous" in src
        assert "--application-ref" in src
        assert "--only-ref" in src
        assert "excluded_ambiguous" in src
        assert "--apply and --dry-run cannot be used together" in src
        assert "--apply requires --exclude-ambiguous or --application-ref/--only-ref" in src

    def test_repair_script_classifies_ambiguous_and_no_op_recompute(self):
        from scripts.repair_missing_risk_scores import (
            _is_ambiguous_edd_low_recompute,
            _normalize_application_refs,
            _same_risk_value,
        )

        assert _normalize_application_refs(["ARF-1, ARF-2", "ARF-1"]) == ["ARF-1", "ARF-2"]
        assert _same_risk_value({"risk_level": "LOW", "risk_score": 0}, {"level": "LOW", "score": 0.0})
        assert not _same_risk_value({"risk_level": None, "risk_score": None}, {"level": "LOW", "score": 26.0})
        assert _is_ambiguous_edd_low_recompute(
            {"status": "edd_required"},
            {"level": "LOW", "score": 26.0},
        )
        assert not _is_ambiguous_edd_low_recompute(
            {"status": "edd_required"},
            {"level": "HIGH", "score": 61.0},
        )
        assert not _is_ambiguous_edd_low_recompute(
            {"status": "pricing_review"},
            {"level": "LOW", "score": 0.0},
        )

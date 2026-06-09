from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
SERVER_PY = ROOT / "arie-backend" / "server.py"


def _html() -> str:
    return BACKOFFICE_HTML.read_text()


def _view_region(html: str, view_id: str, next_view_id: str) -> str:
    start = html.index(f'id="{view_id}"')
    start = html.rindex("<div", 0, start)
    end_marker = html.index(f'id="{next_view_id}"', start)
    end = html.rindex("<div", 0, end_marker)
    return html[start:end]


def _function_region(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_monitoring_alerts_cards_are_sprint1_source_of_truth_only():
    view = _view_region(_html(), "view-monitoring", "view-lifecycle")

    for label in [
        "Open Alerts",
        "High / Critical",
        "Document Expiry",
        "Risk Drift",
        "Escalated",
    ]:
        assert label in view

    for removed in [
        "Active Monitoring",
        "Open Monitoring Alerts",
        "Reviews Due",
        "Active Lifecycle Work",
        "(raw)",
    ]:
        assert removed not in view


def test_monitoring_alerts_filters_use_canonical_backend_values():
    view = _view_region(_html(), "view-monitoring", "view-lifecycle")

    for value in [
        'value="adverse_media"',
        'value="pep_change"',
        'value="sanctions_change"',
        'value="document_expiry"',
        'value="missing_document_refresh"',
        'value="risk_drift"',
        'value="regulatory_impact"',
        'value="high"',
        'value="critical"',
        'value="in_review"',
        'value="document_requested"',
        'value="client_uploaded"',
        'value="routed_to_edd"',
        'value="waived"',
    ]:
        assert value in view

    for display_value in [
        'value="Adverse Media"',
        'value="High"',
        'value="Open"',
    ]:
        assert display_value not in view


def test_monitoring_alerts_canonical_mapping_covers_legacy_api_values():
    html = _html()
    mapping = _function_region(html, "normalizeAlertType", "monitoringAlertTypeLabel")
    severity = _function_region(html, "normalizeAlertSeverity", "monitoringAlertSeverityLabel")
    status = _function_region(html, "normalizeAlertStatus", "normalizeAlertStatusLabel")

    assert "'media'" in mapping
    assert "return 'adverse_media'" in mapping
    assert "'pep'" in mapping
    assert "document_expiry_missing" in mapping
    assert "return 'missing_document_refresh'" in mapping

    assert "monitoringCanonicalToken(severity || 'medium')" in severity
    assert "if (s === 'critical' || s === 'high' || s === 'medium' || s === 'low')" in severity

    assert "routed_to_review" in status
    assert "document_requested" in status
    assert "client_uploaded" in status
    assert "routed_to_edd" in status


def test_monitoring_alerts_render_uses_keys_and_has_truthful_empty_states():
    html = _html()
    render = _function_region(html, "renderMonitoringAlerts", "renderPeriodicReviews")

    assert "MONITORING_DATA_STATE === 'loading'" in render
    assert "Loading monitoring alerts..." in render
    assert "MONITORING_ALERTS_LOAD_ERROR" in render
    assert "Monitoring alerts could not be loaded" in render
    assert "No monitoring alerts found." in render
    assert "No monitoring alerts match the current filters." in render
    assert "alert.severityKey !== severityFilter" in render
    assert "alert.typeKey !== typeFilter" in render
    assert "alert.statusKey !== statusFilter" in render
    assert "openMonitoringAlertDetail(alert.id)" in render


def test_monitoring_alerts_client_display_does_not_primary_render_uuid():
    html = _html()
    client_display = _function_region(html, "isMonitoringUuidLike", "monitoringAlertClientTitle")
    render = _function_region(html, "renderMonitoringAlerts", "renderPeriodicReviews")

    assert "Unmapped client" in client_display
    assert "findApplicationForMonitoringAlert" in client_display
    assert "!isMonitoringUuidLike(value)" in client_display
    assert "[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" in client_display
    assert "[1-5][0-9a-f]{3}" not in client_display
    assert "alert.client = monitoringAlertClientDisplay(alert.raw || alert)" in render
    assert "title=\"" in render


def test_dashboard_monitoring_metric_is_not_mislabelled_as_alerts():
    html = _html()
    server = SERVER_PY.read_text()

    assert "High-risk applications" in html
    assert "Active Monitoring Alerts" not in _view_region(html, "view-dashboard", "view-kpis")
    assert "high_risk_applications" in html
    assert '"high_risk_applications": 0' in server
    assert 'stats["high_risk_applications"] = high_risk' in server


def test_monitoring_alerts_open_uses_full_page_detail_view_not_modal():
    html = _html()
    detail_view = _view_region(html, "view-monitoring-alert-detail", "view-lifecycle")
    render = _function_region(html, "renderMonitoringAlerts", "renderPeriodicReviews")
    opener = _function_region(html, "openMonitoringAlertDetail", "refreshMonitoringAlertDetail")

    assert 'id="view-monitoring-alert-detail"' in detail_view
    assert "Back to Monitoring Alerts" in detail_view
    assert "openMonitoringAlertDetail(alert.id)" in render
    assert "showAlertDetail(alert)" not in render
    assert "showView('monitoring-alert-detail')" in opener
    assert "#monitoring-alerts/" in opener


def test_monitoring_alert_detail_has_required_sprint2_sections():
    html = _html()
    renderer = _function_region(html, "renderMonitoringAlertDetailView", "openMonitoringAlertDetail")

    for label in [
        "Alert Summary",
        "Issue / Evidence",
        "Compliance Impact",
        "Recommended Next Step",
        "Assignment",
        "Officer Decision",
        "Downstream Links",
        "Audit History",
        "Technical Details",
    ]:
        assert label in renderer or label in html


def test_monitoring_alert_detail_keeps_raw_payload_collapsed():
    html = _html()
    technical = _function_region(html, "renderMonitoringTechnicalDetails", "renderMonitoringAlertDetailView")
    evidence = _function_region(html, "monitoringAlertEvidenceHtml", "monitoringComplianceImpact")

    assert "<details" in technical
    assert "<summary" in technical
    assert "Raw alert payload" in technical
    assert "JSON.stringify(raw, null, 2)" in technical
    assert "Detailed provider match evidence is not available in this alert payload." in evidence
    assert "Raw alert payload" not in evidence


def test_monitoring_alert_detail_renders_structured_provider_evidence_without_fake_links():
    html = _html()
    evidence = _function_region(html, "monitoringAlertProviderEvidenceRows", "monitoringAlertEvidenceHtml")
    assert "source_title" in evidence
    assert "source_name" in evidence
    assert "publication_date" in evidence
    assert "match_confidence" in evidence
    assert "Evidence status" in evidence
    assert "Evidence fetched" in evidence
    assert "Provider case ID" in evidence
    assert "Provider risk / match ID" in evidence
    assert "Source article link not available from ComplyAdvantage payload." in evidence
    assert "target=\"_blank\"" in evidence


def test_monitoring_alert_audit_history_renders_readable_metadata_not_json():
    html = _html()
    readable = _function_region(html, "monitoringAuditReadableDetail", "renderMonitoringAuditHistory")
    history = _function_region(html, "renderMonitoringAuditHistory", "renderMonitoringTechnicalDetails")
    technical = _function_region(html, "renderMonitoringTechnicalDetails", "renderMonitoringAlertDetailView")

    assert "JSON.parse(detail)" in readable
    assert "Decision saved:" in readable
    assert "Assigned from " in readable
    assert "Status updated:" in readable
    assert "Detailed provider evidence is not available for this alert." in readable
    assert "JSON.stringify" not in history
    assert "Raw audit metadata" in technical
    assert "JSON.stringify(auditHistory || [], null, 2)" in technical


def test_monitoring_alert_decision_and_assignment_controls_are_simplified():
    html = _html()
    decision = _function_region(html, "renderMonitoringDecisionSection", "renderMonitoringAssignmentSection")
    assignment = _function_region(html, "renderMonitoringAssignmentSection", "renderMonitoringDownstreamLinks")

    assert "Start Review" in decision
    assert "Choose Outcome" in decision
    assert "Save Decision" in decision
    assert "Triage" not in decision
    assert "Mark as Reviewed" not in decision

    assert "Assign to me" in assignment
    assert "Save assignment" in assignment
    assert "Assign-to-officer is restricted to Administrator and Senior CO" in assignment

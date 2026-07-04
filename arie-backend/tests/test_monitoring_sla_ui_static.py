from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text()


def _function_region(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_monitoring_sla_helpers_use_backend_contract_without_recalculation():
    html = _html()
    helpers = _function_region(html, "monitoringSlaToneBadgeClass", "monitoringSeverityBadgeClass")

    assert "sla.sla_label" in helpers
    assert "sla.sla_tone" in helpers
    assert "sla.sla_state" in helpers
    assert "sla.closed_within_sla === false" in helpers
    assert "Closed late" in helpers
    assert "state === 'unknown'" in helpers
    assert "return null" in helpers
    assert "data-monitoring-sla-chip" in helpers

    # The backend owns SLA math and overdue wording. The frontend only displays
    # sla_label and a safe closed-late override.
    chip = _function_region(html, "renderMonitoringSlaChip", "monitoringSlaDateLabel")
    assert "display.label" in chip
    assert "daysOverdue" not in chip
    assert "days_overdue" not in chip
    assert "Overdue 3d" not in helpers
    assert "Closed late" in helpers
    assert "Closed late" not in chip


def test_monitoring_sla_tone_mapping_is_limited_to_supported_tones():
    html = _html()
    helpers = _function_region(html, "monitoringSlaToneBadgeClass", "monitoringSeverityBadgeClass")

    assert "green: 'approved'" in helpers
    assert "amber: 'high'" in helpers
    assert "red: 'very-high'" in helpers
    assert "grey: 'draft'" in helpers
    assert "['green', 'amber', 'red', 'grey']" in helpers
    assert "if (state === 'closed') tone = 'grey'" in helpers
    assert "blue:" not in helpers
    assert "purple:" not in helpers


def test_monitoring_list_renders_compact_sla_chip_from_alert_sla():
    html = _html()
    render = _function_region(html, "renderMonitoringAlerts", "renderMonitoringAlertsPagination")

    assert "var slaChip = renderMonitoringSlaChip(alert.sla)" in render
    assert "var slaCell = slaChip" in render
    assert "slaChip + '<div" in render
    assert "escapeHtml(ageDue)" in render
    assert "renderMonitoringSlaChip(alert.sla)" in html
    assert "alert.sla" in render


def test_monitoring_detail_renders_sla_summary_from_alert_sla():
    html = _html()
    detail = _function_region(html, "renderMonitoringAlertDetailView", "openMonitoringAlertDetail")
    document_card = _function_region(html, "renderMonitoringDocumentExpiryCard", "monitoringAlertEvidenceHtml")

    for region in (detail, document_card):
        assert "['SLA', renderMonitoringSlaDetailValue(alert.sla)]" in region
        assert "['SLA due date', monitoringTextValue(monitoringSlaDateLabel(alert.sla), 'Not set')]" in region
        assert "['Alert age', monitoringTextValue(monitoringSlaAgeLabel(alert.sla), 'Not available')]" in region

    assert "sla: raw.sla || null" in _function_region(html, "normalizeMonitoringAlert", "reviewStatusFromRaw")


def test_monitoring_sla_ui_is_display_only_and_adds_no_workflow_calls():
    html = _html()
    helpers = _function_region(html, "monitoringSlaToneBadgeClass", "monitoringSeverityBadgeClass")
    render = _function_region(html, "renderMonitoringAlerts", "renderMonitoringAlertsPagination")
    detail = _function_region(html, "renderMonitoringAlertDetailView", "openMonitoringAlertDetail")

    display_regions = "\n".join([helpers, render, detail])
    for forbidden in [
        "boApiCall('POST'",
        "boApiCall('PATCH'",
        "boApiCall('PUT'",
        "boApiCall('DELETE'",
        "fetch(",
        "monitoring_alerts.status",
        "alert.status =",
        "sla_due_at =",
        "sla_days =",
    ]:
        assert forbidden not in display_regions

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "arie-backoffice.html"
SERVER = ROOT / "arie-backend" / "server.py"


def _html():
    return HTML.read_text(encoding="utf-8")


def _server():
    return SERVER.read_text(encoding="utf-8")


def test_overdue_escalation_is_detail_only_and_sla_gated():
    html = _html()
    helper = html[
        html.index("function monitoringAlertCanEscalateOverdue"):
        html.index("function renderMonitoringOverdueEscalationAction")
    ]
    assert "sla.sla_state !== 'overdue'" in helper
    assert "['admin','sco','co']" in helper
    assert "status === 'escalated'" in helper
    assert "rawStatus === 'escalated'" in helper
    assert "monitoringAlertIsTerminal(alert)" in helper
    assert "alert.raw && alert.raw.resolved_at" in helper
    assert "analyst" not in helper
    assert "renderMonitoringAlerts()" in html  # existing list renderer remains present
    list_region = html[html.index("function renderMonitoringAlerts()"):html.index("function renderMonitoringAlertsPagination")]
    assert "escalate-overdue" not in list_region
    assert "Escalate overdue" not in list_region


def test_overdue_escalation_requires_reason_and_uses_dedicated_endpoint():
    html = _html()
    assert 'data-monitoring-overdue-escalation-action="true"' in html
    assert "monitoring-overdue-escalation-reason" in html
    assert "A reason is required for overdue escalation." in html
    assert "'POST', '/monitoring/alerts/' + encodeURIComponent(alert.id) + '/escalate-overdue'" in html
    assert "await refreshMonitoringAlertDetail()" in html
    assert "renderMonitoringSlaChip(sla)" in html
    assert "renderMonitoringOverdueEscalationSummary(alert)" in html


def test_overdue_audit_label_and_metadata_summary_render():
    html = _html()
    assert "'monitoring.alert.overdue_escalated': 'Overdue alert escalated'" in html
    assert "alert.raw.overdue_escalations" in html
    assert "latest.days_overdue" in html
    assert "latest.sla_due_at" in html
    assert "latest.reason" in html


def test_backend_wrapper_reuses_escalate_to_sco_and_has_no_literal_status_shortcut():
    server = _server()
    handler = server[
        server.index("class MonitoringAlertOverdueEscalationHandler"):
        server.index("def _execute_monitoring_clearing")
    ]
    assert 'outcome="escalate_to_sco"' in handler
    assert "monitoring.alert.overdue_escalated" in handler
    assert 'sla.get("sla_state") != "overdue"' in handler
    assert 'prior_status_key == "escalated"' in handler
    assert 'roles=["admin", "sco", "co"]' in handler
    assert "monitoring_alert_escalations" in server
    assert "UPDATE monitoring_alerts SET status = 'escalated'" not in handler
    assert 'UPDATE monitoring_alerts SET status = "escalated"' not in handler

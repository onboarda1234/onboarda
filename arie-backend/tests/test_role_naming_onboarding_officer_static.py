import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
SERVER_PY = ROOT / "arie-backend" / "server.py"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


def _function_region(source: str, start_name: str, next_name: str) -> str:
    start = source.index(f"function {start_name}")
    end = source.index(f"function {next_name}", start)
    return source[start:end]


def _server_literal(name: str):
    tree = ast.parse(_server_source())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def test_role_display_mapping_keeps_internal_keys_and_sco_label():
    html = _html()
    server_labels = _server_literal("ROLE_LABELS")

    assert "co:'Onboarding Officer'" in html
    assert "sco:'Senior Compliance Officer'" in html
    assert server_labels["co"] == "Onboarding Officer"
    assert server_labels["sco"] == "Senior Compliance Officer"
    assert '"co": "Onboarding Officer"' in _server_source()
    assert '"sco": "Senior Compliance Officer"' in _server_source()


def test_user_management_and_assignment_labels_use_onboarding_officer():
    html = _html()

    assert '<option value="co">Onboarding Officer</option>' in html
    assert '<option value="sco">Senior Compliance Officer</option>' in html
    assert '<option value="co">Compliance Officer</option>' not in html

    render_users = _function_region(html, "renderUsers", "mapAuditEntry")
    assert "formatRoleLabel(u.role)" in render_users

    populate_dropdowns = _function_region(html, "populateOfficerDropdowns", "showView")
    assert "formatRoleLabel(u.role)" in populate_dropdowns

    monitoring_assignment = _function_region(html, "renderMonitoringAssignmentSection", "monitoringAlertApplicationTarget")
    assert "formatRoleLabel(u.role)" in monitoring_assignment


def test_audit_role_display_resolves_co_without_rewriting_records():
    html = _html()

    role_formatter = _function_region(html, "formatRoleLabel", "switchUser")
    assert "ROLE_LABELS[normalized]" in role_formatter
    assert "normalized === 'compliance officer'" in role_formatter

    map_audit_entry = _function_region(html, "mapAuditEntry", "auditFilterValue")
    assert "role: formatRoleLabel(e.user_role || '')" in map_audit_entry

    audit_card = _function_region(html, "renderAuditEventCard", "renderAuditFilterBar")
    assert "formatRoleLabel(entry.user_role)" in audit_card

    monitoring_audit = _function_region(html, "renderMonitoringAuditHistory", "renderMonitoringTechnicalDetails")
    assert "formatRoleLabel(item.user_role)" in monitoring_audit


def test_role_permission_matrix_keys_and_gates_are_unchanged():
    matrix = {entry["id"]: entry["roles"] for entry in _server_literal("ROLE_PERMISSION_MATRIX")}
    source = _server_source()

    assert matrix["approve_low_medium"] == ["admin", "sco", "co"]
    assert matrix["approve_high_very_high"] == ["admin", "sco"]
    assert matrix["reject_applications"] == ["admin", "sco", "co"]
    assert matrix["assign_reassign_cases"] == ["admin", "sco"]
    assert matrix["view_enhanced_requirements"] == ["admin", "sco", "co"]

    # PR-APPROVAL-AUTHORITY-MATRIX-1: the CO-cannot-approve-HIGH/VERY_HIGH rule
    # moved out of an inline check into the centralized can_decide_application
    # authority gate (security_hardening.py); the decision handler routes
    # approve/reject through that gate.
    assert "can_decide_application(" in source
    sh_source = (ROOT / "arie-backend" / "security_hardening.py").read_text(encoding="utf-8")
    assert 'role == "co" and is_high' in sh_source
    assert "Onboarding Officers cannot approve HIGH" in sh_source
    assert 'user.get("role") not in ("admin", "sco")' in source
    assert 'user.get("role") not in ("admin", "sco", "co")' in source


def test_no_backoffice_co_role_label_regression():
    html = _html()

    assert "Login as: Onboarding Officer" in html
    assert "Onboarding Officer Override Rate" in html
    assert "Clear as False Positive requires Onboarding Officer, SCO, or Admin role" in html
    assert "Login as: Compliance Officer" not in html
    assert re.search(r"<th>Onboarding Officer</th>", html)

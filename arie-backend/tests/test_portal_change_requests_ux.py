import os
import re
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = ROOT / "arie-portal.html"


def _portal_html() -> str:
    return PORTAL_HTML.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    start = html.index(f"function {name}")
    next_function = html.find("\nfunction ", start + 1)
    if next_function == -1:
        next_function = html.find("\nasync function ", start + 1)
    return html[start:next_function]


def test_change_request_form_renders_above_history():
    html = _portal_html()
    form_pos = html.index('id="portal-change-form"')
    history_pos = html.index('id="portal-change-history-card"')
    list_pos = html.index('id="portal-changes-list"')

    assert form_pos < history_pos < list_pos
    form_opening = re.search(r'<div id="portal-change-form"[^>]*>', html).group(0)
    assert "display:none" not in form_opening


def test_request_change_button_opens_focuses_top_form():
    html = _portal_html()
    assert '+ Request a Change' in html
    assert 'onclick="showPortalChangeRequestForm()"' in html

    body = _function_body(html, "showPortalChangeRequestForm")
    assert "form.style.display = ''" in body
    assert "loadPortalChangeApps()" in body
    assert "scrollIntoView" in body
    assert ".focus" in body


def test_history_empty_state_points_to_form_above():
    html = _portal_html()
    assert "No change requests yet. Use the form above" in html
    assert "No change requests yet. Use the button above" not in html


def test_client_friendly_change_type_options_are_present():
    html = _portal_html()
    expected_options = {
        "legal_name_change": "Company legal name",
        "registration_number_change": "Registration number / BRN",
        "address_change": "Registered address",
        "director_change": "Director change",
        "shareholder_change": "Shareholder change",
        "ubo_change": "UBO / beneficial owner change",
        "business_activity_change": "Business activity",
        "regulated_activity_change": "Regulated activity / licence",
        "source_of_funds_change": "Source of funds",
        "source_of_wealth_change": "Source of wealth",
        "contact_detail_update": "Contact details",
        "other": "Other",
    }
    for value, label in expected_options.items():
        assert f'<option value="{value}">{label}</option>' in html


def test_client_friendly_status_labels_are_used():
    html = _portal_html()
    assert "function portalChangeRequestStatusLabel" in html
    for label in (
        "Draft",
        "Submitted",
        "Under Review",
        "More Information Required",
        "Approved",
        "Implemented",
        "Rejected",
        "Cancelled",
    ):
        assert label in html
    assert "portalChangeRequestStatusLabel(r.status)" in html
    assert "(r.status||'').replace(/_/g,' ')" not in html


def test_portal_form_uses_owned_application_endpoint_and_no_admin_controls():
    html = _portal_html()
    body = _function_body(html, "loadPortalChangeApps")
    assert "apiCall('GET', '/portal/applications')" in body
    assert "'/applications'" not in body

    change_view = html[
        html.index('id="view-portal-changes"'):
        html.index('<!-- ══════════════════════════════════════\n       VIEW: MY APPLICATIONS', html.index('id="view-portal-changes"'))
    ]
    assert "/change-management/requests" not in change_view
    assert "Approve" not in change_view
    assert "Implement" not in change_view
    assert "Supporting documents may be requested by Compliance after submission." in change_view


def test_portal_visibility_helper_hides_internal_change_requests():
    from server import portal_change_request_is_client_visible

    assert portal_change_request_is_client_visible({"source": "portal_client", "source_channel": "portal"})
    assert portal_change_request_is_client_visible({"source": "backoffice_manual", "source_channel": "portal"})
    assert not portal_change_request_is_client_visible({"source": "backoffice_manual", "source_channel": "backoffice"})
    assert not portal_change_request_is_client_visible({"source": "external_alert_conversion", "source_channel": "monitoring"})
    assert not portal_change_request_is_client_visible({"source": "ongoing_monitoring", "source_channel": "system"})
    assert not portal_change_request_is_client_visible({"source": "system_admin", "source_channel": "backoffice"})

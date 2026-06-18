import re
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = REPO_ROOT / "arie-portal.html"
BACKOFFICE_PATH = REPO_ROOT / "arie-backoffice.html"


CLIENT_VIEW_IDS = (
    "view-pending",
    "view-risk-scoring",
    "view-pre-approval-hold",
    "view-compliance-hold",
    "view-pricing",
    "view-onboarding",
    "view-submission-review",
    "view-docs-review",
    "view-approved",
    "view-client-notifications",
)

FORBIDDEN_CLIENT_PATTERNS = (
    r"\bLOW RISK\b",
    r"\bMEDIUM RISK\b",
    r"\bHIGH RISK\b",
    r"\bVERY HIGH RISK\b",
    r"Medium-Low",
    r"Risk Assessment",
    r"Risk Rating",
    r"Next Review",
    r"All Checks Cleared",
    r"Sanctions Screening",
    r"Document Authenticity",
    r"Document Validity",
    r"Composite Risk Score",
    r"AI Transparency",
    r"AI-powered risk scoring",
    r"5-dimension risk scoring",
    r"AI risk scoring engine",
    r"Risk unavailable\s*[\u2014-]\s*recalculation required",
    r"Enhanced Due Diligence",
    r"\bEDD\b",
    r"elevated risk profile",
    r"high risk",
)


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._hidden_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        style = attrs_dict.get("style", "")
        parent_hidden = any(self._hidden_stack)
        if "display:none" in style.replace(" ", "").lower():
            self._hidden_stack.append(True)
        else:
            self._hidden_stack.append(parent_hidden)

    def handle_endtag(self, tag):
        if self._hidden_stack:
            self._hidden_stack.pop()

    def handle_data(self, data):
        if not any(self._hidden_stack):
            text = data.strip()
            if text:
                self.parts.append(text)


def _portal_html():
    return PORTAL_PATH.read_text(encoding="utf-8")


def _visible_text(markup):
    parser = VisibleTextParser()
    parser.feed(markup)
    return " ".join(parser.parts)


def _extract_div_by_id(html, element_id):
    marker = f'id="{element_id}"'
    marker_index = html.index(marker)
    start = html.rfind("<div", 0, marker_index)
    pos = start
    depth = 0
    while pos < len(html):
        next_open = html.find("<div", pos)
        next_close = html.find("</div>", pos)
        if next_close == -1:
            raise AssertionError(f"Could not find end of {element_id}")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
            continue
        depth -= 1
        pos = next_close + len("</div>")
        if depth == 0:
            return html[start:pos]
    raise AssertionError(f"Could not extract {element_id}")


def _extract_js_function(html, function_name):
    marker = f"function {function_name}"
    start = html.index(marker)
    brace = html.index("{", start)
    depth = 0
    for pos in range(brace, len(html)):
        char = html[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start:pos + 1]
    raise AssertionError(f"Could not extract function {function_name}")


def _assert_client_safe_text(text):
    for pattern in FORBIDDEN_CLIENT_PATTERNS:
        assert not re.search(pattern, text, flags=re.IGNORECASE), pattern


def test_client_portal_rendered_views_hide_risk_and_ai_wording():
    html = _portal_html()
    for view_id in CLIENT_VIEW_IDS:
        text = _visible_text(_extract_div_by_id(html, view_id))
        _assert_client_safe_text(text)


def test_approved_state_is_activation_only():
    text = _visible_text(_extract_div_by_id(_portal_html(), "view-approved"))
    _assert_client_safe_text(text)
    assert "Application Approved" in text
    assert "Activation in progress" in text
    assert "No Further Action Required" in text
    assert "Our team will contact you" in text


def test_no_recalculation_or_ai_risk_wording_in_portal_bundle():
    html = _portal_html()
    forbidden = (
        "Risk unavailable \u2014 recalculation required",
        "Risk unavailable - recalculation required",
        "AI-powered risk scoring",
        "5-dimension risk scoring",
        "AI risk scoring engine",
        "AI Transparency",
    )
    for phrase in forbidden:
        assert phrase not in html


def test_pricing_acceptance_routes_by_backend_status_not_frontend_risk():
    html = _portal_html()
    accept_body = _extract_js_function(html, "acceptPricing")
    assert "routePortalByBackendStatus(result.status" in accept_body
    assert "result.risk_level" not in accept_body
    assert "computedRiskLevel" not in accept_body

    route_body = _extract_js_function(html, "routePortalByBackendStatus")
    assert "nextStatus === 'pre_approval_review'" in route_body
    assert "showPreApprovalHold()" in route_body
    assert "nextStatus === 'kyc_documents'" in route_body
    assert "continueToKYCDocuments" in route_body
    assert "nextStatus === 'approved'" in route_body
    assert "showView('approved')" in route_body
    assert "nextStatus === 'rmi_sent'" in route_body
    assert "showView('client-notifications')" in route_body

    pre_approval_branch = route_body.split("nextStatus === 'pre_approval_review'", 1)[1].split("nextStatus === 'kyc_documents'", 1)[0]
    assert "showPreApprovalHold()" in pre_approval_branch
    assert "continueToKYCDocuments" not in pre_approval_branch
    assert "showView('onboarding')" not in pre_approval_branch


def test_prescreening_submit_routes_by_backend_status():
    body = _extract_js_function(_portal_html(), "submitPrescreening")
    assert "routePortalByBackendStatus(submitResp.status" in body
    assert "submitResp.risk_level" not in body
    assert "showView('risk-scoring')" not in body


def test_person_document_polling_rejects_invalid_person_identifiers():
    html = _portal_html()
    poll_body = _extract_js_function(html, "startVerificationStatusPolling")
    assert "isValidPortalPathId(options.docId)" in poll_body

    trigger_body = _extract_js_function(html, "triggerUploadKYC")
    assert "isValidPortalPathId(inputId)" in trigger_body

    upload_body = _extract_js_function(html, "handleKYCUpload")
    assert upload_body.index("isValidPortalPersonId(personId)") < upload_body.index("var docKey")
    assert "document.getElementById('kyc-person-' + personId)" in upload_body
    assert "encodeURIComponent(personId)" in upload_body

    link_body = _extract_js_function(html, "sendKYCLink")
    assert link_body.index("isValidPortalPersonId(personId)") < link_body.index("document.getElementById('kyc-email-' + personId)")
    assert "document.getElementById('kyc-person-' + personId)" in link_body

    sync_body = _extract_js_function(html, "syncPersistedApplicationDocuments")
    assert "var personId = String(rawPersonId).trim();" in sync_body
    assert "isValidPortalPersonId(personId)" in sync_body
    assert "document.getElementById('kyc-person-' + personId)" in sync_body
    assert "renderPersonVerification(personId" in sync_body
    assert "renderPersonVerification(personId, doc.doc_type, updatedRecord)" in sync_body


def test_backoffice_retains_internal_risk_visibility():
    text = BACKOFFICE_PATH.read_text(encoding="utf-8")
    assert "Risk Rating" in text
    assert "Composite Risk Score" in text
    assert "Enhanced Due Diligence" in text

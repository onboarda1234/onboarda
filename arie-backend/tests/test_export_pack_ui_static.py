import os
import re


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKOFFICE_PATH = os.path.join(REPO_ROOT, "arie-backoffice.html")
PORTAL_PATH = os.path.join(REPO_ROOT, "arie-portal.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(html, name):
    start = html.index(f"function {name}")
    brace = html.index("{", start)
    depth = 0
    for idx in range(brace, len(html)):
        char = html[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    raise AssertionError(f"Could not extract function {name}")


def test_export_pack_button_is_in_application_review_topbar_after_reassign():
    html = _read(BACKOFFICE_PATH)
    topbar_start = html.index('id="case-management-actions-topbar"')
    topbar_end = html.index('id="internal-note"', topbar_start)
    topbar = html[topbar_start:topbar_end]

    assert 'id="btn-reassign"' in topbar
    assert 'id="btn-export-pack"' in topbar
    assert 'onclick="openExportPackModal()"' in topbar
    assert topbar.index('id="btn-reassign"') < topbar.index('id="btn-export-pack"')
    assert topbar.index('id="btn-export-pack"') < html.index('id="internal-note"', topbar_start)


def test_export_pack_frontend_permission_guard_matches_backend_roles():
    html = _read(BACKOFFICE_PATH)
    fn = _extract_function(html, "canExportEvidencePack")
    sync = _extract_function(html, "syncApplicationActionPermissions")

    assert "role === 'admin' || role === 'sco'" in fn
    assert "setDetailActionVisibility('btn-export-pack', canExportPacks);" in sync
    assert "(canReassignCases || canExportPacks)" in sync
    assert "You do not have permission to export evidence packs." in html


def test_export_pack_modal_contains_required_fields_and_default_options():
    html = _read(BACKOFFICE_PATH)
    modal_start = html.index('id="modal-export-pack"')
    modal_end = html.index("<!-- ═══════════════ DECISION REASON MODAL", modal_start)
    modal = html[modal_start:modal_end]

    assert "Generate Evidence Pack" in modal
    assert 'id="export-pack-type"' in modal
    assert '<option value="regulator">Regulator Pack</option>' in modal
    assert '<option value="auditor">Auditor Pack</option>' in modal
    assert '<option value="bank_partner">Bank / Partner Pack</option>' in modal
    assert '<option value="internal_case">Internal Case Pack</option>' in modal
    assert 'id="export-pack-reason"' in modal
    assert "Example: Requested by regulator, auditor, bank partner, or internal review." in modal
    assert 'id="export-pack-redaction"' in modal
    assert '<option value="full_internal">Full internal</option>' in modal
    assert '<option value="external_redacted">External redacted</option>' in modal
    assert 'onclick="submitExportPack()"' in modal
    assert 'onclick="closeModal(\'modal-export-pack\')"' in modal

    values = re.findall(r'<input type="checkbox" value="([^"]+)" checked', modal)
    assert values == [
        "client_submission",
        "documents",
        "risk_assessment",
        "screening_summary",
        "compliance_memo",
        "officer_corrections",
        "audit_trail",
    ]


def test_export_pack_form_reset_defaults_are_backend_contract_values():
    html = _read(BACKOFFICE_PATH)
    fn = _extract_function(html, "resetExportPackForm")
    collect = _extract_function(html, "collectExportPackSections")

    assert "typeEl.value = 'regulator';" in fn
    assert "redactionEl.value = 'full_internal';" in fn
    assert "cb.checked = true;" in fn
    assert "#export-pack-sections input" in collect
    assert "return cb.value;" in collect


def test_export_pack_submit_validates_reason_sections_and_duplicate_submit():
    html = _read(BACKOFFICE_PATH)
    fn = _extract_function(html, "submitExportPack")
    loading = _extract_function(html, "setExportPackGenerating")

    assert "if (EXPORT_PACK_GENERATING) return;" in fn
    assert "Reason for export is required." in fn
    assert "Select at least one include section." in fn
    assert "submitBtn.disabled = EXPORT_PACK_GENERATING;" in loading
    assert "Generating..." in loading
    assert "Generate ZIP" in loading


def test_export_pack_submit_calls_existing_endpoint_with_expected_payload():
    html = _read(BACKOFFICE_PATH)
    fn = _extract_function(html, "submitExportPack")

    assert "export_type:" in fn
    assert "reason: reason" in fn
    assert "include_sections: includeSections" in fn
    assert "redaction_level:" in fn
    assert "method: 'POST'" in fn
    assert "'Content-Type': 'application/json'" in fn
    assert "JSON.stringify(payload)" in fn
    assert "BO_API_BASE + '/applications/' + encodeURIComponent(appId) + '/export-pack'" in fn


def test_export_pack_binary_success_path_downloads_zip_blob():
    html = _read(BACKOFFICE_PATH)
    fn = _extract_function(html, "submitExportPack")
    fallback = _extract_function(html, "exportPackFallbackFilename")

    assert "var blob = await res.blob();" in fn
    assert "window.URL.createObjectURL(blob)" in fn
    assert "getFilenameFromContentDisposition(res.headers.get('Content-Disposition'))" in fn
    assert "exportPackFallbackFilename(currentApp)" in fn
    assert "link.click();" in fn
    assert "window.URL.revokeObjectURL(url)" in fn
    assert "RegMind_Evidence_Pack_" in fallback
    assert ".zip" in fallback


def test_export_pack_errors_are_readable_and_not_raw_json():
    html = _read(BACKOFFICE_PATH)
    reader = _extract_function(html, "readExportPackError")
    submit = _extract_function(html, "submitExportPack")

    assert "You do not have permission to export evidence packs." in reader
    assert "data.error || data.message || fallback" in reader
    assert "Evidence pack export failed. Please try again or contact support." in reader
    assert "throw new Error(await readExportPackError(res));" in submit


def test_export_pack_does_not_add_portal_surface():
    portal = _read(PORTAL_PATH)
    assert "export-pack" not in portal
    assert "Export Pack" not in portal
    assert "Generate Evidence Pack" not in portal


def test_application_review_shell_controls_still_render_near_export_button():
    html = _read(BACKOFFICE_PATH)
    assert 'id="btn-open-officer-correction"' in html
    assert 'id="internal-note"' in html
    assert "Periodic Reviews" in html
    assert "Alerts" in html
    assert "function loadApplicationAlertsDetailTab(force)" in html
    assert "No Active Periodic Review" in html

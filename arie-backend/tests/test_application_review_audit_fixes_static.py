"""Static guards for the Application Review pilot audit fixes.

Three defects were found on the back-office Application Review page and fixed in
``arie-backoffice.html``. These static-source assertions (the repo's established
pattern for the large single-file UIs) lock in the fixes so they cannot silently
regress:

  P1 (critical) — memo generate/validate/approve must go through ``boApiCall`` so
     a non-2xx backend response rejects instead of flowing through the success
     branch (e.g. an officer seeing "Memo approved" after the backend rejected it).
  P2 (high) — each memo mutation must reload authoritative application detail so the
     Case Command Centre / approval blockers reflect backend truth, not stale state.
  P3 (medium) — the Approve Memo disabled-state must include the officer sign-off,
     and toggling the checkbox must re-evaluate the button.

Backend remains the source of truth; these fixes only stop the UI from showing
false success and stale gating.
"""
import os
import re


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(path):
    with open(os.path.join(_repo_root(), path), "r", encoding="utf-8") as handle:
        return handle.read()


def _func_body(html, signature):
    start = html.index(signature)
    after = html[start + len(signature):]
    match = re.search(r"\n(?:async function |function )", after)
    end = match.start() if match else len(after)
    return after[:end]


# ── P1: memo mutations use the shared wrapper, not raw fetch ──────────────────

def test_memo_mutations_use_shared_api_wrapper():
    html = _read("arie-backoffice.html")
    assert "boApiCall('POST', '/applications/' + app.ref + '/memo')" in html
    assert "boApiCall('POST', '/applications/' + app.ref + '/memo/validate')" in html
    assert (
        "boApiCall('POST', '/applications/' + app.ref + '/memo/approve', memoApproveBody)"
        in html
    )


def test_memo_mutations_drop_raw_fetch_that_ignored_response_status():
    html = _read("arie-backoffice.html")
    # The raw fetch() paths for the mutating endpoints (which never checked res.ok)
    # must be gone. The PDF download keeps its own fetch because it streams a blob.
    assert "fetch(BO_API_BASE + '/applications/' + app.ref + '/memo'," not in html
    assert "fetch(BO_API_BASE + '/applications/' + app.ref + '/memo/validate'" not in html
    assert "fetch(BO_API_BASE + '/applications/' + app.ref + '/memo/approve'" not in html


# ── P2: authoritative detail refresh after every memo state change ────────────

def test_tab_preserving_refresh_helper_exists():
    html = _read("arie-backoffice.html")
    assert "function refreshCurrentAppDetailPreservingTab(" in html
    assert "function getActiveDetailTab(" in html
    body = _func_body(html, "async function refreshCurrentAppDetailPreservingTab(")
    assert "await refreshCurrentAppDetail();" in body
    assert "switchDetailTab(activeTab)" in body


def test_each_memo_mutation_refreshes_authoritative_detail():
    html = _read("arie-backoffice.html")
    for signature in (
        "async function generateComplianceMemo(",
        "async function revalidateMemo(",
        "async function approveMemo(",
    ):
        body = _func_body(html, signature)
        assert "await refreshCurrentAppDetailPreservingTab();" in body, signature


# ── P3: Approve Memo disabled-state honours the officer sign-off ──────────────

def test_memo_approval_blockers_include_officer_signoff():
    html = _read("arie-backoffice.html")
    body = _func_body(html, "function getMemoApprovalBlockers(")
    assert "getElementById('memo-officer-signoff')" in body
    assert "Confirm the officer sign-off before submitting memo approval." in body


def test_officer_signoff_checkbox_revalidates_approve_button():
    html = _read("arie-backoffice.html")
    assert (
        'id="memo-officer-signoff" onchange="refreshMemoApprovalReasonState()"' in html
    )


# ── Tab-preserving refresh for KYC-tab actions ────────────────────────────────
# Bug report 2026-07-16: resolving an IDV exception in the KYC Documents tab
# bounced the officer back to Overview, because the success path used the
# tab-resetting refreshCurrentAppDetail(). Same defect class as the memo
# mutations above; these pin the KYC-tab actions to tab-aware refreshes.

def test_idv_resolution_returns_officer_to_kyc_documents_tab():
    html = _read("arie-backoffice.html")
    body = _func_body(html, "async function submitIdvResolution(")
    assert "await refreshCurrentKycDocumentsDetail();" in body
    assert "await refreshCurrentAppDetail();" not in body


def test_officer_correction_preserves_active_tab():
    html = _read("arie-backoffice.html")
    body = _func_body(html, "async function submitOfficerCorrection(")
    assert "await refreshCurrentAppDetailPreservingTab();" in body
    assert "await refreshCurrentAppDetail();" not in body

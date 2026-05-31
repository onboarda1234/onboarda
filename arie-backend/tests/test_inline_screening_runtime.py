"""
Runtime checks for PR 3 inline screening disposition behavior on Application Detail.

These tests execute the real front-end helper functions with a small DOM shim so
the application-detail screening controls stay pinned without requiring a live browser.
"""
import json
import os
import shutil
import subprocess
import textwrap


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_between(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _runtime_js(html, config):
    region = _extract_between(
        html,
        "function screeningQueueStatusBadge(statusKey, statusLabel) {",
        "async function renderScreening(options) {",
    )
    return "\n".join(
        [
            textwrap.dedent(
                f"""
                const CONFIG = {json.dumps(config)};
                var currentUser = CONFIG.currentUser || {{ role: 'co', name: 'Officer Test' }};
                var currentApp = CONFIG.currentApp || null;
                var currentScreeningReviewFocus = null;
                var SCREENING_QUEUE = CONFIG.screeningQueue || {{ rows: [] }};
                var SCREENING_REVIEW_ROWS = {{}};
                var SCREENING_EVIDENCE_DETAILS = {{}};
                var INLINE_SCREENING_DISPOSITION_STATE = CONFIG.inlineState || {{}};
                var INLINE_SCREENING_DISPOSITION_SUBMITTING = CONFIG.inlineSubmitting || {{}};

                function escapeHtml(value) {{
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }}

                function showToast() {{}}
                function renderScreeningReviewPanel() {{}}
                async function loadScreeningQueue() {{}}
                async function fetchApplicationDetail() {{ return CONFIG.currentApp || null; }}
                function renderAuthoritativeAppDetail() {{}}
                function switchDetailTab() {{}}
                function loadDecisionRecords() {{}}
                function loadAuditTrail() {{}}
                async function boApiCall() {{ return {{ status: 'complete' }}; }}
                """
            ),
            region,
            textwrap.dedent(
                """
                const row = CONFIG.row;
                const app = CONFIG.currentApp;
                const rationaleEmpty = screeningDispositionRationaleError('escalated', '   ');
                const clearCounter = screeningDispositionRationaleCounterText('cleared', 'Short evidence note');
                const followUpCounter = screeningDispositionRationaleCounterText('follow_up_required', 'Need more info');
                const escalateCounter = screeningDispositionRationaleCounterText('escalated', 'Escalation ready');
                const unresolvedHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                const resolvedRow = Object.assign({}, row, {
                  review_required: false,
                  review_disposition: 'cleared',
                  review_disposition_code: 'false_positive_cleared',
                  review_actionable: false,
                  status_key: 'reviewed_false_positive_cleared',
                  status_label: 'False Positive Cleared'
                });
                const resolvedHtml = renderInlineScreeningDispositionPanel(
                  app,
                  resolvedRow,
                  row.subject_type,
                  row.subject_name
                );
                const resolvedBadgeHtml = screeningReviewBadge(resolvedRow);
                const dedupedQueueStatusHtml =
                  screeningQueueStatusBadge(resolvedRow.status_key, resolvedRow.status_label) +
                  screeningReviewBadge(resolvedRow, { suppressChip: screeningReviewDisplayLabel(resolvedRow) === resolvedRow.status_label });
                const providerHighlightsHtml = providerResultHighlights([{
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_case_identifier: 'ca-case-uuid-123456',
                  provider_alert_identifier: 'alert-uuid-654321',
                  provider_profile_identifier: 'profile-uuid-pep',
                  provider_risk_identifier: 'risk-uuid-fedcba',
                  discovered_at: '2026-05-30T10:15:00Z',
                  summary: 'Potential PEP hit.',
                  date_of_birth: '1952-10-07',
                  nationality: 'RU',
                  match_strength: 'High',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_case_identifier: 'ca-case-uuid-123457',
                  provider_alert_identifier: 'alert-uuid-654322',
                  provider_profile_identifier: 'profile-uuid-pep',
                  provider_risk_identifier: 'risk-uuid-fedcbb',
                  discovered_at: '2026-05-30T10:16:00Z',
                  summary: 'Matched to provider PEP records.',
                  year_of_birth: '1952',
                  country: 'RU',
                  match_strength: 'Medium',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'Provider Screening Hit',
                  risk_type_labels: [],
                  provider_case_identifier: 'ca-case-uuid-223456',
                  provider_alert_identifier: 'alert-uuid-754321',
                  provider_profile_identifier: 'profile-uuid-watch',
                  provider_risk_identifier: 'risk-uuid-aedcba',
                  discovered_at: '2026-05-30T10:18:00Z',
                  summary: 'Provider screening hit.',
                  date_of_birth: '1952-10-07',
                  nationality: 'RU',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'Provider Screening Hit',
                  risk_type_labels: [],
                  provider_case_identifier: 'ca-case-uuid-223457',
                  provider_alert_identifier: 'alert-uuid-754322',
                  provider_profile_identifier: 'profile-uuid-watch',
                  provider_risk_identifier: 'risk-uuid-aedcbb',
                  discovered_at: '2026-05-30T10:19:00Z',
                  summary: 'Provider screening hit duplicate evidence.',
                  year_of_birth: '1952',
                  country: 'RU',
                  indicators: []
                }], {
                  application_ref: 'ARF-SCREEN-11',
                  subject_type: row.subject_type,
                  subject_name: row.subject_name,
                  provider: 'complyadvantage'
                });
                const comparisonRows = screeningComparisonRows([
                  { label: 'Name', declared: 'Vladimir Putin', provider: 'Vladimir Putin', kind: 'name' },
                  { label: 'DOB', declared: '1952-10-07', provider: '1952', kind: 'date' },
                  { label: 'Nationality', declared: 'RU', provider: 'Russia', kind: 'country' },
                  { label: 'PEP declaration', declared: 'No', provider: 'Yes', kind: 'pep' },
                  { label: 'Aliases', declared: '', provider: 'V. Putin', kind: 'aliases' },
                  { label: 'Registration number', declared: 'BRN-123', provider: '', kind: 'text' },
                  { label: 'Provider category / source', declared: '', provider: 'PEP · ComplyAdvantage', kind: 'informational' }
                ]);
                const comparisonHtml = buildScreeningComparisonPanel('person', {
                  subject_type: 'director',
                  name: 'Vladimir Putin',
                  nat: 'RU',
                  dob: '1952-10-07',
                  pep: 'No'
                }, [{
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_profile_identifier: 'profile-uuid-pep',
                  date_of_birth: '1952',
                  nationality: 'Russia',
                  aliases: ['V. Putin'],
                  indicators: []
                }], {
                  declaredPep: 'No',
                  providerPep: 'Yes'
                }, {
                  application_ref: 'ARF-SCREEN-11',
                  subject_type: 'director',
                  subject_name: 'Vladimir Putin',
                  provider: 'complyadvantage'
                });
                console.log(JSON.stringify({
                  rationaleEmpty,
                  clearCounter,
                  followUpCounter,
                  escalateCounter,
                  unresolvedHtml,
                  resolvedHtml,
                  resolvedBadgeHtml,
                  dedupedQueueStatusHtml,
                  providerHighlightsHtml,
                  comparisonRows,
                  comparisonHtml
                }));
                """
            ),
        ]
    )


def _queue_runtime_js(html):
    region = _extract_between(
        html,
        "function screeningBadge(status) {",
        "var EDD_CASES = [];",
    )
    return "\n".join(
        [
            textwrap.dedent(
                """
                var SCREENING_QUEUE = { metrics:null, rows:[], generated_at:null, load_error:null };
                var SCREENING_QUEUE_DIRTY = false;
                var SCREENING_REVIEW_ROWS = {};
                var currentUser = { role: 'co', name: 'Officer Test' };
                var BO_AUTH_TOKEN = 'token';
                var calledPaths = [];
                function escapeHtml(value) {
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }
                function showToast() {}
                function openScreeningReviewByRow() {}
                function openScreeningDispositionModalByRow() {}
                function canClearScreeningDisposition() { return true; }
                var tbody = { innerHTML: '', rows: [], appendChild(node) { this.rows.push(node); } };
                var stat = { textContent: '' };
                var document = {
                  getElementById(id) {
                    if (id === 'screening-body') return tbody;
                    if (id === 'screening-stat-awaiting' || id === 'screening-stat-screened' || id === 'screening-stat-hits') return stat;
                    return null;
                  },
                  createElement() { return { innerHTML: '' }; }
                };
                async function boApiCall(method, path) {
                  calledPaths.push(path);
                  return {
                    metrics: {
                      applications_awaiting_screening: 1,
                      applications_screened: 2,
                      applications_requiring_review: 0
                    },
                    rows: [{
                      subject_name: 'Jane Director',
                      subject_type: 'director',
                      company_name: 'Inline Screening Ltd',
                      watchlist_status: 'clear',
                      pep_declared_status: 'not_declared',
                      pep_screening_status: 'clear',
                      status_key: 'reviewed_false_positive_cleared',
                      status_label: 'False Positive Cleared',
                      review_required: false,
                      review_actionable: false
                    }]
                  };
                }
                """
            ),
            region,
            textwrap.dedent(
                """
                (async () => {
                  markScreeningQueueDirty();
                  await renderScreening({ force: SCREENING_QUEUE_DIRTY });
                  console.log(JSON.stringify({
                    dirtyAfterRender: SCREENING_QUEUE_DIRTY,
                    calledPaths,
                    renderedRows: tbody.rows.length,
                    queueRowLabel: SCREENING_QUEUE.rows[0].status_label
                  }));
                })().catch((err) => {
                  console.error(err);
                  process.exit(1);
                });
                """
            ),
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for runtime tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=os.path.dirname(BACKOFFICE_PATH),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _app():
    return {
        "id": 11,
        "ref": "ARF-SCREEN-11",
        "company": "Inline Screening Ltd",
        "brn": "BRN-123",
        "country": "Mauritius",
        "entityType": "SME",
        "screeningReviews": [],
    }


def _row(**overrides):
    base = {
        "application_id": 11,
        "application_ref": "ARF-SCREEN-11",
        "company_name": "Inline Screening Ltd",
        "subject_type": "director",
        "subject_name": "Jane Director",
        "review_required": True,
        "review_actionable": True,
        "review_four_eyes_status": "not_required",
    }
    base.update(overrides)
    return base


class TestInlineScreeningRuntime:
    def test_inline_screening_controls_render_for_unresolved_hit(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "Clear / False Positive" in result["unresolvedHtml"]
        assert "Escalate" in result["unresolvedHtml"]
        assert "Follow-Up Required" in result["unresolvedHtml"]
        assert "Save disposition" in result["unresolvedHtml"]
        assert "required for escalation" in result["unresolvedHtml"]

    def test_rationale_is_mandatory_and_permission_message_is_present(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "viewer", "name": "Viewer User"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert result["rationaleEmpty"] == "Please enter a rationale before saving this screening disposition."
        assert "You do not have permission to disposition screening results." in result["unresolvedHtml"]
        assert "40 characters" in result["clearCounter"]
        assert "false-positive clearance" in result["clearCounter"]
        assert "14 / 12 characters" in result["followUpCounter"]
        assert "16 / 12 characters" in result["escalateCounter"]

    def test_submitting_state_disables_inline_save_button(self):
        html = _read_backoffice()
        key = "ARF-SCREEN-11|director|Jane Director"
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                    "inlineState": {
                        key: {
                            "disposition": "cleared",
                            "dispositionCode": "false_positive_cleared",
                            "rationale": "Officer reviewed the provider evidence and confirmed a different identity.",
                            "evidenceReference": "Provider case CA-100 and passport copy.",
                            "error": "",
                        }
                    },
                    "inlineSubmitting": {key: True},
                },
            )
        )
        assert "Saving…" in result["unresolvedHtml"]
        assert "disabled" in result["unresolvedHtml"]

    def test_resolved_hit_is_rendered_read_only(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "read-only" in result["resolvedHtml"]
        assert "View audit trail" in result["resolvedHtml"]
        assert "Save disposition" not in result["resolvedHtml"]
        assert "False Positive Cleared" in result["resolvedBadgeHtml"]
        assert "Review Required" not in result["resolvedBadgeHtml"]
        assert result["dedupedQueueStatusHtml"].count("False Positive Cleared") == 1

    def test_screening_queue_dirty_flag_forces_refetch(self):
        html = _read_backoffice()
        result = _run_node(_queue_runtime_js(html))
        assert result["dirtyAfterRender"] is False
        assert result["renderedRows"] == 1
        assert result["queueRowLabel"] == "False Positive Cleared"
        assert result["calledPaths"]
        assert "/screening/queue?refresh=" in result["calledPaths"][0]

    def test_provider_identifiers_are_collapsed_under_technical_details(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "Evidence groups" in result["providerHighlightsHtml"]
        assert "PEP · 2 evidence records" in result["providerHighlightsHtml"]
        assert "Provider Screening Hit · 2 evidence records" in result["providerHighlightsHtml"]
        assert result["providerHighlightsHtml"].count("Vladimir Putin") >= 4
        assert "Show evidence" in result["providerHighlightsHtml"]
        assert "Technical provider details" in result["providerHighlightsHtml"]
        assert "Provider case ID" in result["providerHighlightsHtml"]
        assert "<details" in result["providerHighlightsHtml"]
        assert "Declared vs Provider Match" in result["comparisonHtml"]
        assert "Comparison shown against highest-risk provider match." in result["comparisonHtml"]
        assert "Match" in result["comparisonRows"]
        assert "Likely Match" in result["comparisonRows"]
        assert "Conflict" in result["comparisonRows"]
        assert "Missing Declared Data" in result["comparisonRows"]
        assert "Missing Provider Data" in result["comparisonRows"]
        assert "Not Comparable" in result["comparisonRows"]

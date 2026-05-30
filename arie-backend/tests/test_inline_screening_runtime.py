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
        "async function renderScreening() {",
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
                const unresolvedHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                const resolvedHtml = renderInlineScreeningDispositionPanel(
                  app,
                  Object.assign({}, row, {
                    review_required: false,
                    review_disposition: 'escalated',
                    review_actionable: false
                  }),
                  row.subject_type,
                  row.subject_name
                );
                console.log(JSON.stringify({
                  rationaleEmpty,
                  unresolvedHtml,
                  resolvedHtml
                }));
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

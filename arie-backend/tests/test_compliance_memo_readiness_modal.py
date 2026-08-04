"""Presentation-only guards for the Compliance Memorandum readiness modal."""

import json
import os
import subprocess


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKOFFICE_PATH = os.path.join(REPO_ROOT, "arie-backoffice.html")


def _backoffice_html():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _function_body(html, signature):
    start = html.index(signature)
    cursor = html.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(cursor, len(html)):
        char = html[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start : index + 1]
    raise AssertionError(f"Unterminated function: {signature}")


def _run_node(source):
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_modal_uses_professional_action_focused_copy_and_accessible_dialog_markup():
    html = _backoffice_html()
    start = html.index('id="modal-compliance-memo-readiness"')
    end = html.index("<!-- ═══════════════ ADD USER MODAL", start)
    modal = html[start:end]

    assert 'role="dialog"' in modal
    assert 'aria-modal="true"' in modal
    assert "Compliance Memorandum Cannot Be Generated" in modal
    assert (
        "The Compliance Memorandum can only be generated once all mandatory "
        "review requirements have been completed."
    ) in modal
    assert "The application is not yet ready for memorandum generation." in modal
    assert "Outstanding items" in modal
    assert "Complete the outstanding review items before generating the Compliance Memorandum." in modal
    assert "View Readiness Checklist" in modal
    assert ">Cancel<" in modal

    lowered = modal.lower()
    for forbidden in (
        "document evidence gate failed",
        "document_id",
        "slot_key",
        "uuid",
        "validation code",
        "stack trace",
        "generation failed",
    ):
        assert forbidden not in lowered


def test_generate_keeps_success_path_and_sanitizes_every_failure_surface():
    html = _backoffice_html()
    body = _function_body(html, "async function generateComplianceMemo()")

    assert "var data = await boApiCall('POST', '/applications/' + app.ref + '/memo');" in body
    assert "await refreshCurrentAppDetailPreservingTab();" in body
    assert "isComplianceMemoReadinessError(error)" in body
    assert "showComplianceMemoReadinessModal(error)" in body
    assert "error.message" not in body
    assert "Memo generation failed" not in body
    assert "Document evidence gate failed" not in body
    assert "The Compliance Memorandum could not be generated at this time." in body


def test_dynamic_summary_deduplicates_documents_and_never_renders_backend_details():
    html = _backoffice_html()
    start = html.index("var COMPLIANCE_MEMO_READINESS_RETURN_FOCUS")
    end = html.index("async function generateComplianceMemo()", start)
    helpers = html[start:end]

    review_blockers = []
    for index in range(7):
        review_blockers.append(
            {
                "code": "document_flagged",
                "document_id": f"00000000-0000-4000-8000-0000000000{index:02d}",
                "slot_key": f"entity:doc-{index}",
                "description": f"internal review flag {index}",
            }
        )
    # The same document can have more than one gate finding; count the document once.
    review_blockers.append(
        {
            "code": "missing_agent_execution_proof",
            "document_id": "00000000-0000-4000-8000-000000000000",
            "slot_key": "entity:doc-0",
            "description": "internal validation code AGENT_EXECUTION_PROOF_MISSING",
        }
    )
    blockers = review_blockers + [
        {
            "code": "missing_required_document",
            "slot_key": "entity:missing-1",
            "description": "internal missing slot",
        },
        {
            "code": "missing_required_document",
            "slot_key": "entity:missing-2",
            "description": "internal missing slot",
        },
    ]

    error = {
        "status": 409,
        "message": "Document evidence gate failed for memo generation: internal backend detail",
        "payload": {
            "error": "Document evidence gate failed; doc=00000000-0000-4000-8000-000000000000",
            "memo_reliance_status": "blocked",
            "document_evidence_gate": {"passed": False, "blocker_count": len(blockers)},
            "document_blockers": blockers,
            "enhanced_review": {"mandatory_outstanding_count": 2},
            "additional_mandatory_review_count": 1,
        },
    }

    script = f"""
const list = {{
  children: [],
  set innerHTML(value) {{ this.children = []; }},
  get innerHTML() {{ return ''; }},
  appendChild(child) {{ this.children.push(child); }}
}};
const modal = {{ classList: {{ values: [], add(value) {{ this.values.push(value); }} }} }};
const primary = {{ focused: false, focus() {{ this.focused = true; }} }};
const returnFocus = {{ focused: false, focus() {{ this.focused = true; }} }};
const target = {{
  focused: false,
  attributes: {{}},
  setAttribute(name, value) {{ this.attributes[name] = value; }},
  focus() {{ this.focused = true; }}
}};
const elements = {{
  'modal-compliance-memo-readiness': modal,
  'memo-readiness-outstanding-list': list,
  'memo-readiness-view-checklist': primary,
  'detail-case-command-centre': target
}};
global.document = {{
  activeElement: returnFocus,
  getElementById(id) {{ return elements[id] || null; }},
  createElement(tag) {{ return {{ tagName: tag, textContent: '' }}; }}
}};
global.requestAnimationFrame = function(callback) {{ callback(); }};
let closed = [];
let focusCaseCommandCentreCalls = 0;
function closeModal(id) {{ closed.push(id); }}
function focusCaseCommandCentre() {{ focusCaseCommandCentreCalls += 1; }}
{helpers}
const readinessError = {json.dumps(error)};
showComplianceMemoReadinessModal(readinessError);
const visibleItems = list.children.map(function(item) {{ return item.textContent; }});
const classified = isComplianceMemoReadinessError(readinessError);
const unrelatedFailureClassified = isComplianceMemoReadinessError({{ status: 500, payload: {{ error: 'database unavailable' }} }});
viewComplianceMemoReadinessChecklist();
process.stdout.write(JSON.stringify({{
  visibleItems,
  classified,
  unrelatedFailureClassified,
  modalOpen: modal.classList.values.indexOf('open') >= 0,
  primaryFocused: primary.focused,
  closed,
  focusCaseCommandCentreCalls,
  targetFocused: target.focused,
  targetTabIndex: target.attributes.tabindex
}}));
"""
    result = _run_node(script)

    assert result["visibleItems"] == [
        "7 documents require review",
        "2 mandatory documents remain outstanding",
        "Enhanced Review is incomplete",
        "1 additional mandatory review item remains",
    ]
    visible = " ".join(result["visibleItems"]).lower()
    for forbidden in (
        "00000000",
        "document evidence gate",
        "agent_execution_proof_missing",
        "slot",
        "internal",
    ):
        assert forbidden not in visible
    assert result["classified"] is True
    assert result["unrelatedFailureClassified"] is False
    assert result["modalOpen"] is True
    assert result["primaryFocused"] is True
    assert result["closed"] == ["modal-compliance-memo-readiness"]
    assert result["focusCaseCommandCentreCalls"] == 1
    assert result["targetFocused"] is True
    assert result["targetTabIndex"] == "-1"

"""Runtime proof of the client portal upload privacy boundary.

The static guards in ``test_client_portal_upload_privacy_static.py`` pin the
source. This module actually *executes* the portal's document renderer under a
minimal DOM stub with a fully-populated verification payload — red flags,
warnings, per-check pass/fail results, check identifiers, confidence, extracted
values and declared-vs-extracted mismatches — and proves:

1. none of it reaches the client-facing markup;
2. the client sees byte-identical copy whether the document verified or failed;
3. the machine-readable state the submission gate reads is unchanged;
4. the same payload still renders in full for compliance officers in the
   RegMind back office.
"""

import re
import shutil
import subprocess
from pathlib import Path

from tests.test_kyc_docs_verification_details_and_reverify_static import (
    _backoffice_html,
    _js_prelude,
    _run_node,
    _verification_render_functions,
)


ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = ROOT / "arie-portal.html"


def _portal_html() -> str:
    return PORTAL_HTML.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"function {name}")
    brace = src.index("{", start)
    depth = 0
    for index in range(brace, len(src)):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"could not extract function {name}")


PORTAL_FUNCTIONS = (
    "escapeHtml",
    "sanitizeClientPortalCopy",
    "normalizePersonType",
    "isValidPortalPathId",
    "isValidPortalPersonId",
    "getKYCCardIdentity",
    "findKYCPersonCard",
    "getKYCPersonType",
    "getDocumentSlotKey",
    "ensureVerificationList",
    "isCurrentDocumentRecord",
    "clearVerificationForSlot",
    "renderPersistedVerification",
    "slotVerificationIsSuccessful",
)


def _portal_render_functions(html: str) -> str:
    constants = re.findall(r"^var CLIENT_UPLOAD_RECEIPT_[A-Z_]+ = '.*';$", html, flags=re.M)
    assert len(constants) == 2, constants
    functions = "\n\n".join(_extract_function(html, name) for name in PORTAL_FUNCTIONS)
    return "\n".join(constants) + "\n\n" + functions


def _dom_stub() -> str:
    return r"""
function assert(condition, message) {
  if (!condition) { throw new Error(message); }
}

function makeElement(tag) {
  return {
    tagName: tag,
    id: '',
    className: '',
    dataset: {},
    style: { cssText: '' },
    innerHTML: '',
    parentElement: null,
    _attrs: {},
    _children: [],
    setAttribute: function(key, value) { this._attrs[key] = String(value); },
    getAttribute: function(key) {
      return Object.prototype.hasOwnProperty.call(this._attrs, key) ? this._attrs[key] : null;
    },
    appendChild: function(child) {
      child.parentElement = this;
      this._children.push(child);
      return child;
    },
    remove: function() {
      if (!this.parentElement) return;
      var siblings = this.parentElement._children;
      var index = siblings.indexOf(this);
      if (index >= 0) siblings.splice(index, 1);
      this.parentElement = null;
    },
    _find: function(selector) {
      var wanted = selector.replace(/^\./, '');
      var found = [];
      this._children.forEach(function(child) {
        if (String(child.className).split(/\s+/).indexOf(wanted) >= 0) found.push(child);
        found = found.concat(child._find(selector));
      });
      return found;
    },
    querySelector: function(selector) { return this._find(selector)[0] || null; },
    querySelectorAll: function(selector) { return this._find(selector); }
  };
}

var document = {
  createElement: makeElement,
  getElementById: function() { return null; },
  querySelectorAll: function() { return []; }
};

// Every internal finding the client must never see.
var FINDINGS = {
  ai_source: 'live',
  verified_at: '2026-08-01T09:15:00Z',
  confidence: 0.62,
  red_flags: ['Registration number mismatch against the declared entity'],
  warnings: ['Document expiry falls within 30 days'],
  checks: [
    {
      id: 'DOC-NAME-01',
      label: 'Name Match',
      result: 'fail',
      classification: 'model-generated',
      source: 'document-intelligence',
      message: 'Extracted name does not match the declared director.',
      ps_field: 'director_full_name',
      ps_value: 'Jane Doe',
      extracted_value: 'J. D. Smith'
    },
    {
      id: 'DOC-AUTH-02',
      label: 'Authenticity',
      result: 'warn',
      message: 'Missing issuer stamp and signature; possible tampering.'
    },
    {
      id: 'DOC-QUAL-03',
      label: 'Document Quality Score',
      result: 'warn',
      message: 'Quality score 0.41 — document is difficult to read.'
    }
  ]
};

function flaggedRecord(name) {
  return {
    id: 'doc_flagged',
    doc_name: name,
    doc_type: 'passport',
    slot_key: 'entity:passport',
    is_current: true,
    version: 2,
    verification_status: 'flagged',
    verification_state: 'flagged',
    verification_success: false,
    verification_status_label: 'Review required',
    verification_status_tone: 'warning',
    verification_results: FINDINGS
  };
}

function verifiedRecord(name) {
  return {
    id: 'doc_verified',
    doc_name: name,
    doc_type: 'passport',
    slot_key: 'entity:passport',
    is_current: true,
    version: 2,
    verification_status: 'verified',
    verification_state: 'verified',
    verification_success: true,
    verification_status_label: 'Verified',
    verification_status_tone: 'success',
    verification_results: { ai_source: 'live', confidence: 0.98, checks: [
      { id: 'DOC-NAME-01', label: 'Name Match', result: 'pass', message: 'Name matches the declared director.' }
    ] }
  };
}

function renderInto(record) {
  var target = makeElement('div');
  renderPersistedVerification(target, record);
  return { target: target, card: target.querySelector('.doc-verify-results') };
}
"""


LEAKED_STRINGS = (
    "Name Match",
    "DOC-NAME-01",
    "DOC-AUTH-02",
    "DOC-QUAL-03",
    "Authenticity",
    "Document Quality Score",
    "Extracted name does not match",
    "Missing issuer stamp",
    "Quality score",
    "director_full_name",
    "Jane Doe",
    "J. D. Smith",
    "Registration number mismatch",
    "Document expiry falls within 30 days",
    "Red flags",
    "Warnings",
    "Confidence",
    "62%",
    "Review required",
    "2026-08-01",
)


def test_client_receipt_hides_every_verification_finding():
    html = _portal_html()
    leaked = "\n".join(
        "assert(markup.indexOf({0}) < 0, 'leaked: ' + {0});".format(repr(phrase).replace("'", '"'))
        for phrase in LEAKED_STRINGS
    )
    script = f"""
{_dom_stub()}
{_portal_render_functions(html)}

var rendered = renderInto(flaggedRecord('passport.pdf'));
assert(rendered.card, 'an upload receipt card should be rendered');
var markup = rendered.card.innerHTML;

{leaked}

assert(markup.indexOf('passport.pdf') >= 0, 'the client should still see their own file name');
assert(markup.indexOf('Upload successful') >= 0, 'the client should see the upload outcome');
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_client_receipt_is_identical_for_verified_and_flagged_documents():
    html = _portal_html()
    script = f"""
{_dom_stub()}
{_portal_render_functions(html)}

var flagged = renderInto(flaggedRecord('passport.pdf'));
var verified = renderInto(verifiedRecord('passport.pdf'));

assert(flagged.card.innerHTML === verified.card.innerHTML,
  'client-visible copy must not differ by verification outcome:\\n' +
  flagged.card.innerHTML + '\\n---\\n' + verified.card.innerHTML);
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_submission_gate_state_survives_the_hidden_rendering():
    html = _portal_html()
    script = f"""
{_dom_stub()}
{_portal_render_functions(html)}

var flagged = renderInto(flaggedRecord('passport.pdf'));
assert(flagged.card.getAttribute('data-doc-id') === 'doc_flagged', 'doc id attribute');
assert(flagged.card.getAttribute('data-doc-slot') === 'entity:passport', 'slot attribute');
assert(flagged.card.getAttribute('data-verification-success') === 'false', 'flagged is not success');
assert(flagged.card.getAttribute('data-verification-state') === 'flagged', 'state attribute');
assert(flagged.card.getAttribute('data-reliance-state') === 'flagged', 'reliance attribute');
assert(slotVerificationIsSuccessful(flagged.target, 'entity:passport') === false,
  'gate must still block a flagged document');

var verified = renderInto(verifiedRecord('passport.pdf'));
assert(verified.card.getAttribute('data-verification-success') === 'true', 'verified is success');
assert(verified.card.getAttribute('data-verification-state') === 'verified', 'state attribute');
assert(slotVerificationIsSuccessful(verified.target, 'entity:passport') === true,
  'gate must still pass a verified document');

var blocked = verifiedRecord('passport.pdf');
blocked.document_reliance_state = 'blocked';
var blockedRender = renderInto(blocked);
assert(blockedRender.card.getAttribute('data-verification-success') === 'false',
  'blocked reliance must not count as verified');
assert(slotVerificationIsSuccessful(blockedRender.target, 'entity:passport') === false,
  'gate must still block an unreliable document');
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_backoffice_still_renders_the_same_findings_for_officers():
    assert shutil.which("node"), "Node.js is required for these rendering checks"
    html = _backoffice_html()
    script = f"""
{_js_prelude()}
{_verification_render_functions(html)}
var rendered = buildVerificationResultsHtml({{
  ai_source: 'live',
  verified_at: '2026-08-01T09:15:00Z',
  overall: 'flagged',
  checks: [
    {{ id: 'DOC-NAME-01', label: 'Name Match', result: 'fail', method: 'AI-Generated',
       message: 'Extracted name does not match the declared director.' }},
    {{ id: 'DOC-AUTH-02', label: 'Authenticity', result: 'warn', method: 'Hybrid',
       message: 'Missing issuer stamp and signature; possible tampering.' }},
    {{ id: 'DOC-QUAL-03', label: 'Document Quality Score', result: 'warn', method: 'Deterministic',
       message: 'Quality score 0.41 — document is difficult to read.' }}
  ]
}}, {{}}, {{ uploadedBy: 'Officer', stateLabel: 'Review required' }});

assert(rendered.indexOf('Name Match') >= 0, 'officers must still see the name-match finding');
assert(rendered.indexOf('Extracted name does not match the declared director.') >= 0,
  'officers must still see the mismatch detail');
assert(rendered.indexOf('Authenticity') >= 0, 'officers must still see the authenticity finding');
assert(rendered.indexOf('Missing issuer stamp and signature; possible tampering.') >= 0,
  'officers must still see the tampering detail');
assert(rendered.indexOf('Document Quality Score') >= 0, 'officers must still see the quality finding');
assert(rendered.indexOf('Checks requiring attention') >= 0, 'officers keep the attention section');
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_node_is_available_for_the_rendering_harness():
    assert shutil.which("node")
    assert subprocess.run(["node", "--version"], capture_output=True, check=False).returncode == 0

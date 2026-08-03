"""Executes the portal's upload-error classifier against real backend strings.

The static guards pin the source; this executes it. Every message the backend
can actually return for a rejected upload is fed through the classifier, and
the result must be one of the three fixed client messages — never the backend
text itself.

Backend strings below are copied from:
  server.py                — "File exceeds {MAX_UPLOAD_MB}MB limit"
  security_hardening.py    — FileUploadValidator.validate_with_reason
"""

import json
import re
from pathlib import Path

from tests.test_kyc_docs_verification_details_and_reverify_static import _run_node


ROOT = Path(__file__).resolve().parents[2]
PORTAL_PATH = ROOT / "arie-portal.html"

TOO_LARGE = "File exceeds the maximum allowed size. Please upload a smaller file."
UNSUPPORTED = "This file type is not supported. Please upload one of the supported file formats."
GENERIC = "We couldn’t upload your document. Please try again."


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


def _classifier_source():
    html = PORTAL_PATH.read_text(encoding="utf-8")
    table = re.search(
        r"var CLIENT_UPLOAD_ERROR_COPY = \{.*?\};", html, flags=re.S
    )
    assert table, "CLIENT_UPLOAD_ERROR_COPY table not found"
    return "\n\n".join(
        [
            table.group(0),
            _extract_js_function(html, "classifyClientUploadError"),
            _extract_js_function(html, "clientUploadErrorMessage"),
        ]
    )


# (backend message, expected client message)
CASES = [
    # server.py per-file size guard
    ("File exceeds 10MB limit", TOO_LARGE),
    ("File exceeds 25MB limit", TOO_LARGE),
    # FileUploadValidator size guard, wrapped by the handler
    (
        "File rejected: File size 12582912 bytes exceeds maximum of 10485760 bytes (10MB)",
        TOO_LARGE,
    ),
    # FileUploadValidator extension guard
    (
        "File rejected: File type '.exe' not allowed. Allowed: .pdf, .docx, .xlsx, .pptx, .png, .jpg, .jpeg",
        UNSUPPORTED,
    ),
    # FileUploadValidator MIME guard
    (
        "File rejected: Content-Type 'application/x-msdownload' not allowed. Allowed: application/pdf",
        UNSUPPORTED,
    ),
    # FileUploadValidator magic-bytes guard
    (
        "File rejected: File content does not match claimed file type (magic bytes mismatch)",
        UNSUPPORTED,
    ),
    # Anything else stays generic — including server internals and transport errors
    ("Internal server error", GENERIC),
    ("Request failed", GENERIC),
    ("Failed to fetch", GENERIC),
    ("NetworkError when attempting to fetch resource.", GENERIC),
    ("Traceback (most recent call last): File \"server.py\", line 12510", GENERIC),
    ("No file provided", GENERIC),
    ("Document upload failed. Please try again or contact support.", GENERIC),
    ("", GENERIC),
]


def test_backend_upload_errors_map_to_fixed_client_messages():
    cases_json = json.dumps([{"raw": raw, "expected": expected} for raw, expected in CASES])
    script = f"""
{_classifier_source()}

var CASES = {cases_json};
var failures = [];
CASES.forEach(function(testCase) {{
  var actual = clientUploadErrorMessage({{ message: testCase.raw }});
  if (actual !== testCase.expected) {{
    failures.push('raw=' + JSON.stringify(testCase.raw) +
      ' expected=' + JSON.stringify(testCase.expected) +
      ' actual=' + JSON.stringify(actual));
  }}
}});
if (failures.length) {{
  throw new Error('mapping failures:\\n' + failures.join('\\n'));
}}
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_no_backend_text_survives_into_the_client_message():
    """Distinctive backend tokens must not appear in any produced message."""
    leak_tokens = [
        ".exe",
        "Allowed:",
        "bytes",
        "magic bytes",
        "Content-Type",
        "application/x-msdownload",
        "Traceback",
        "server.py",
        "10MB",
        "10485760",
        "Internal server error",
        "File rejected",
    ]
    cases_json = json.dumps([raw for raw, _ in CASES])
    tokens_json = json.dumps(leak_tokens)
    script = f"""
{_classifier_source()}

var CASES = {cases_json};
var TOKENS = {tokens_json};
var leaks = [];
CASES.forEach(function(raw) {{
  var message = clientUploadErrorMessage({{ message: raw }});
  TOKENS.forEach(function(token) {{
    if (message.indexOf(token) >= 0) {{
      leaks.push('raw=' + JSON.stringify(raw) + ' leaked ' + JSON.stringify(token));
    }}
  }});
}});
if (leaks.length) {{
  throw new Error('leaked backend text:\\n' + leaks.join('\\n'));
}}
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")


def test_only_three_messages_can_ever_be_produced():
    cases_json = json.dumps([raw for raw, _ in CASES] + ["anything at all", "42", "null"])
    script = f"""
{_classifier_source()}

var ALLOWED = [
  {json.dumps(TOO_LARGE)},
  {json.dumps(UNSUPPORTED)},
  {json.dumps(GENERIC)}
];
var CASES = {cases_json};
var bad = [];
CASES.concat([undefined, null]).forEach(function(raw) {{
  var message = clientUploadErrorMessage(raw == null ? raw : {{ message: raw }});
  if (ALLOWED.indexOf(message) < 0) {{
    bad.push('raw=' + JSON.stringify(raw) + ' produced ' + JSON.stringify(message));
  }}
}});
if (bad.length) {{
  throw new Error('unexpected message:\\n' + bad.join('\\n'));
}}
console.log('ok');
"""
    assert _run_node(script).strip().endswith("ok")

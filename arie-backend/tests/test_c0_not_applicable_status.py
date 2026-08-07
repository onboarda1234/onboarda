"""C0 — a document whose checks were ALL skipped must not present as verified.

Covers the Section A audit P0 finding: a Regulatory Licence uploaded for a
client who declared no licence (LIC-GATE short-circuit) — and a retired
document type — produced overall "verified" with zero executed checks, and
rendered as "✓ Verified" / reliance-ready in the back office.

The fix maps a clean all-skip outcome to the established "skipped" document
state (verification_state.STATE_SKIPPED) with a not_applicable marker, and the
back office renders it as "Not applicable" — never Verified, never Failed.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")

from document_verification import (  # noqa: E402
    CheckClassification,
    _aggregate,
    _pass,
    _skip,
    to_legacy_result,
    verify_document_layered,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKOFFICE = os.path.join(REPO_ROOT, "arie-backoffice.html")
SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── Aggregation semantics ─────────────────────────────────────────


class TestAggregateAllSkip:
    def test_all_skip_is_skipped_not_verified(self):
        result = _aggregate([
            _skip("LIC-GATE", "Licence Applicability Gate", CheckClassification.RULE,
                  "Regulatory licence checks skipped — client declared no licence"),
        ])
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True
        assert "declared no licence" in result["skipped_reason"]

    def test_multiple_skips_still_skipped(self):
        result = _aggregate([
            _skip("X-1", "Gate A", CheckClassification.RULE, "skipped A"),
            _skip("X-2", "Gate B", CheckClassification.RULE, "skipped B"),
        ])
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True

    def test_mixed_skip_and_pass_remains_verified(self):
        """A partial skip alongside real passes is still a verified outcome."""
        result = _aggregate([
            _skip("X-1", "Gate", CheckClassification.RULE, "gate skipped"),
            _pass("DOC-05", "Entity Name Match", CheckClassification.RULE, "match"),
        ])
        assert result["overall"] == "verified"
        assert "not_applicable" not in result

    def test_empty_checks_stay_flagged(self):
        """P0-5 parity: no checks at all is an error state, not not-applicable."""
        result = _aggregate([])
        assert result["overall"] == "flagged"
        assert "not_applicable" not in result

    def test_legacy_result_preserves_marker(self):
        layered = _aggregate([
            _skip("LIC-GATE", "Licence Applicability Gate", CheckClassification.RULE,
                  "Regulatory licence checks skipped — client declared no licence"),
        ])
        legacy = to_legacy_result(layered)
        assert legacy["overall"] == "skipped"
        assert legacy["not_applicable"] is True
        assert legacy["skipped_reason"]


# ── Engine entry point: real short-circuit paths ──────────────────


class TestLayeredEngineSkipPaths:
    def _run(self, doc_type, prescreening):
        return verify_document_layered(
            doc_type=doc_type,
            category="entity",
            file_path=None,
            file_size=0,
            mime_type="application/pdf",
            prescreening_data=prescreening,
            risk_level="LOW",
            existing_hashes=[],
        )

    def test_licence_not_applicable_is_skipped(self):
        result = self._run("licence", {"regulatory_licences": "None"})
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True
        checks = result["checks"]
        assert len(checks) == 1
        assert checks[0]["id"] == "LIC-GATE"
        assert checks[0]["result"] == "skip"

    def test_retired_doc_type_is_skipped(self):
        result = self._run("cert_reg", {"registered_entity_name": "Acme Ltd"})
        assert result["overall"] == "skipped"
        assert result["not_applicable"] is True

    def test_applicable_licence_is_not_short_circuited(self):
        """When a licence IS held the gate must not short-circuit to skipped —
        the engine proceeds (and, with no file, flags for review instead)."""
        result = self._run("licence", {"regulatory_licences": "FSC Investment Dealer Licence"})
        assert result["overall"] != "skipped"


# ── Server status mapping (source-level guard) ────────────────────


class TestServerStatusMapping:
    def test_all_skipped_maps_to_state_skipped(self):
        src = _read(SERVER)
        assert "_all_checks_skipped" in src, "C0 all-skip mapping missing from server.py"
        block = src.split("_all_checks_skipped = bool(checks)", 1)[1][:600]
        assert "status = STATE_SKIPPED" in block
        assert "STATE_VERIFIED if all_passed else STATE_FLAGGED" in block

    def test_skipped_status_carries_no_verified_at(self):
        src = _read(SERVER)
        assert '_verified_at_sql = "NULL" if status == STATE_SKIPPED' in src


# ── Back office rendering (static guards) ─────────────────────────


class TestBackofficeNotApplicableRendering:
    @pytest.fixture(scope="class")
    def html(self):
        return _read(BACKOFFICE)

    def test_display_state_has_not_applicable_branch(self, html):
        assert "{ label: 'Not applicable', tone: 'skipped' }" in html

    def test_not_applicable_branch_precedes_failed_mapping(self, html):
        """The branch must run before the generic skipped→Failed mapping,
        otherwise a not-applicable document renders as Failed."""
        fn = html.split("function documentRelianceDisplayState(doc, policy) {", 1)[1]
        fn = fn.split("\nfunction ", 1)[0]
        na = fn.index("Not applicable")
        failed_map = fn.index("['failed', 'flagged', 'skipped'].indexOf(verificationState)")
        assert na < failed_map, "Not-applicable branch must precede the skipped→Failed mapping"

    def test_guarded_against_agent_disabled_skip(self, html):
        """The operational Agent-1-disabled skip (checks: []) must keep its
        existing treatment — the branch requires the marker or a non-empty
        all-skip check list."""
        assert "results.not_applicable === true || allChecksSkipped" in html
        assert "checks.length > 0 && checks.every(function(check)" in html

    def test_required_action_copy(self, html):
        assert "No verification checks apply to this document" in html

    def test_badge_css_exists(self, html):
        assert ".reliance-badge.skipped" in html

    def test_coverage_not_applicable_state(self, html):
        assert "runState = 'not_applicable';" in html
        assert "Verification not applicable — all checks were skipped" in html

    def test_coverage_badge_mapping(self, html):
        assert "coverage.runState === 'not_applicable' ? 'Not applicable'" in html

    def test_verified_group_unreachable_for_not_applicable(self, html):
        """documentReviewGroupKey routes to 'verified' only for the labels
        'Verified' and 'Manual accepted' — 'Not applicable' must not be one."""
        fn = html.split("function documentReviewGroupKey(", 1)[1].split("\nfunction ", 1)[0]
        verified_line = next(
            line for line in fn.splitlines() if "return 'verified'" in line
        )
        assert "Not applicable" not in verified_line

"""Founder-reported: the zero-hit Screening Review card was wrong twice.

Observed on ARF-2026-100435 (PR Attestation Test — Change Ltd), a terminal,
live, zero-hit entity:

1. "Loading ranked screening hits…" spun forever. The lazy-evidence branch is
   entered whenever a triage block exists — and a zero-hit subject HAS one, all
   buckets zero. The fetch returned nothing (correctly), items stayed empty, and
   the same branch re-rendered. There was no reachable state that resolved the
   spinner, on what is the most common subject state in a pilot.

2. The state note contradicted itself::

       Evidence readiness: Unavailable · Provider screening completed with no
       hits; source/article evidence is not applicable. · Detailed provider
       evidence is unavailable for this screening result.

   "Not applicable" (correct — there are no hits, so no article evidence exists)
   and "Unavailable" (wrong — it reads as "we could not check") in one line. The
   quality classifier defaults to 'Unavailable' when there is no evidence
   status, and a clean result has none, so a complete live screening was
   labelled degraded.

Both now key off ONE fail-closed helper so the label, the note and the hit list
cannot disagree.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _fn(name):
    start = HTML.index("function " + name)
    end = HTML.index("\nfunction ", start + 1)
    return HTML[start:end]


def _strip_comments(src):
    """Drop // comment lines so assertions test CODE, not prose.

    Several of these checks assert a token is absent, or that one branch
    precedes another. Comments in this area legitimately quote the very strings
    and status names being asserted on, which would otherwise produce false
    failures (and, worse, false passes).
    """
    out = []
    for line in src.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


# --- the shared, fail-closed detector ----------------------------------------

def test_detector_requires_canonical_terminal_status_and_zero_hits():
    fn = _strip_comments(_fn("screeningQueueIsTerminalNoHits"))
    # CANONICAL status only. The legacy status_key fallback was a latent
    # fail-open: server.py emits 'screened_no_match' for NEVER-SCREENED
    # subjects (no record => total_hits 0), which the canonical resolver
    # correctly calls screening_in_progress.
    assert "row.canonical_status_key || ''" in fn
    assert "row.status_key" not in fn, "legacy status_key fallback is a fail-open"
    assert "'screened_no_match'" not in fn, "never emitted by the canonical resolver"
    # An explicit integer zero — Number(x || 0) is satisfied by MISSING data.
    assert "typeof row.total_hits !== 'number'" in fn
    assert "Number(row.total_hits || 0)" not in fn
    # Never assert "no matches" over a truncated evidence set, or over items.
    assert "summary.evidence_capped" in fn
    assert "screening_evidence" in fn and "items" in fn


def test_detector_admits_no_other_status():
    fn = _strip_comments(_fn("screeningQueueIsTerminalNoHits"))
    for forbidden in (
        "review_required",
        "failed",
        "stale",
        "screening_unavailable",
        "pending",
        "declared_pep_review",
        "in_progress",
        # These are the tokens a well-meaning future edit would add. The first
        # is the dangerous one: a subject cleared BY AN OFFICER has hits by
        # construction, so admitting it would render "No provider matches"
        # over real matches.
        "cleared_by_officer",
        "completed_clear",
        "no_match",
        "reviewed",
        "escalated",
        "follow_up_required",
    ):
        assert forbidden not in fn, forbidden


def test_detector_body_admits_exactly_one_accepting_condition():
    """Structural guard: substring assertions alone cannot catch an ADDED
    accepting branch. Two fail-open mutations previously passed this file —
    including one admitting 'cleared_by_officer'. Pin the shape: the helper may
    compare exactly one status literal, and every other return is a rejection.
    """
    body = _strip_comments(_fn("screeningQueueIsTerminalNoHits"))
    body = body[body.index("{") + 1:]
    # Exactly one status-string comparison.
    assert body.count("=== 'clear'") + body.count("!== 'clear'") == 1
    # Every early return is a rejection; the only truthy result is the final
    # computed expression.
    assert body.count("return true") == 0, "no unconditional accept may exist"
    assert body.count("return false") >= 3


# --- defect 1: the permanent spinner -----------------------------------------

def test_zero_hit_renders_an_empty_state_not_a_loading_state():
    region = HTML[HTML.index("var evidenceCards = ''"):]
    region = _strip_comments(region[: region.index("var stateNoteBits")])
    # The zero-hit branch must come BEFORE the lazy-evidence loading branch,
    # otherwise the spinner still wins.
    empty_at = region.index("data-screening-no-hits-empty")
    loading_at = region.index("Loading ranked screening hits")
    assert empty_at < loading_at, "zero-hit empty state must pre-empt the loading branch"
    assert "} else if (isTerminalNoHits) {" in region
    assert "No provider matches for this subject" in region


def test_zero_hit_does_not_trigger_the_pointless_evidence_fetch():
    # ensureApplicationScreeningEvidenceRows lives in the loading branch only;
    # a zero-hit subject must never reach it (nothing to hydrate).
    region = HTML[HTML.index("var evidenceCards = ''"):]
    region = _strip_comments(region[: region.index("var stateNoteBits")])
    fetch_at = region.index("ensureApplicationScreeningEvidenceRows")
    empty_at = region.index("data-screening-no-hits-empty")
    assert empty_at < fetch_at


# --- defect 2: the self-contradicting state note -----------------------------

def test_quality_label_is_not_applicable_for_zero_hits():
    fn = _fn("screeningQueueEvidenceQuality")
    assert "screeningQueueIsTerminalNoHits(row)" in fn
    assert "'Not applicable'" in fn
    # Applied to the server-supplied label too, not only the default fallback —
    # the server may explicitly send evidence_status='unavailable'.
    assert "String(label).toLowerCase() === 'unavailable'" in fn


def test_partial_message_is_suppressed_for_zero_hits():
    # The third clause ("...is partial or unavailable") is the one that
    # contradicted the second. Nothing is partial when there is nothing to show.
    region = HTML[HTML.index("var partialMessage = isLoadingFullEvidence"):]
    region = _strip_comments(region[: region.index("var missingSourceMessage")])
    assert "isTerminalNoHits" in region
    assert "? ''" in region


def test_explanatory_reason_survives_the_rename():
    # screeningQueueEvidenceQualityReason early-returns unless the label is
    # 'unavailable'; renaming to 'Not applicable' must not silence the
    # "completed with no hits" sentence.
    fn = _fn("screeningQueueEvidenceQualityReason")
    assert "normalizedLabel !== 'not applicable'" in fn
    assert "completed with no hits" in fn


def test_unavailable_is_still_used_when_evidence_really_is_missing():
    # Guard against over-reach: a subject WITH hits but no evidence, or a failed
    # screen, must still read "Unavailable" — that one is honest.
    fn = _fn("screeningQueueEvidenceQuality")
    assert "'Unavailable'" in fn  # the mapping/default is retained
    reason = _fn("screeningQueueEvidenceQualityReason")
    assert "Provider screening failed before detailed evidence was available." in reason
    assert "Detailed provider evidence is unavailable for this screening result." in reason


# --- the server-side invariant the client fix RESTS ON -----------------------
#
# The whole zero-hit UI change is only safe because a canonically-"clear" row is
# guaranteed to have zero hits and zero evidence. That guarantee is currently an
# emergent property of _queue_provider_terminal + _queue_evidence_incomplete +
# post-enrichment re-resolution — nothing pinned it, so a future perf change
# could quietly break the client's fail-closed assumption. Pin it here.

import pytest

from screening_state import resolve_screening_queue_state


def _resolved(row):
    return resolve_screening_queue_state(dict(row))


@pytest.mark.parametrize(
    "label,row",
    [
        ("simulated", {"provider_availability": "simulated", "terminal": True, "total_hits": 0}),
        ("sandbox", {"provider_availability": "sandbox", "terminal": True, "total_hits": 0}),
        ("simulated_fallback mode", {"provider_mode": "simulated_fallback", "terminal": True, "total_hits": 0}),
        ("sandbox_provider mode", {"provider_mode": "sandbox_provider", "terminal": True, "total_hits": 0}),
        ("pending", {"provider_availability": "pending", "terminal": True, "total_hits": 0}),
        ("stale", {"provider_availability": "stale", "terminal": True, "total_hits": 0}),
        ("failed", {"provider_availability": "failed", "terminal": True, "total_hits": 0}),
        ("not_configured", {"provider_availability": "not_configured", "terminal": True, "total_hits": 0}),
        ("not_started state", {"screening_state": "not_started", "terminal": True, "total_hits": 0}),
        ("declared pep", {"provider_mode": "live_provider", "terminal": True, "total_hits": 0,
                          "pep_declared_status": "declared"}),
        ("evidence items present", {"provider_mode": "live_provider", "terminal": True, "total_hits": 0,
                                    "screening_evidence": {"items": [{"category": "pep"}]}}),
        ("hits present", {"provider_mode": "live_provider", "terminal": True, "total_hits": 3}),
    ],
)
def test_canonical_clear_is_never_emitted_for_a_non_clean_row(label, row):
    resolved = _resolved(row)
    key = str(resolved.get("canonical_status_key") or resolved.get("status_key") or "").lower()
    assert key != "clear", f"{label!r} resolved to canonical clear: {resolved}"


def test_bare_terminal_without_provenance_is_a_known_resolver_gap():
    """Documented, pre-existing contract weakness — pinned so it stays visible.

    _queue_provider_terminal trusts an explicit ``terminal: True`` when the row
    carries NO provenance token at all (screening_state.py:1672-1676), so such a
    row resolves to canonical "clear" without proven live provenance. It is not
    reachable from _build_screening_queue_payload, which always injects a
    provider mode before resolving, and it predates this change. Pinned here so
    that if the builder ever stops injecting mode, this test — not a compliance
    officer — is what notices.
    """
    resolved = _resolved({"terminal": True, "total_hits": 0})
    key = str(resolved.get("canonical_status_key") or "").lower()
    assert key == "clear", (
        "resolver behaviour changed — if this now fails closed, delete this test "
        "and tighten the note in screeningQueueIsTerminalNoHits"
    )


def test_canonical_clear_implies_zero_hits_and_zero_evidence():
    resolved = _resolved({"provider_mode": "live_provider", "terminal": True, "total_hits": 0})
    key = str(resolved.get("canonical_status_key") or resolved.get("status_key") or "").lower()
    assert key == "clear"
    # The two facts the client helper relies on.
    assert int(resolved.get("total_hits") or 0) == 0
    assert not ((resolved.get("screening_evidence") or {}).get("items") or [])

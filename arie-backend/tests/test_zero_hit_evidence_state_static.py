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
    fn = _fn("screeningQueueIsTerminalNoHits")
    # Canonical status first — a degraded/pending/errored/stale/conflict state
    # must never be mistaken for a clean zero-hit result.
    assert "row.canonical_status_key || row.status_key" in fn
    assert "'clear'" in fn and "'screened_no_match'" in fn
    assert "return false" in fn
    # AND an explicit zero hit count.
    assert "Number(row.total_hits || 0) === 0" in fn


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
    ):
        assert forbidden not in fn, forbidden


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

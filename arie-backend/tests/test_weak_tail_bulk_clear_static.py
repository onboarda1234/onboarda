"""Weak-tail bulk-clear (founder request) — Screening Review page.

The near-identical group block already offers "Select all undecided" / bulk-clear,
but ONLY for hits sharing an identical (category, score, matched-name) signature.
A big weak tail of below-threshold, name-VARIANT matches (e.g. "michael davis" /
"mike davis" / "michael mickey davis") never folds into a group, so clearing it
meant dozens of individual clicks. This adds an always-visible "Clear all N as
false positive" (+ matching "Undo all") to the weak-tail section, reusing the exact
per-hit disposition persistence path.
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


def test_registry_declared():
    assert "var SCREENING_WEAK_TAIL_REG = {}" in HTML


def test_weak_tail_section_renders_bulk_actions():
    fn = _fn("screeningTriageWeakTailSection")
    # honest existing line preserved
    assert 'data-screening-triage-weak-tail="true"' in fn
    assert "weak name-only matches — below triage threshold" in fn
    # new always-visible bulk actions
    assert 'data-screening-weak-tail-actions="true"' in fn
    assert "Clear all ' + pending + ' as false positive" in fn
    assert "screeningWeakTailBulkClear(" in fn
    assert "screeningWeakTailUndoAll(" in fn
    # registers exactly this subject's rendered weak hit ids
    assert "SCREENING_WEAK_TAIL_REG[weakRegId]" in fn
    assert "screeningHitId(entry.item, entry.index)" in fn
    # button click must NOT toggle the <details> open/closed
    assert "event.stopPropagation()" in fn
    assert "event.preventDefault()" in fn


def test_bulk_bar_is_inside_the_permission_and_lock_guard():
    # The bar must render ONLY when the officer can disposition AND the subject
    # is not recorded/locked — mirror the per-hit read-only lock. Assert the
    # guard clause literally wraps the bar, not merely that the tokens appear.
    fn = _fn("screeningTriageWeakTailSection")
    assert "var locked = (!row.review_required || (row.review_disposition && row.review_actionable === false));" in fn
    assert "if (canDispositionScreeningDisposition() && !locked && (pending > 0 || cleared > 0)) {" in fn
    # falsy hit ids are filtered out of the bulk set
    assert ".filter(Boolean)" in fn


def test_bulk_clear_only_touches_pending_and_persists_cleared():
    fn = _fn("screeningWeakTailBulkClear")
    assert "SCREENING_WEAK_TAIL_REG[weakRegId]" in fn
    assert "st.status === 'pending'" in fn          # never overrides a decision
    assert "st.status = 'cleared'" in fn
    assert "'cleared', null" in fn                  # false-positive, no materiality
    assert "renderScreeningReviewPanel(" in fn
    assert "if (changed.length)" in fn              # only persist a real change


def test_undo_reverts_only_cleared_never_a_true_match():
    fn = _fn("screeningWeakTailUndoAll")
    # Undo reverses ONLY the false-positive clears; a confirmed true match is
    # left intact (never reverted to pending, never has materiality wiped).
    assert "st.status === 'cleared'" in fn
    assert "st.status !== 'pending'" not in fn      # NOT the broad "any decided" revert
    assert "st.status = 'pending'" in fn
    assert "'pending', null" in fn
    assert "if (changed.length)" in fn


def test_reuses_shared_disposition_persistence_path():
    # No bespoke endpoint — reuse the same persistence helper the per-hit and
    # near-identical-group actions use.
    for name in ("screeningWeakTailBulkClear", "screeningWeakTailUndoAll"):
        fn = _fn(name)
        assert "screeningPersistHitDisposition(reg.appRef, reg.subjType, reg.subjName, changed," in fn

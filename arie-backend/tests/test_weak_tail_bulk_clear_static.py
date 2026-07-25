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
    # 'selectable' folds the permission + lock check; both the summary bulk bar
    # and the multi-select bar/checkboxes render only when selectable.
    assert "var selectable = canDispositionScreeningDisposition() && !locked;" in fn
    assert "if (selectable && (pending > 0 || cleared > 0)) {" in fn
    assert "if (selectable && pending > 0) {" in fn        # selection bar
    assert "if (!selectable) return card;" in fn           # no checkbox when not selectable
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
    for name in ("screeningWeakTailBulkClear", "screeningWeakTailUndoAll", "screeningWeakTailClearSelected"):
        fn = _fn(name)
        assert "screeningPersistHitDisposition(reg.appRef, reg.subjType, reg.subjName, changed," in fn


# --- multi-select (tick a subset, clear just those) --------------------------

def test_multi_select_registry_and_handlers_declared():
    assert "var SCREENING_WEAK_TAIL_SEL = {}" in HTML
    for name in ("screeningWeakTailToggleSel", "screeningWeakTailSelectAll", "screeningWeakTailClearSelected"):
        assert "function " + name in HTML


def test_section_renders_selection_bar_and_per_pending_checkbox():
    fn = _fn("screeningTriageWeakTailSection")
    # selection bar mirrors the near-identical group
    assert 'data-screening-weak-tail-select="true"' in fn
    assert "Select all undecided" in fn
    assert "Clear ' + selCount + ' selected as false positive" in fn
    assert "screeningWeakTailSelectAll(" in fn
    assert "screeningWeakTailClearSelected(" in fn
    # a checkbox renders per selectable hit, toggling selection
    assert 'type="checkbox"' in fn
    assert "screeningWeakTailToggleSel(" in fn
    assert "escapeJsAttr(hid)" in fn
    # only PENDING, stamped hits are selectable
    assert "!== 'pending'" in fn
    # "Clear all" is kept alongside multi-select
    assert "Clear all ' + pending + ' as false positive" in fn


def test_select_all_selects_pending_only():
    fn = _fn("screeningWeakTailSelectAll")
    # Only undecided (pending) hits are added to the selection — a decided hit
    # is never auto-selected.
    assert "status === 'pending'" in fn
    assert "sel[hid] = true" in fn


def test_clear_selected_only_clears_ticked_pending_and_resets_selection():
    fn = _fn("screeningWeakTailClearSelected")
    assert "Object.keys(sel)" in fn
    assert "st.status === 'pending'" in fn          # never overrides a decision
    assert "st.status = 'cleared'" in fn
    assert "SCREENING_WEAK_TAIL_SEL[weakRegId] = {}" in fn   # selection reset after
    assert "'cleared', null" in fn

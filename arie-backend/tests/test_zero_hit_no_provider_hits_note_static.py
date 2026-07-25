"""Pilot-audit fix — zero-hit provider result must not read as an officer clearance.

A subject the provider returned NO matches for carries the canonical status
"Clear", which can read as an officer DECISION rather than "provider found
nothing". This adds an additive honesty note on the entity/person Screening
Review cards (when the status is a provider no-match AND no officer disposition
exists yet) stating it is a provider result, not a clearance. The status
label/state is unchanged — additive only.
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


def test_note_helper_contract():
    fn = _fn("screeningNoProviderHitsNote")
    # Fires for the provider no-match / clear family only.
    assert "screened_no_match" in fn
    assert "'clear'" in fn
    # Suppressed once an officer has dispositioned the subject.
    assert "hasDisposition" in fn and "return ''" in fn
    # Honest copy: provider result, not an officer clearance.
    assert "provider result, not an officer clearance" in fn
    assert "officer review and sign-off remain separate" in fn
    assert 'data-screening-no-provider-hits-note="true"' in fn
    # Family boundary: must NOT fire for hits-present or declared-PEP review —
    # those are officer-review states, not a provider no-match.
    assert "review_required" not in fn
    assert "declared_pep_review" not in fn


def test_note_wired_into_entity_and_person_cards():
    # Rendered at the top of each card body, and gated on no officer disposition.
    assert "entityNoHitsNote + body" in HTML
    assert "personNoHitsNote + body" in HTML
    assert HTML.count("screeningNoProviderHitsNote(") >= 3  # helper def + 2 call sites
    for call in ("entityNoHitsNote", "personNoHitsNote"):
        region = HTML[HTML.index("var " + call):HTML.index("var " + call) + 240]
        assert "review_disposition" in region  # suppressed after an officer decision


def test_status_label_clear_is_left_unchanged():
    # This is an ADDITIVE note — it must NOT rename the canonical "Clear"
    # status (server-side + shared mapper stay intact; frozen state machine
    # and its guards are untouched).
    mapper = _fn("screeningBusinessStatusLabel")
    assert "return 'Clear';" in mapper

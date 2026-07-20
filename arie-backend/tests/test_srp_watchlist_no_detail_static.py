"""PR-821 flag-off validation fix — regression guard.

Staging validation for the profile-hydration flag-off path FAILed browser
acceptance: an ARF-QAFIX-007 watchlist/warning hit that carried no list name or
source detail expanded (in the modern triage hit card) with nothing said about
the missing detail. The honest F7 fallback sentence existed only in the legacy
provider-card path (evidenceReviewRationale), never in the modern triage card
(screeningTriageHitEvidenceBody). This guard pins the honest sentence into the
modern card so the detail-less watchlist hit can never again render silent.
"""

import pathlib
import re

BACKOFFICE_HTML = pathlib.Path(__file__).resolve().parents[2] / "arie-backoffice.html"

HONEST_COPY = (
    "The provider flagged a watchlist/warning match but supplied no list name "
    "or source detail. Review the provider record identifiers in Technical details."
)


def _function_region(html, start_fn, next_fn):
    start = html.index("function " + start_fn)
    end = html.index("function " + next_fn, start)
    return html[start:end]


def test_modern_triage_card_emits_watchlist_no_detail_copy():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    body = _function_region(
        html, "screeningTriageHitEvidenceBody", "screeningTriageHitTechnicalDetails"
    )
    # The honest sentence and its stable hook must live in the modern triage card.
    assert HONEST_COPY in body
    assert 'data-screening-watchlist-no-detail="true"' in body
    # It is gated on the watchlist bucket AND the absence of real detail — it must
    # never fabricate over hydrated entries or a real source.
    assert "screeningTriageBucketKeyForCategory(item.category) === 'watchlist'" in body
    assert "hasWatchlistDetail" in body
    assert "item.watchlist_entries" in body


def test_honest_copy_wording_is_shared_verbatim_with_legacy_path():
    # The two paths must say exactly the same honest thing — no drift.
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    assert html.count(HONEST_COPY) >= 2

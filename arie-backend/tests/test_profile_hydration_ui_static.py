"""Phase G — static pins on the back-office profile-hydration UI.

Guards the flag-gated, single-flight hydration call and the F7 watchlist card
render without booting a browser. Also asserts the banned-vocabulary rule
(no %, confidence, probability) inside the new render functions.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _function_region(html, name, next_name=None):
    start = html.index(f"function {name}")
    if next_name:
        end = html.index(f"function {next_name}", start)
    else:
        end = start + 4000
    return html[start:end]


@pytest.fixture(scope="module")
def html():
    return BACKOFFICE_HTML.read_text()


def test_hydration_flag_getter_reads_queue_metrics(html):
    assert "function screeningProfileHydrationEnabled" in html
    region = _function_region(html, "screeningProfileHydrationEnabled", "ensureApplicationScreeningEvidenceRows")
    assert "SCREENING_QUEUE.metrics.hydration_enabled === true" in region


def test_hydration_call_is_single_flight_and_flag_gated(html):
    region = _function_region(html, "ensureSubjectProfileHydration", "renderScreeningReviewPanel")
    # Flag gate — no call when hydration is disabled.
    assert "if (!app || !selectedSubject || !screeningProfileHydrationEnabled()) return;" in region
    # Single-flight guard mirroring ensureApplicationScreeningEvidenceRows.
    assert "SCREENING_SUBJECT_HYDRATION_FETCHES[flightKey]" in region
    assert "if (state === 'loading' || state === 'done' || state === 'error') return;" in region
    # Hits POST the documented endpoint with the top hits' alert + profile ids.
    assert "'/screening/hydrate-profiles'" in region
    assert "alert_identifiers: alertIds" in region
    assert "profile_identifiers: profileIds" in region
    # Best-effort: failure keeps current display silently.
    assert "'error'" in region


def test_hydration_only_when_top_hits_lack_attributes(html):
    region = _function_region(html, "ensureSubjectProfileHydration", "renderScreeningReviewPanel")
    assert "if (!needsHydration || !alertIds.length || !profileIds.length) return;" in region


def test_render_panel_invokes_single_flight_hydration(html):
    region = _function_region(html, "renderScreeningReviewPanel", "openScreeningReview")
    assert "ensureSubjectProfileHydration(app, selectedSubject);" in region


def test_watchlist_card_renders_list_names_and_gated_source_link(html):
    region = _function_region(html, "screeningWatchlistEntriesCard", "screeningTriageHitEvidenceBody")
    # List name and listed date render.
    assert "entry.list_name" in region
    assert "entry.listed_date" in region
    assert "'Listed '" in region
    # Source link is gated on a real http(s) url.
    assert "Source ↗" in region
    assert "rel=\"noopener\"" in region
    assert "target=\"_blank\"" in region
    assert ".toLowerCase().indexOf('http') === 0" in region
    # Absent watchlist_entries → empty string (honest F7 copy stays).
    assert "if (!entries.length) return '';" in region
    # escapeHtml applied.
    assert "escapeHtml(meta)" in region


def test_watchlist_card_wired_into_evidence_body(html):
    region = _function_region(html, "screeningTriageHitEvidenceBody", "screeningTriageHitTechnicalDetails")
    assert "screeningWatchlistEntriesCard(item)" in region


def test_watchlist_rationale_treats_entries_as_detail(html):
    region = _function_region(html, "evidenceReviewRationale", "evidencePrimaryLabel")
    assert "Array.isArray(hit.watchlist_entries) && hit.watchlist_entries.length" in region
    # The honest fallback copy is preserved for the no-detail case.
    assert "supplied no list name or source detail" in region


def test_no_banned_vocabulary_in_hydration_render_functions(html):
    banned = ["percent", "%", "confidence", "probability"]
    for fn, nxt in [
        ("screeningWatchlistEntriesCard", "screeningTriageHitEvidenceBody"),
        ("ensureSubjectProfileHydration", "renderScreeningReviewPanel"),
    ]:
        region = _function_region(html, fn, nxt)
        for token in banned:
            assert token not in region.lower(), f"banned token {token!r} in {fn}"

"""SRP-3e de-dup (founder request): the Screening Review panel drops the
redundant intro header card.

The former full-width card repeated the tab title, the mode badge, the
last-screened timestamp and the focused subject — all already shown on the
subject card below. It is removed; only the compliance-critical freshness
warning and the data-outage banner remain (standalone). These guards are
revert-sensitive and confirm nothing safety-relevant was lost.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _review_region() -> str:
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    start = html.index("function renderScreeningReviewPanel")
    end = html.index("\nfunction openScreeningReview", start)
    return html[start:end]


def test_redundant_header_card_is_gone():
    region = _review_region()
    assert '<div class="card-title">Screening Review</div>' not in region
    assert "Stored provider AML/watchlist and PEP results for this application." not in region
    assert "Focused subject: <strong>" not in region


def test_compliance_critical_banners_survive():
    region = _review_region()
    # Freshness (expired / aging) must still fire — approval-blocking signal.
    assert "Screening expired" in region
    assert "Re-screen required before approval." in region
    # Data-outage banner (items 1 & 4) must still fire.
    assert 'data-screening-evidence-outage="true"' in region
    assert "do not treat an empty or" in region


def test_mode_badge_still_shown_on_subject_card():
    # The Live/Demo mode signal must not vanish — it renders on the subject card
    # (not the retired header). Confirm the subject-card renderers still call it.
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    entity_card = html[html.index("function buildEntityScreeningReviewCard"):]
    entity_card = entity_card[: entity_card.index("\nfunction ")]
    assert "screeningModeBadge(" in entity_card

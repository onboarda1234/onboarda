"""SRP-3 Phase B — screening review triage redesign (UI static pins).

Pins the founder-approved Phase B presentation contract in
arie-backoffice.html:

* Agent 3 panel is explicitly advisory ("Advisory — decisions are made by
  officers.") with a purple accent.
* The RegMind triage caption states ordering-only semantics (never hides
  matches, never affects risk scores or approvals) and is exactly the
  approved copy.
* Weak hits collapse to ONE line ("N weak name-only matches — below triage
  threshold <server value> · ... each remains reviewable") and every hit
  inside stays reviewable/actionable.
* Honesty banners render ONLY for warranted report states (identifier
  conflict / pre-enrichment blind report / degraded sources) with the exact
  approved copy; healthy reports render no banner.
* Per-hit actions: "Clear as false positive" is the primary (navy) action,
  "Confirm true match" secondary, Escalate + Request more information live
  under a "More ▾" menu — all routing into the existing disposition flow.
* "Open in ComplyAdvantage" renders only when the stored hit carries a
  non-empty provider_case_url — URLs are never constructed client-side.
* The triage strip is fed only from the server-computed row.triage block.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _function_region(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_agent3_panel_carries_advisory_officer_decision_label():
    html = _html()
    panel = _function_region(
        html, "renderAgent3ScreeningInterpretationPanel", "generateAgent3ScreeningInterpretation"
    )
    assert "Advisory — decisions are made by officers." in panel
    # Purple advisory accent (not the old teal), applied as the left border.
    assert "border-left:4px solid #7c3aed" in panel
    assert "border-left:4px solid #0f766e" not in panel


def test_rts_caption_is_exactly_the_approved_ordering_only_copy():
    html = _html()
    assert (
        "RegMind triage ranking (rts-1.0) orders matches for review — it never hides them "
        "and never affects risk scores or approvals." in html
    )
    # The caption is rendered with the bucketed sections.
    sections = _function_region(
        html, "screeningTriageRankedHitSections", "renderScreeningReviewPanel"
    )
    assert "SCREENING_TRIAGE_RTS_CAPTION" in sections
    assert "labelled “unscored”" in sections


def test_weak_tail_is_one_collapsed_line_with_server_threshold():
    html = _html()
    tail = _function_region(
        html, "screeningTriageWeakTailSection", "screeningTriageRankedHitSections"
    )
    # ONE collapsed <details> line built from server-computed values only.
    assert "<details" in tail and "<summary" in tail
    assert "' weak name-only matches — below triage threshold '" in tail
    assert "each remains reviewable" in tail
    assert "triage.weak_count" in tail
    assert "triage.weak_threshold" in tail
    # Every weak hit renders through the same reviewable/actionable hit card.
    assert "screeningTriageHitCard(row, entry.item, entry.index" in tail

    sections = _function_region(
        html, "screeningTriageRankedHitSections", "renderScreeningReviewPanel"
    )
    # Weak classification uses the server threshold, never a client constant.
    assert "item.triage_score < weakThreshold" in sections
    assert "triage.weak_threshold" in sections


def test_honesty_banners_use_approved_copy_and_are_conditional():
    html = _html()
    banner = _function_region(
        html, "screeningReviewHonestyBanner", "screeningTriageStrip"
    )
    assert "This screening predates enriched provider data." in banner
    assert (
        "Hit names and evidence are unavailable until a controlled re-screen is run. "
        "This is a data-vintage limitation, not a provider result." in banner
    )
    assert "Re-screen blocked." in banner
    assert (
        "The screening provider already holds a record for this subject. "
        "This is not evidence of zero hits. An updated re-screen path is in progress." in banner
    )
    # Conditionality: each banner is gated on a real report/row state and the
    # healthy path renders nothing.
    assert "report.customer_identifier_conflict === true" in banner
    assert "screeningReviewReportIsBlindPreEnrichment(row)" in banner
    assert "report.degraded_sources" in banner
    assert banner.rstrip().endswith("return '';\n}")

    blind = _function_region(
        html, "screeningReviewReportIsBlindPreEnrichment", "screeningReviewHonestyBanner"
    )
    # "Blind" means no human-readable names AND no article evidence — a report
    # with real names/evidence never triggers the pre-enrichment banner.
    assert "isUuidLike(name)" in blind
    assert "!item.source_title && !item.snippet && !item.source_url" in blind


def test_per_hit_actions_use_navy_hierarchy_with_more_menu():
    html = _html()
    actions = _function_region(
        html, "screeningTriageHitActions", "screeningTriageHitToggled"
    )
    # Primary (navy) clear, secondary outline confirm.
    clear_idx = actions.index("Clear as false positive</button>")
    assert "btn btn-primary btn-sm" in actions[max(0, clear_idx - 400):clear_idx]
    confirm_idx = actions.index("Confirm true match</button>")
    assert "btn btn-outline btn-sm" in actions[max(0, confirm_idx - 400):confirm_idx]
    # Escalate + Request more information live under the "More ▾" menu.
    more_idx = actions.index("More ▾")
    assert more_idx < actions.index("Escalate</button>")
    assert more_idx < actions.index("Request more information</button>")
    assert "data-screening-hit-more-menu" in actions
    # All four route into the existing inline disposition flow (same endpoint,
    # validation, and four-eyes behavior).
    assert actions.count("screeningTriageHitAction(") == 4
    hit_action = _function_region(
        html, "screeningTriageHitAction", "screeningTriageHitActions"
    )
    assert "setInlineScreeningDispositionChoice(applicationRef, subjectType, subjectName, disposition)" in hit_action
    # Role gate preserved: clearing stays gated with an explanatory title.
    assert "Clear as False Positive requires Onboarding Officer, SCO, or Admin role." in actions


def test_provider_case_url_link_is_gated_on_the_stored_field():
    html = _html()
    tech = _function_region(
        html, "screeningTriageHitTechnicalDetails", "screeningClearanceNeedsSecondReviewer"
    )
    assert "Open in ComplyAdvantage" in tech
    # The link renders only when the stored hit carries the field — the code
    # reads provider_case_url and checks it before emitting the anchor.
    assert "String(item.provider_case_url || '').trim()" in tech
    assert tech.index("if (caseUrl)") < tech.index("Open in ComplyAdvantage ↗</a>")
    assert "escapeHtml(caseUrl)" in tech
    # Provider raw score is surfaced as a raw value, never a percentage.
    assert "Provider raw score" in tech
    assert "'%'" not in tech


def test_triage_strip_and_buckets_read_only_server_computed_triage():
    html = _html()
    strip = _function_region(html, "screeningTriageStrip", "screeningTriageHitDisplayName")
    assert "row && row.triage" in strip
    assert "if (!triage || !triage.buckets) return '';" in strip
    assert "buckets.sanctions" in strip
    assert "buckets.pep" in strip
    assert "buckets.adverse_media" in strip
    assert "triage.weak_count" in strip
    assert "triage.unscored_count" in strip
    # Bucket order is Sanctions → PEP → Adverse media → Watchlist (then other).
    meta_start = html.index("var SCREENING_TRIAGE_BUCKET_META")
    meta = html[meta_start:meta_start + 600]
    assert meta.index("'sanctions'") < meta.index("'pep'") < meta.index("'adverse_media'") < meta.index("'watchlist'")
    # Unscored hits sort last and are labelled "unscored", never called weak.
    band = _function_region(html, "screeningTriageScoreBand", "screeningTriageScoreBlock")
    assert "'unscored'" in band
    sections = _function_region(
        html, "screeningTriageRankedHitSections", "renderScreeningReviewPanel"
    )
    assert "if (sa === null) return 1;" in sections
    # Calm affirmative empty state for the sanctions section.
    assert "No sanctions or watchlist matches — every subject was screened against sanction lists." in sections

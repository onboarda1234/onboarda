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
        "RegMind triage ranking (rts-1.1) orders matches for review — it never hides them "
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


def test_lazy_evidence_fetch_mirrors_fixture_visibility():
    # E-0 hydration fix: the review page's lazy evidence fetch
    # (ensureScreeningQueueRowEvidence) must carry the queue's
    # fixture-visibility state. Without show_fixtures, fixture applications
    # are excluded from the secondary fetch, the row comes back not_found,
    # and the review page silently falls back to the legacy summary view.
    html = _html()
    fetch_fn = _function_region(
        html, "ensureScreeningQueueRowEvidence", "openScreeningReviewByRow"
    )
    assert "include_evidence=1" in fetch_fn
    assert "showTestSmokeRecordsEnabled" in fetch_fn
    assert "show_fixtures=true" in fetch_fn


def test_application_tab_hydrates_evidence_without_queue_visit():
    # E-1: the Application Review tab must fetch evidence-mode queue rows for
    # its application when the shared queue state has none — arriving via the
    # queue first must not be a precondition for the triage view. Single
    # flight per application ref, fixture visibility mirrored, and the panel
    # re-renders when the rows arrive.
    html = _html()
    fetch_fn = _function_region(
        html, "ensureApplicationScreeningEvidenceRows", "renderScreeningReviewPanel"
    )
    assert "include_evidence=1" in fetch_fn
    assert "application_ref=" in fetch_fn
    assert "showTestSmokeRecordsEnabled" in fetch_fn
    assert "show_fixtures=true" in fetch_fn
    assert "SCREENING_REVIEW_APP_EVIDENCE_FETCHES[appRef] = 'loading'" in fetch_fn
    assert "SCREENING_REVIEW_APP_EVIDENCE_FETCHES[appRef] = 'error'" in fetch_fn
    assert "renderScreeningReviewPanel(currentApp, currentScreeningReviewFocus)" in fetch_fn
    panel_start = html.index("function renderScreeningReviewPanel")
    panel_head = html[panel_start:panel_start + 1600]
    assert "ensureApplicationScreeningEvidenceRows(app.ref)" in panel_head
    assert "row.screening_evidence" in panel_head


# ---------------------------------------------------------------------------
# Phase F — polish batch pins (strip honesty, panel consolidation, ID lists)
# ---------------------------------------------------------------------------

def test_phase_f_first_strip_tile_never_excludes_what_it_names():
    """F3: the first tile counts sanctions + watchlist/warning and is labelled
    'Sanctions & watchlist' with an honest sub-caption breakdown — a watchlist
    'warning' hit must never sit behind a tile showing 0."""
    html = _html()
    strip = _function_region(html, "screeningTriageStrip", "screeningTriageHitDisplayName")
    assert "tile(sanctionsCount + watchlistCount, 'Sanctions & watchlist', sanctionsWatchlistSub" in strip
    assert "Number(buckets.sanctions || 0)" in strip
    assert "Number(buckets.watchlist || 0)" in strip
    assert "' sanctions · '" in strip
    assert "' watchlist/warning'" in strip
    assert "'Screened against sanction lists'" in strip
    # The dishonest label is retired from the strip.
    assert "Sanctions & warnings" not in strip


def test_phase_f_agent3_panel_consolidated_layout():
    """F1: advisory line → grouped narrative → ONE compact status line →
    summary (false-positive merged after) → key concerns → ONE collapsed
    audit disclosure (hit-by-hit table + evidence used + audit trace). The
    old stacked sub-section cards are retired; their content is folded, not
    deleted."""
    html = _html()
    panel = _function_region(
        html, "renderAgent3ScreeningInterpretationPanel", "generateAgent3ScreeningInterpretation"
    )
    assert panel.count("data-agent3-status-line") == 1
    assert "Hit-by-hit review, evidence &amp; audit trace" in panel
    # Retired stacked cards (their labels survive as folded section content).
    assert "False-positive assessment &amp; context" not in panel
    assert "sanctions · PEP · adverse media" not in panel
    # Folded content still renders once each in the hit path.
    assert "agent3EvidenceHtml(output.evidence_used)" in panel
    assert "agent3AuditTraceHtml(output)" in panel
    assert "False-positive assessment" in panel
    assert "Adverse media relevance" in panel
    # Order in the hit path: advisory line, narrative, status line, summary.
    hit_path = panel[panel.index("Officer decision required. Agent 3 provides an advisory interpretation only."):]
    assert hit_path.index("agent3TriageNarrativeHtml(output.triage_narrative)") \
        < hit_path.index("statusLineHtml") \
        < hit_path.index("Plain-English summary")


def test_phase_f_narrative_helper_renders_grouped_entries():
    """F1: the narrative helper renders server-grouped entries when present
    (homogeneous masses collapse to one line), falling back to priority_hits
    for older stored narratives. Counts are never recomputed client-side."""
    html = _html()
    helper = _function_region(
        html, "agent3TriageNarrativeHtml", "renderAgent3ScreeningInterpretationPanel"
    )
    assert "narrative.entries" in helper
    assert "narrative.priority_hits" in helper
    assert "hit.kind === 'group'" in helper
    assert "near-identical matches on" in helper
    assert "No single hit stands out." in helper
    assert "escapeHtml(String(hit.count))" in helper


def test_phase_f_provider_id_walls_collapsed():
    """F8: provider-reference ID lists render as count summary + native
    <details> ('Show IDs') with a scrollable monospace block and a
    copy-to-clipboard button — never comma-joined UUID walls. Audit
    completeness preserved."""
    html = _html()
    helper = _function_region(html, "screeningProviderIdListHtml", "copyScreeningProviderIds")
    assert "<details" in helper and "Show IDs" in helper
    assert "escapeHtml(idText)" in helper
    assert "data-copy-provider-ids" in helper
    assert "overflow:auto" in helper
    assert "ui-monospace" in helper
    copy_fn = _function_region(html, "copyScreeningProviderIds", "screeningQueueEvidenceReadinessPanel")
    assert "navigator.clipboard.writeText" in copy_fn
    assert "showToast" in copy_fn
    panel = _function_region(html, "screeningQueueEvidenceReadinessPanel", "providerIndicatorDetails")
    assert "screeningProviderIdListHtml('Provider case IDs', summary.provider_case_ids" in panel
    assert "screeningProviderIdListHtml('Provider alert IDs', summary.provider_alert_ids" in panel
    assert "screeningProviderIdListHtml('Provider risk IDs', summary.provider_risk_ids" in panel
    # The old comma-joined grid rows are retired.
    assert "screeningEvidenceArrayText(summary.provider_case_ids)" not in panel
    assert "screeningEvidenceArrayText(summary.provider_alert_ids)" not in panel
    assert "screeningEvidenceArrayText(summary.provider_risk_ids)" not in panel


# ---------------------------------------------------------------------------
# Phase F — F9 per-hit applicant-vs-profile reconciliation (self-lighting)
# ---------------------------------------------------------------------------

def test_f9_comparison_grid_rows_are_conditional_never_empty():
    """F9: the "Applicant vs matched profile" grid self-lights — a row renders
    ONLY when at least one side carries stored data, and an empty grid renders
    nothing at all (no shell, no placeholder dash rows)."""
    html = _html()
    grid = _function_region(
        html, "screeningTriageHitApplicantComparison", "screeningTriageHitEvidenceBody"
    )
    assert "Applicant vs matched profile" in grid
    assert "if (!applicantText && !providerText) return;" in grid
    assert "if (!rows.length) return '';" in grid
    assert "applicant.name || provider.name" in grid
    assert "provider.places_of_birth.length" in grid
    assert "provider.aka_names.length" in grid
    assert "provider.positions.length" in grid
    # One-sided data stays labelled honestly, never guessed into a verdict.
    assert "['provider only', 'draft']" in grid
    assert "'applicant only'" in grid


def test_f9_name_row_verdict_comes_from_stored_match_types():
    """F9: the name-row verdict derives ONLY from the stored provider match
    types — an exact token lights "exact", anything else stays "similar"."""
    html = _html()
    token = _function_region(
        html, "screeningTriageHitExactNameToken", "screeningTriagePepClassChip"
    )
    assert "provider_match_types" in token
    assert "'name_exact'" in token
    assert "'exact_match'" in token
    assert "'aka_exact'" in token
    grid = _function_region(
        html, "screeningTriageHitApplicantComparison", "screeningTriageHitEvidenceBody"
    )
    assert "screeningTriageHitExactNameToken(item) ? ['exact', 'approved'] : ['similar', 'draft']" in grid


def test_f9_birth_year_chip_suffix_gated_on_both_sides_present_and_equal():
    """F9: the match-quality chip lights only on a stored exact token, and its
    " + birth year" / " + country" suffixes append ONLY when the stored
    provider value exists AND equals the applicant's collected value."""
    html = _html()
    chip = _function_region(
        html, "screeningTriageMatchQualityChip", "screeningTriageHitApplicantComparison"
    )
    assert "if (!screeningTriageHitExactNameToken(item)) return '';" in chip
    assert "provider.birth_year && applicant.birth_year && provider.birth_year === applicant.birth_year" in chip
    assert "' + birth year'" in chip
    assert "provider.country && applicant.country && screeningApplicantProviderCountryMatch(applicant.country, provider.country)" in chip
    assert "' + country'" in chip
    assert "'Exact name'" in chip


def test_f9_pep_class_chip_gated_on_stored_class_token():
    """F9: "PEP class N" renders ONLY from a parsable stored pep_classes
    token (strongest wins); with no stored class the existing risk-type chip
    is the fallback — a class is never invented."""
    html = _html()
    chip = _function_region(
        html, "screeningTriagePepClassChip", "screeningApplicantSubjectFacts"
    )
    assert "pep_classes" in chip
    assert "if (strongest === null) return '';" in chip
    assert "'PEP class ' + strongest" in chip
    chips = _function_region(
        html, "screeningTriageHitChips", "screeningTriageHitExactNameToken"
    )
    assert "screeningTriagePepClassChip(item)" in chips
    assert "else if (item.category)" in chips


def test_f9_matched_against_suffix_gated_on_known_applicant_facts():
    """F9: the "Matched against" line appends "— <Role> · <Country>" from the
    application data we already hold, each part only when known, with the
    generic relationship label as the fallback."""
    html = _html()
    card = _function_region(
        html, "screeningTriageHitCard", "screeningTriageWeakTailSection"
    )
    assert "[applicantFacts.role, applicantFacts.country].filter(Boolean).join(' · ')" in card
    assert "applicantContext ? ' — ' + applicantContext : (item.relationship_to_application" in card
    facts = _function_region(
        html, "screeningApplicantSubjectFacts", "screeningProviderProfileFacts"
    )
    # Applicant facts come from the SAME party fields the Phase E surfaces
    # read (app.country, party.nat / party.jurisdiction / party.dob) and are
    # guarded to the row's application — never guessed across applications.
    assert "app.country" in facts
    assert "party.nat || party.jurisdiction" in facts
    assert "screeningComparisonBirthYear(party.dob)" in facts
    assert "String(currentApp.ref) === String(subject.application_ref)" in facts

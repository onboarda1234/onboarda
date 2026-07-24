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
    # Per-hit redesign: each action records a decision for THIS hit (hit id
    # threaded through screeningHitDispositionSet) — NOT a subject-level facade.
    # The four disposition verbs all route through the per-hit setter, and the
    # old subject-level facade (setInlineScreeningDispositionChoice) is gone.
    assert "setInlineScreeningDispositionChoice" not in actions
    assert "function screeningTriageHitAction(" not in html
    for verb in ("'cleared'", "'match'", "'escalated'", "'follow_up_required'", "'pending'"):
        assert "screeningHitDispositionSet(" in actions
        assert verb in actions
    # A confirmed true match records a per-hit materiality call.
    assert 'data-screening-hit-materiality="true"' in actions
    assert "screeningHitMaterialitySet(" in actions
    # The four materiality tiers are defined in the shared label map.
    for tier in ("Material — High", "Material — Moderate", "Non-material", "Insufficient info"):
        assert tier in html
    # Role gate preserved: clearing stays gated with an explanatory title.
    assert "requires Onboarding Officer, SCO, or Admin role." in actions


def test_near_identical_hits_collapse_into_grouped_multiselect_block():
    html = _html()
    # Near-identical runs (same category + triage score + matched name) collapse.
    assert "SCREENING_HIT_GROUP_MIN" in html
    assert "screeningTriageEntrySignature" in html
    sections = _function_region(
        html, "screeningTriageRankedHitSections", "screeningHitProfileId"
    )
    assert "screeningTriageGroupedBlock(row, meta, g)" in sections
    assert "list.length >= SCREENING_HIT_GROUP_MIN" in sections
    block = _function_region(
        html, "screeningTriageGroupedBlock", "screeningTriageRankedHitSections"
    )
    assert "near-identical" in block
    # Scored groups tag the shared triage score; unscored groups are labelled
    # "(unscored)" so they are NOT mistaken for a duplicate of the scored card.
    assert "' (all triage '" in block
    assert "' (unscored)'" in block
    # Honesty: the group is stated as the verified grouping basis (matched name +
    # category) with duplication framed as "likely" — the grouping does not prove
    # the stronger "no new facts across them" claim, so that assertion is gone.
    assert "likely duplicate or syndicated records of the same matter" in block
    assert "no new facts across them" not in block
    # Bulk clear only the not-yet-reviewed remainder; multi-select clear; and the
    # invariant that confirming any one flips the subject and bulk never overrides.
    assert "Clear the remaining " in block
    assert "screeningHitGroupBulkClear(" in block
    assert "Select all undecided" in block
    assert "screeningHitGroupClearSelected(" in block
    assert "the bulk action never overrides an individual decision" in block
    # Bulk clear only touches pending hits — a recorded true match is preserved —
    # and it persists the change to the durable per-hit store.
    bulk = _function_region(html, "screeningHitGroupBulkClear", "screeningHitGroupSelectAll")
    assert "if (st.status === 'pending') { st.status = 'cleared'; changed.push(hid); }" in bulk
    assert "screeningPersistHitDisposition(reg.appRef, reg.subjType, reg.subjName, changed, 'cleared'" in bulk


def test_resolved_subject_finalize_feeds_frozen_gate():
    html = _html()
    fin = _function_region(html, "screeningSubjectFinalizeSection", "screeningTriageHitActions")
    # PR-B / audit H2: finalize is gated on the SERVER's completeness — never
    # offered while hits are unloaded (allLoaded) or still open (pending). An
    # unloaded >cap subject gets a "load all hits" control instead.
    assert "if (!rollup.total) return ''" in fin
    assert "if (!rollup.allLoaded)" in fin
    assert "loadAllScreeningHitsForSubject(" in fin
    assert "if (rollup.pending) return ''" in fin
    # Aggregate: TRUE MATCH if any hit is a confirmed true match, else CLEAR.
    assert "var aggregate = rollup.trueCount ? 'match' : 'cleared';" in fin
    # The subject decision is recorded through the EXISTING gate flow — this is
    # what writes screening_reviews / risk / routing / four-eyes (freeze-safe).
    assert "renderInlineScreeningDispositionPanel(app, row, subjectType, subjectName)" in fin
    assert "Record the subject decision." in fin
    body = _function_region(html, "screeningSubjectWorkspaceBody", "buildEntityScreeningReviewCard")
    assert "screeningSubjectFinalizeSection(app, row, config.subjectType, config.subjectName)" in body


def test_subject_rollup_and_finalize_use_server_authoritative_totals():
    """PR-B / audit H2+H3: completeness and the denominator come from the
    server rollup (uncapped), not the browser's loaded-items count."""
    html = _html()
    rollup = _function_region(html, "screeningSubjectRollup", "screeningSubjectRollupStrip")
    # The server rollup is consulted and owns total + complete.
    assert "SCREENING_HIT_ROLLUP[screeningReviewSubjectKey(" in rollup
    assert "server && typeof server.total === 'number' ? server.total : loaded" in rollup
    assert "server ? (!!server.complete && allLoaded)" in rollup
    # POST and GET store the server rollup.
    persist = _function_region(html, "screeningPersistHitDisposition", "ensureScreeningHitDispositionsHydrated")
    assert "SCREENING_HIT_ROLLUP[screeningReviewSubjectKey(appRef, subjectType, subjectName)] = resp.rollup" in persist
    hydrate = _function_region(html, "ensureScreeningHitDispositionsHydrated", "loadAllScreeningHitsForSubject")
    assert "resp.rollups" in hydrate
    # Load-all fetches the uncapped subject items so a >cap subject is finalizable.
    loader = _function_region(html, "loadAllScreeningHitsForSubject", "screeningHitId")
    assert "subject_name=" in loader
    assert "SCREENING_HIT_ITEMS_FULL[subjectKey] = resp.items" in loader


def test_per_hit_decisions_persist_and_hydrate():
    html = _html()
    # Each per-hit setter persists to the durable store.
    setter = _function_region(html, "screeningHitDispositionSet", "screeningHitMaterialitySet")
    assert "screeningPersistHitDisposition(appRef, subjectType, subjectName, [hitId], status" in setter
    persist = _function_region(html, "screeningPersistHitDisposition", "ensureScreeningHitDispositionsHydrated")
    assert "boApiCall('POST', '/screening/hit-disposition'" in persist
    assert "hit_ids: hitIds" in persist
    # On failure the client re-hydrates from the record of truth (never drifts).
    assert "ensureScreeningHitDispositionsHydrated(appRef, true)" in persist
    hydrate = _function_region(html, "ensureScreeningHitDispositionsHydrated", "screeningHitDispositionState")
    assert "boApiCall('GET', '/screening/hit-disposition?application_id='" in hydrate
    assert "SCREENING_HIT_DISPOSITION_HYDRATED[appRef] = 'done'" in hydrate
    # The review panel triggers hydration once per application.
    panel = _function_region(html, "renderScreeningReviewPanel", "openScreeningReview")
    assert "ensureScreeningHitDispositionsHydrated(app.ref)" in panel


def test_subject_status_is_computed_from_per_hit_decisions():
    html = _html()
    rollup = _function_region(html, "screeningSubjectRollup", "screeningSubjectRollupStrip")
    # TRUE MATCH if ANY hit is a confirmed true match (cannot be overridden by
    # clearing the others); CLEAR only once every hit is resolved. Post-PR-B the
    # match/complete verdict is server-authoritative (hasMatch / complete).
    assert "if (s.status === 'match') trueCount++;" in rollup
    assert "if (hasMatch) { cls = 'match'" in rollup
    assert "else if (complete) { cls = 'clear'" in rollup
    strip = _function_region(html, "screeningSubjectRollupStrip", "screeningTriageHitActions")
    assert 'data-screening-subject-rollup="true"' in strip
    assert "Subject status: " in strip


def test_provider_case_url_link_is_gated_on_the_stored_field():
    html = _html()
    tech = _function_region(
        html, "screeningTriageHitTechnicalDetails", "screeningClearanceNeedsSecondReviewer"
    )
    assert "Open in ComplyAdvantage" in tech
    # The link renders only when the stored hit carries the field — the code
    # reads provider_case_url and checks it before emitting the anchor.
    assert "String(item.provider_case_url || '').trim()" in tech
    # PR-A (audit C2): the anchor is gated on safeUrl(caseUrl) — a stored but
    # dangerous scheme (javascript:/data:) yields no link — and the href value
    # is the sanitised URL.
    assert tech.index("if (safeUrl(caseUrl))") < tech.index("Open in ComplyAdvantage ↗</a>")
    assert "escapeHtml(safeUrl(caseUrl))" in tech
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
    # Bucket order is Sanctions → PEP → Watchlist → Adverse media (then other):
    # watchlist/warning is more material than adverse media, so it ranks higher.
    meta_start = html.index("var SCREENING_TRIAGE_BUCKET_META")
    meta = html[meta_start:meta_start + 900]
    assert meta.index("'sanctions'") < meta.index("'pep'") < meta.index("'watchlist'") < meta.index("'adverse_media'")
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
    panel_head = html[panel_start:panel_start + 2600]
    assert "ensureApplicationScreeningEvidenceRows(app.ref)" in panel_head
    # The hydration trigger must detect EVIDENCE-MODE rows (hydrated items
    # array or server triage block) — a truthy screening_evidence check
    # alone matched the stripped summary dict that include_evidence=false
    # rows carry, so arriving via the Screening Queue list suppressed
    # hydration and left every subject on the legacy pre-triage UI.
    assert "hasEvidenceModeRows" in panel_head
    assert "Array.isArray(row.screening_evidence.items)" in panel_head
    assert "row.triage && row.triage.buckets" in panel_head
    assert (
        "!queueRows.some(function(row) { return row && row.screening_evidence; })"
        not in panel_head
    )


def test_summary_queue_rows_show_loading_never_legacy_evidence():
    # A summary-mode queue row (include_evidence=false) is hydratable by
    # definition — the server marks it with evidence_detail_available and
    # strips items + triage. The readiness panel must show the loading state
    # and trigger the evidence fetch for that shape, never fall through to
    # the legacy "Provider match records (stored report)" renderer.
    html = _html()
    panel = _function_region(
        html,
        "screeningQueueEvidenceReadinessPanel",
        "providerIndicatorDetails",
    )
    loading_branch = panel.index(
        "(row.triage && row.triage.buckets) || row.evidence_detail_available"
    )
    legacy_branch = panel.index("providerResultHighlights(legacyResults")
    assert loading_branch < legacy_branch
    assert "Loading ranked screening hits" in panel


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


def test_agent3_panel_is_the_compact_approved_callout():
    """Approved per-hit mock: Agent 3 is ONE compact advisory callout —
    purple header ("Agent 3 — Screening Interpretation", scoped to the
    selected subject when the narrative references it), a single
    where-to-start paragraph, and an advisory caption. The former stacked
    layout (advisory banner, recommendation/severity badge row, summary +
    key-concerns grid, provider-count chips, hit-by-hit audit disclosure,
    collapse toggle) duplicated the status strip, the triage tiles and the
    ranked per-hit cards and is retired."""
    html = _html()
    panel = _function_region(
        html, "renderAgent3ScreeningInterpretationPanel", "generateAgent3ScreeningInterpretation"
    )
    assert "Agent 3 — Screening Interpretation" in panel
    assert "scoped to" in panel
    assert "agent3TriageNarrativeHtml(narrative" in panel
    assert panel.count("Advisory — decisions are made by officers.") == 1
    for retired in (
        "Hit-by-hit review",
        "agent3HitRowsTableHtml",
        "agent3RecommendationBadge",
        "agent3SeverityBadge",
        "agent3ProviderHitsHtml",
        "agent3HitStatusCountsHtml",
        "agent3ScreeningFieldHtml",
        "agent3EvidenceHtml",
        "agent3AuditTraceHtml",
        "Plain-English summary",
        "Key concerns",
        "Show full detail",
        "Officer decision required. Agent 3 provides an advisory interpretation only.",
        "Collapse Agent 3",
        "data-agent3-status-line",
    ):
        assert retired not in panel, f"retired Agent 3 surface leaked back: {retired!r}"
    # Honesty floors survive the slim-down: terminal-clean is one caveat line,
    # incomplete zero-hit keeps the amber not-clean notice.
    assert "absence of hits is not proof of no compliance risk" in panel
    assert "Screening is not a terminal clean result." in panel


def test_phase_f_narrative_helper_renders_grouped_entries():
    """The narrative helper renders the server-grouped entries when present
    (homogeneous masses collapse to one sentence), falling back to
    priority_hits for older stored narratives. Counts are never recomputed
    client-side, and the scoped view filters by subject without hiding
    entries when nothing matches."""
    html = _html()
    scope = _function_region(
        html, "agent3NarrativeEntriesForSubject", "agent3TriageNarrativeHtml"
    )
    assert "narrative.entries" in scope
    assert "narrative.priority_hits" in scope
    assert "screeningSubjectNamesMatch(entry.subject_name, subjectName)" in scope
    assert "scoped.length ? scoped : entries" in scope
    helper = _function_region(
        html, "agent3TriageNarrativeHtml", "renderAgent3ScreeningInterpretationPanel"
    )
    assert "agent3NarrativeEntriesForSubject" in helper
    assert "hit.kind === 'group'" in helper
    assert "near-identical " in helper
    assert "grouped as duplicates by identical name, score and reason." in helper
    assert "Priority match — review first: the " in helper
    assert "escapeHtml(String(hit.count))" in helper
    assert "data-agent3-triage-narrative" in helper


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

def test_f9r_comparison_grid_relevance_gated_suppresses_name_only():
    """F9r: the "Applicant vs matched profile" grid is relevance-gated — the
    Name row alone never earns it (a lone name row merely restates the card
    header). The grid renders ONLY when a DISAMBIGUATING (non-name) row carries
    data; a name-only hit renders NOTHING (no shell, no placeholder rows).
    Rows still self-light: each renders only when a side has stored data."""
    html = _html()
    grid = _function_region(
        html, "screeningTriageHitApplicantComparison", "screeningTriageHitEvidenceBody"
    )
    assert "Applicant vs matched profile" in grid
    # Per-row self-lighting (rowHtml returns '' when both sides empty).
    assert "if (!applicantText && !providerText) return '';" in grid
    # The Name row is built into nameRowHtml and NEVER pushed into `rows`,
    # so it cannot satisfy the relevance gate on its own.
    assert "nameRowHtml = rowHtml('Name'" in grid
    assert "rows = [];" in grid
    assert "the Name row never lands here" in grid
    # Relevance gate: suppress unless a disambiguating (non-name) row exists.
    assert "if (!rows.length) return '';" in grid
    assert "applicant.name || provider.name" in grid
    # When the grid DOES render, the name row heads it.
    assert "nameRowHtml + rows.join('')" in grid
    # One-sided data stays labelled honestly, never guessed into a verdict.
    assert "['provider only', 'draft']" in grid
    assert "'applicant only'" in grid
    # F9r2: a disambiguating row requires the PROVIDER side — an applicant-only
    # row (our own data, nothing to reconcile against) must not render or earn
    # the grid, so every non-name row guards on a provider field, never on
    # "applicant OR provider".
    assert "if (provider.jurisdiction)" in grid
    assert "if (providerDobShown)" in grid
    assert "if (provider.country)" in grid
    assert "if (applicant.country || provider.jurisdiction)" not in grid
    assert "if (applicant.birth_year || providerDobShown)" not in grid
    assert "if (applicant.country || provider.country)" not in grid


def test_f9r_person_item_renders_disambiguating_rows():
    """F9r pin (a): an individual subject shows the disambiguating person rows
    (Year of birth, Country / nationality, Place of birth, Also known as,
    Listed role) — so a person hit carrying DOB/country lights the grid."""
    html = _html()
    grid = _function_region(
        html, "screeningTriageHitApplicantComparison", "screeningTriageHitEvidenceBody"
    )
    assert "pushRow('Year of birth'" in grid
    assert "pushRow('Country / nationality'" in grid
    assert "provider.places_of_birth.length" in grid
    assert "provider.aka_names.length" in grid
    assert "provider.positions.length" in grid


def test_f9r_company_item_uses_company_rows_no_person_rows():
    """F9r pins (c)+(d): a company subject shows company rows (Jurisdiction /
    country, Registration number, Also known as) and NEVER the person-only
    rows (Year of birth / Place of birth / Listed role) — those are gated to
    the individual branch."""
    html = _html()
    grid = _function_region(
        html, "screeningTriageHitApplicantComparison", "screeningTriageHitEvidenceBody"
    )
    assert "if (kind === 'company')" in grid
    assert "pushRow('Jurisdiction / country'" in grid
    assert "pushRow('Registration number'" in grid
    assert "provider.registration_number" in grid
    # Person-only rows live ONLY under the individual `else` branch — they must
    # not appear inside the company branch. The company branch text ends at the
    # `} else {` that opens the individual branch.
    company_branch = grid[grid.index("if (kind === 'company')"):grid.index("} else {")]
    assert "pushRow('Year of birth'" not in company_branch
    assert "provider.places_of_birth.length" not in company_branch
    assert "provider.positions.length" not in company_branch


def test_f9r_entity_kind_resolver_type_aware():
    """F9r pin (d): the entity-kind resolver drives row selection — an explicit
    'entity'/'company' subject type resolves to company, any person role to
    individual, and with no stored type it infers from the provider profile
    shape (company attributes → company, person attributes → individual)."""
    html = _html()
    resolver = _function_region(
        html, "screeningComparisonEntityKind", "screeningTriageHitApplicantComparison"
    )
    assert "subjectType === 'entity' || subjectType === 'company'" in resolver
    assert "return 'company'" in resolver
    assert "if (subjectType) return 'individual'" in resolver
    assert "provider.jurisdiction || provider.registration_number" in resolver
    assert "provider.birth_year || provider.dob" in resolver


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


def test_subject_match_normalizes_name_case_and_order():
    # Subject-match fix: application party name vs screening subject_name can
    # differ in case ("Jan Marsalek"/"jan marsalek") or word order
    # ("MARSALEK, Jan"). Exact-string keying dropped the scored queue row, so
    # its triage block never reached the render and it fell to the legacy
    # unscored provider-record cards (the Jan Marsalek staging symptom).
    html = _html()
    assert "function screeningSubjectMatchKey" in html
    assert "function screeningSubjectNamesMatch" in html
    assert "function screeningSubjectTokenKey" in html
    # App-tab subject builder keys + looks up via the normalised helper.
    assert "queueMap[screeningSubjectMatchKey(row.subject_type, row.subject_name)] = row;" in html
    assert "queueMap[screeningSubjectMatchKey(person.subject_type, person.name)]" in html
    assert "queueMap[screeningSubjectMatchKey('entity', app.company)]" in html
    # The old exact-string keys are gone.
    assert "queueMap[(row.subject_type || '') + '|' + (row.subject_name || '')]" not in html
    assert "queueMap[person.subject_type + '|' + person.name]" not in html
    # Row-match (E-1 merge) + review/queue fallback are case/order-insensitive.
    assert "screeningSubjectNamesMatch(a.subject_name, b.subject_name)" in html
    assert "String(a.subject_name || '') === String(b.subject_name || '')" not in html
    assert "screeningSubjectNamesMatch(item.subject_name, subjectName)" in html

"""
Runtime checks for PR 3 inline screening disposition behavior on Application Detail.

These tests execute the real front-end helper functions with a small DOM shim so
the application-detail screening controls stay pinned without requiring a live browser.
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_between(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _runtime_js(html, config):
    region = _extract_between(
        html,
        "function screeningDispositionStatusChip(row) {",
        "async function renderScreening(options) {",
    )
    return "\n".join(
        [
            textwrap.dedent(
                f"""
                const CONFIG = {json.dumps(config)};
                var currentUser = CONFIG.currentUser || {{ role: 'co', name: 'Officer Test' }};
                var currentApp = CONFIG.currentApp || null;
                var currentScreeningReviewFocus = null;
                var SCREENING_QUEUE = CONFIG.screeningQueue || {{ rows: [] }};
                var SCREENING_REVIEW_ROWS = {{}};
                var SCREENING_EVIDENCE_DETAILS = {{}};
                var INLINE_SCREENING_DISPOSITION_STATE = CONFIG.inlineState || {{}};
                var INLINE_SCREENING_DISPOSITION_SUBMITTING = CONFIG.inlineSubmitting || {{}};
                var renderHost = {{ innerHTML: '' }};
                var boCalls = [];

                function escapeHtml(value) {{
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }}

                function classifyScreeningHits(results) {{
                  var rows = Array.isArray(results) ? results : [];
                  var facts = {{
                    total: rows.length,
                    sanctions_hits: 0,
                    pep_hits: 0,
                    adverse_media_hits: 0,
                    other_hits: 0
                  }};
                  rows.forEach(function(row) {{
                    if (!row || typeof row !== 'object') return;
                    if (row.is_sanctioned) facts.sanctions_hits += 1;
                    if (row.is_pep) facts.pep_hits += 1;
                    if (row.is_adverse_media) facts.adverse_media_hits += 1;
                  }});
                  facts.other_hits = Math.max(
                    0,
                    facts.total - facts.sanctions_hits - facts.pep_hits - facts.adverse_media_hits
                  );
                  return facts;
                }}

                function screeningProviderModeFromRecord(record) {{
                  record = record || {{}};
                  var raw = String(record.provider_mode || record.status || record.provider_status || '').trim().toLowerCase();
                  if (raw === 'not_configured') return 'not_configured';
                  if (raw === 'failed' || raw === 'error' || raw === 'unavailable') return 'failed';
                  if (raw === 'sandbox' || raw === 'sandbox_provider') return 'sandbox_provider';
                  if (raw === 'simulated' || raw === 'simulated_fallback') return 'simulated_fallback';
                  if (raw === 'live' || raw === 'live_provider' || raw === 'complete' || raw === 'completed') return 'live_provider';
                  return record.matched || (Array.isArray(record.results) && record.results.length) ? 'live_provider' : 'pending';
                }}

                function deriveScreeningTruth(record) {{
                  record = record || {{}};
                  var providerMode = screeningProviderModeFromRecord(record);
                  var results = Array.isArray(record.results) ? record.results : [];
                  var hasMatch = !!record.matched || results.length > 0;
                  var canonicalState = providerMode;
                  var terminal = false;
                  var screeningResult = hasMatch ? 'match' : 'unknown';
                  var availability = 'pending';
                  if (providerMode === 'live_provider') {{
                    canonicalState = hasMatch ? 'completed_match' : 'completed_clear';
                    terminal = true;
                    screeningResult = hasMatch ? 'match' : 'clear';
                    availability = 'available';
                  }} else if (providerMode === 'not_configured') {{
                    availability = 'not_configured';
                  }} else if (providerMode === 'failed') {{
                    availability = 'failed';
                  }} else if (providerMode === 'sandbox_provider') {{
                    availability = 'sandbox';
                  }} else if (providerMode === 'simulated_fallback') {{
                    availability = 'simulated';
                  }}
                  return {{
                    canonical_state: canonicalState,
                    provider_mode: providerMode,
                    provider_availability: availability,
                    screening_result: screeningResult,
                    terminal: terminal,
                    defensible_clear: canonicalState === 'completed_clear' && providerMode === 'live_provider',
                    legacy_status:
                      canonicalState === 'completed_clear'
                        ? 'clear'
                        : canonicalState === 'completed_match'
                          ? 'match'
                          : canonicalState === 'not_configured'
                            ? 'not_configured'
                            : canonicalState === 'failed'
                              ? 'unavailable'
                              : 'pending'
                  }};
                }}

                var document = {{
                  getElementById(id) {{
                    if (id === 'detail-screening-review') return renderHost;
                    return null;
                  }}
                }};

                function showToast() {{}}
                function renderDocumentReadinessBanner() {{ return ''; }}
                function computeDocumentReadinessSummary() {{ return {{}}; }}
                function getApplicationScreeningSummary(app) {{ return (app && app._screeningSummary) || {{}}; }}
                function renderScreeningReviewPanel() {{}}
                function getCurrentBoSessionId() {{ return 'inline-screening-test-session'; }}
                async function loadScreeningQueue() {{}}
                async function fetchApplicationDetail() {{ return CONFIG.currentApp || null; }}
                function renderAuthoritativeAppDetail() {{}}
                function switchDetailTab() {{}}
                function loadDecisionRecords() {{}}
                function loadAuditTrail() {{}}
                async function boApiCall(method, path, body) {{ boCalls.push([method, path, body]); return {{ status: 'complete' }}; }}
                """
            ),
            region,
            textwrap.dedent(
                """
                const row = CONFIG.row;
                const app = CONFIG.currentApp;
                const rationaleEmpty = screeningDispositionRationaleError('escalated', '   ');
                const clearCounter = screeningDispositionRationaleCounterText('cleared', 'Short evidence note');
                const matchCounter = screeningDispositionRationaleCounterText('match', 'Confirmed hit');
                const escalateCounter = screeningDispositionRationaleCounterText('escalated', 'Escalation ready');
                const unresolvedHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                const subjectKey = screeningReviewSubjectKey(app.ref, row.subject_type, row.subject_name);
                const resolvedRow = Object.assign({}, row, {
                  review_required: false,
                  review_disposition: 'cleared',
                  review_disposition_code: 'false_positive_cleared',
                  review_actionable: false,
                  status_key: 'reviewed_false_positive_cleared',
                  status_label: 'No Match'
                });
                const resolvedHtml = renderInlineScreeningDispositionPanel(
                  app,
                  resolvedRow,
                  row.subject_type,
                  row.subject_name
                );
                INLINE_SCREENING_DISPOSITION_STATE[subjectKey] = {
                  disposition: 'cleared',
                  dispositionCode: 'false_positive_cleared',
                  rationale: 'Too short',
                  evidenceReference: 'CA-case-1',
                  error: ''
                };
                const invalidClearHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                INLINE_SCREENING_DISPOSITION_STATE[subjectKey] = {
                  disposition: 'cleared',
                  dispositionCode: 'false_positive_cleared',
                  rationale: 'Officer reviewed the provider evidence and confirmed this is not the same subject.',
                  evidenceReference: 'CA-case-100 and passport copy pack 12',
                  error: ''
                };
                const validClearHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                INLINE_SCREENING_DISPOSITION_STATE[subjectKey] = {
                  disposition: 'match',
                  dispositionCode: 'confirmed_match',
                  rationale: 'Short',
                  evidenceReference: '',
                  error: ''
                };
                const invalidMatchHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                INLINE_SCREENING_DISPOSITION_STATE[subjectKey] = {
                  disposition: 'match',
                  dispositionCode: 'confirmed_match',
                  rationale: 'Confirmed relevant provider hit.',
                  evidenceReference: '',
                  error: ''
                };
                const validMatchHtml = renderInlineScreeningDispositionPanel(app, row, row.subject_type, row.subject_name);
                const resolvedBadgeHtml = screeningReviewBadge(resolvedRow);
                const dedupedQueueStatusHtml =
                  screeningQueueStatusBadge(resolvedRow.status_key, resolvedRow.status_label) +
                  screeningReviewBadge(resolvedRow, { suppressChip: screeningReviewDisplayLabel(resolvedRow) === resolvedRow.status_label });
                const resolvedSupportBadgeHtml = screeningQueueSignalBadge('pending', resolvedRow);
                const providerHighlightsHtml = providerResultHighlights([{
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_case_identifier: 'ca-case-uuid-123456',
                  provider_alert_identifier: 'alert-uuid-654321',
                  provider_profile_identifier: 'profile-uuid-pep',
                  provider_risk_identifier: 'risk-uuid-fedcba',
                  discovered_at: '2026-05-30T10:15:00Z',
                  summary: 'Potential PEP hit.',
                  date_of_birth: '1952-10-07',
                  nationality: 'RU',
                  match_strength: 'High',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_case_identifier: 'ca-case-uuid-123457',
                  provider_alert_identifier: 'alert-uuid-654322',
                  provider_profile_identifier: 'profile-uuid-pep',
                  provider_risk_identifier: 'risk-uuid-fedcbb',
                  discovered_at: '2026-05-30T10:16:00Z',
                  summary: 'Matched to provider PEP records.',
                  year_of_birth: '1952',
                  country: 'RU',
                  match_strength: 'Medium',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'Provider Screening Hit',
                  risk_type_labels: [],
                  provider_case_identifier: 'ca-case-uuid-223456',
                  provider_alert_identifier: 'alert-uuid-754321',
                  provider_profile_identifier: 'profile-uuid-watch',
                  provider_risk_identifier: 'risk-uuid-aedcba',
                  discovered_at: '2026-05-30T10:18:00Z',
                  summary: 'Provider screening hit.',
                  date_of_birth: '1952-10-07',
                  nationality: 'RU',
                  indicators: []
                }, {
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'Provider Screening Hit',
                  risk_type_labels: [],
                  provider_case_identifier: 'ca-case-uuid-223457',
                  provider_alert_identifier: 'alert-uuid-754322',
                  provider_profile_identifier: 'profile-uuid-watch',
                  provider_risk_identifier: 'risk-uuid-aedcbb',
                  discovered_at: '2026-05-30T10:19:00Z',
                  summary: 'Provider screening hit duplicate evidence.',
                  year_of_birth: '1952',
                  country: 'RU',
                  indicators: []
                }], {
                  application_ref: 'ARF-SCREEN-11',
                  subject_type: row.subject_type,
                  subject_name: row.subject_name,
                  provider: 'complyadvantage'
                });
                const comparisonRows = screeningComparisonRows([
                  { label: 'Name', declared: 'Vladimir Putin', provider: 'Vladimir Putin', kind: 'name' },
                  { label: 'DOB', declared: '1952-10-07', provider: '1952', kind: 'date' },
                  { label: 'Nationality', declared: 'RU', provider: 'Russia', kind: 'country' },
                  { label: 'PEP declaration', declared: 'No', provider: 'Yes', kind: 'pep' },
                  { label: 'Aliases', declared: '', provider: 'V. Putin', kind: 'aliases' },
                  { label: 'Registration number', declared: 'BRN-123', provider: '', kind: 'text' },
                  { label: 'Provider category / source', declared: '', provider: 'PEP · ComplyAdvantage', kind: 'informational' }
                ]);
                const comparisonHtml = buildScreeningComparisonPanel('person', {
                  subject_type: 'director',
                  name: 'Vladimir Putin',
                  nat: 'RU',
                  dob: '1952-10-07',
                  pep: 'No'
                }, [{
                  name: 'Vladimir Putin',
                  provider: 'complyadvantage',
                  match_category: 'PEP',
                  risk_type_labels: ['PEP'],
                  provider_profile_identifier: 'profile-uuid-pep',
                  date_of_birth: '1952',
                  nationality: 'Russia',
                  aliases: ['V. Putin'],
                  indicators: []
                }], {
                  declaredPep: 'No',
                  providerPep: 'Yes'
                }, {
                  application_ref: 'ARF-SCREEN-11',
                  subject_type: 'director',
                  subject_name: 'Vladimir Putin',
                  provider: 'complyadvantage'
                });
                const triageApp = CONFIG.triageApp || {
                  id: 12,
                  ref: 'ARF-TRIAGE-12',
                  company: 'Triage Holdings Ltd',
                  directors: [{
                    name: 'Jane Director',
                    nat: 'RU',
                    dob: '1970-01-01',
                    pep: 'No',
                    first_name: 'Jane',
                    last_name: 'Director',
                    aliases: ['J. Director']
                  }],
                  ubos: [{
                    name: 'John Harbor',
                    nat: 'MU',
                    dob: '1980-02-02',
                    pct: 40,
                    pep: 'No',
                    first_name: 'John',
                    last_name: 'Harbor',
                    aliases: []
                  }],
                  screeningReviews: [{
                    subject_type: 'entity',
                    subject_name: 'Triage Holdings Ltd',
                    review_required: false,
                    review_actionable: false,
                    review_disposition: 'cleared',
                    review_disposition_code: 'false_positive_cleared',
                    status_key: 'reviewed_false_positive_cleared',
                    status_label: 'No Match',
                    reviewed_by: 'Aisha Sudally',
                    reviewed_at: '2026-05-31T10:00:00Z'
                  }],
                  _screeningSummary: {
                    screening_mode: 'live',
                    screened_at: '2026-05-31T09:00:00Z',
                    screening_run_recorded: true,
                    company_screening: {
                      found: true,
                      source: 'complyadvantage',
                      api_status: 'live',
                      matched: false,
                      results: []
                    },
                    report: {
                      director_screenings: [{
                        person_name: 'Jane Director',
                        declared_pep: 'No',
                        screening: {
                          source: 'complyadvantage',
                          api_status: 'live',
                          matched: true,
                          results: [{
                            name: 'Jane Director',
                            provider: 'complyadvantage',
                            match_category: 'PEP',
                            is_pep: true,
                            date_of_birth: '1970-01-01',
                            nationality: 'Russia',
                            risk_type_labels: ['PEP'],
                            indicators: []
                          }, {
                            name: 'Jane Director',
                            provider: 'complyadvantage',
                            match_category: 'Provider Screening Hit',
                            risk_type_labels: [],
                            indicators: []
                          }]
                        }
                      }],
                      ubo_screenings: [{
                        person_name: 'John Harbor',
                        declared_pep: 'No',
                        screening: {
                          source: 'complyadvantage',
                          api_status: 'live',
                          matched: true,
                          results: [{
                            name: 'John Harbor',
                            provider: 'complyadvantage',
                            match_category: 'Sanctions',
                            is_sanctioned: true,
                            country: 'Mauritius',
                            indicators: []
                          }]
                        }
                      }]
                    }
                  }
                };
                const triageQueueRows = [{
                  application_ref: 'ARF-TRIAGE-12',
                  subject_type: 'director',
                  subject_name: 'Jane Director',
                  status_key: 'review_required',
                  status_label: 'Review Required',
                  review_required: true,
                  review_actionable: true,
                  provider_evidence: [{
                    name: 'Jane Director',
                    provider: 'complyadvantage',
                    match_category: 'PEP',
                    is_pep: true,
                    date_of_birth: '1970-01-01',
                    nationality: 'Russia',
                    indicators: []
                  }]
                }, {
                  application_ref: 'ARF-TRIAGE-12',
                  subject_type: 'ubo',
                  subject_name: 'John Harbor',
                  status_key: 'review_follow_up_required',
                  status_label: 'Follow-Up Required',
                  review_required: false,
                  review_actionable: false,
                  review_disposition: 'follow_up_required',
                  review_disposition_code: 'needs_more_information',
                  provider_evidence: [{
                    name: 'John Harbor',
                    provider: 'complyadvantage',
                    match_category: 'Sanctions',
                    is_sanctioned: true,
                    indicators: []
                  }]
                }];
                const triageQueueMap = {};
                triageQueueRows.forEach(function(row) {
                  triageQueueMap[(row.subject_type || '') + '|' + (row.subject_name || '')] = row;
                });
                SCREENING_QUEUE = { rows: triageQueueRows.slice() };
                const triageReviewMap = {};
                (triageApp.screeningReviews || []).forEach(function(review) {
                  triageReviewMap[(review.subject_type || '') + '|' + (review.subject_name || '')] = review;
                });
                const triageSubjects = buildScreeningTriageSubjects(
                  triageApp,
                  getApplicationScreeningSummary(triageApp),
                  triageQueueMap,
                  triageReviewMap
                );
                let capturedFocus = null;
                const originalRender = renderScreeningReviewPanel;
                currentApp = triageApp;
                renderScreeningReviewPanel = function(appArg, focusArg) {
                  capturedFocus = {
                    application_ref: appArg && appArg.ref,
                    subject_type: focusArg && focusArg.subject_type,
                    subject_name: focusArg && focusArg.subject_name
                  };
                };
                setScreeningReviewFocus('director', 'Jane Director');
                const boCallsAfterFocus = boCalls.length;
                renderScreeningReviewPanel = originalRender;
                currentScreeningReviewFocus = { subject_type: 'director', subject_name: 'Jane Director' };
                renderScreeningReviewPanel(triageApp, currentScreeningReviewFocus);
                const triageCockpitHtml = renderHost.innerHTML;
                currentScreeningReviewFocus = { subject_type: 'entity', subject_name: 'Triage Holdings Ltd' };
                renderScreeningReviewPanel(triageApp, currentScreeningReviewFocus);
                const resolvedCockpitHtml = renderHost.innerHTML;
                SCREENING_QUEUE = { rows: [] };
                currentScreeningReviewFocus = { subject_type: 'director', subject_name: 'Jane Director' };
                renderScreeningReviewPanel(triageApp, currentScreeningReviewFocus);
                const directDetailWithoutQueueHtml = renderHost.innerHTML;
                const directDetailWithoutQueueSubjects = buildScreeningTriageSubjects(
                  triageApp,
                  getApplicationScreeningSummary(triageApp),
                  {},
                  {}
                ).map(function(subject) {
                  return {
                    name: subject.subject_name,
                    type: subject.subject_type,
                    status: subject.display_status_label,
                    reviewRequired: subject.review_required,
                    reviewActionable: subject.review_actionable
                  };
                });
                const inlineRefreshElements = {};
                document = {
                  getElementById(id) {
                    return inlineRefreshElements[id] || null;
                  }
                };
                currentApp = app;
                SCREENING_QUEUE = { rows: [row] };
                INLINE_SCREENING_DISPOSITION_STATE[subjectKey] = {
                  disposition: 'cleared',
                  dispositionCode: 'false_positive_cleared',
                  rationale: '',
                  evidenceReference: '',
                  evidenceFile: null,
                  error: ''
                };
                const subjectDomKey = screeningReviewSubjectDomKey(app.ref, row.subject_type, row.subject_name);
                inlineRefreshElements['screening-inline-save-' + subjectDomKey] = { disabled: true };
                inlineRefreshElements['screening-inline-validation-' + subjectDomKey] = {
                  textContent: 'Please enter a rationale before saving this screening disposition.',
                  style: { display: '' }
                };
                inlineRefreshElements['screening-inline-error-' + subjectDomKey] = {
                  textContent: 'Please enter a rationale before saving this screening disposition.',
                  style: { display: '' }
                };
                inlineRefreshElements['screening-inline-rationale-counter-' + subjectDomKey] = { textContent: '' };
                updateInlineScreeningDispositionField(
                  app.ref,
                  row.subject_type,
                  row.subject_name,
                  'rationale',
                  'Officer reviewed the provider evidence and confirmed this is not the same subject.'
                );
                const inlineRefreshResult = {
                  saveDisabled: inlineRefreshElements['screening-inline-save-' + subjectDomKey].disabled,
                  validationText: inlineRefreshElements['screening-inline-validation-' + subjectDomKey].textContent,
                  validationDisplay: inlineRefreshElements['screening-inline-validation-' + subjectDomKey].style.display,
                  errorText: inlineRefreshElements['screening-inline-error-' + subjectDomKey].textContent,
                  errorDisplay: inlineRefreshElements['screening-inline-error-' + subjectDomKey].style.display,
                  counterText: inlineRefreshElements['screening-inline-rationale-counter-' + subjectDomKey].textContent
                };

                const fallbackSubmitKey = screeningReviewSubjectKey(triageApp.ref, 'director', 'Jane Director');
                currentApp = triageApp;
                currentScreeningReviewFocus = { subject_type: 'director', subject_name: 'Jane Director' };
                SCREENING_QUEUE = { rows: [] };
                INLINE_SCREENING_DISPOSITION_STATE[fallbackSubmitKey] = {
                  disposition: 'cleared',
                  dispositionCode: 'false_positive_cleared',
                  rationale: 'Officer reviewed the provider evidence and confirmed this is not the same subject.',
                  evidenceReference: '',
                  evidenceFile: null,
                  error: ''
                };
                boCalls = [];
                submitInlineScreeningDisposition(
                  triageApp.ref,
                  'director',
                  'Jane Director'
                ).then(function() {
                  var fallbackSubmitResult = {
                    calls: boCalls.slice(),
                    state: INLINE_SCREENING_DISPOSITION_STATE[fallbackSubmitKey]
                  };
                  console.log(JSON.stringify({
                  rationaleEmpty,
                  clearCounter,
                  matchCounter,
                  escalateCounter,
                  unresolvedHtml,
                  resolvedHtml,
                  resolvedBadgeHtml,
                  dedupedQueueStatusHtml,
                  providerHighlightsHtml,
                  comparisonRows,
                  comparisonHtml,
                  triageSubjectOrder: triageSubjects.map(function(subject) {
                    return subject.subject_name + '|' + subject.display_status_label;
                  }),
                  triageSubjectSummaries: triageSubjects.map(function(subject) {
                    return {
                      name: subject.subject_name,
                      type: subject.subject_type,
                      status: subject.display_status_label,
                      hits: subject.hit_count,
                      categories: subject.category_labels,
                      hitSummary: screeningTriageHitSummary(subject)
                    };
                  }),
                  invalidClearHtml,
                  validClearHtml,
                  invalidMatchHtml,
                  validMatchHtml,
                  resolvedSupportBadgeHtml,
                  declaredPepZeroHitSummary: screeningTriageHitSummary({ declared_pep: true, hit_count: 0 }),
                  capturedFocus,
                  boCallsAfterFocus,
                  triageCockpitHtml,
                  resolvedCockpitHtml,
                  directDetailWithoutQueueHtml,
                  directDetailWithoutQueueSubjects,
                    inlineRefreshResult,
                    fallbackSubmitResult
                  }));
                }).catch(function(err) {
                  console.error(err);
                  process.exit(1);
                });
                """
            ),
        ]
    )


def _queue_runtime_js(html):
    region = _extract_between(
        html,
        "function screeningBadge(status) {",
        "var EDD_CASES = [];",
    )
    return "\n".join(
        [
            textwrap.dedent(
                """
                var SCREENING_QUEUE = { metrics:null, rows:[], generated_at:null, load_error:null };
                var SCREENING_QUEUE_DIRTY = false;
                var SCREENING_QUEUE_FILTERS = { search:'', status:'', type:'', provider:'', pep:'', application_ref:'' };
                var SCREENING_QUEUE_SEARCH_TIMER = null;
                var SCREENING_QUEUE_ACTIVE_REQUEST_ID = 0;
                var SCREENING_REVIEW_ROWS = {};
                var currentUser = { role: 'co', name: 'Officer Test' };
                var BO_AUTH_TOKEN = 'token';
                var calledPaths = [];
                function escapeHtml(value) {
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }
                function showToast() {}
                function openScreeningReviewByRow() {}
                function openScreeningDispositionModalByRow() {}
                function canClearScreeningDisposition() { return true; }
                function getCurrentBoSessionId() { return 'inline-screening-test-session'; }
                var tbody = {
                  _innerHTML: '',
                  rows: [],
                  set innerHTML(value) { this._innerHTML = value; this.rows = []; },
                  get innerHTML() { return this._innerHTML; },
                  appendChild(node) { this.rows.push(node); }
                };
                var stat = { textContent: '' };
                var document = {
                  getElementById(id) {
                    if (id === 'screening-body') return tbody;
                    if (id === 'screening-stat-awaiting' || id === 'screening-stat-screened' || id === 'screening-stat-hits') return stat;
                    return null;
                  },
                  createElement() { return { innerHTML: '' }; }
                };
                async function boApiCall(method, path) {
                  calledPaths.push(path);
                  return {
                    metrics: {
                      applications_awaiting_screening: 1,
                      applications_screened: 2,
                      applications_requiring_review: 0
                    },
                    rows: [{
                      subject_name: 'Jane Director',
                      subject_type: 'director',
                      company_name: 'Inline Screening Ltd',
                      watchlist_status: 'pending',
                      pep_declared_status: 'not_declared',
                      pep_screening_status: 'pending',
                      status_key: 'reviewed_false_positive_cleared',
                      status_label: 'No Match',
                      review_required: false,
                      review_actionable: false,
                      review_disposition: 'cleared',
                      review_disposition_code: 'false_positive_cleared'
                    }]
                  };
                }
                """
            ),
            region,
            textwrap.dedent(
                """
                (async () => {
                  markScreeningQueueDirty();
                  await renderScreening({ force: SCREENING_QUEUE_DIRTY });
                  const firstRenderedLabel = SCREENING_QUEUE.rows[0].status_label;
                  const firstRenderedHtml = tbody.rows[0] ? tbody.rows[0].innerHTML : '';
                  const firstCalledPaths = calledPaths.slice();
                  const deferred = [];
                  calledPaths = [];
                  boApiCall = function(method, path) {
                    calledPaths.push(path);
                    return new Promise(function(resolve) {
                      deferred.push({ path: path, resolve: resolve });
                    });
                  };
                  const slowRequest = loadScreeningQueue({ force: true, offset: 0 });
                  const fastRequest = loadScreeningQueue({ force: true, offset: 50 });
                  deferred[1].resolve({
                    metrics: { applications_awaiting_screening: 0, applications_screened: 1, applications_requiring_review: 1 },
                    rows: [{ subject_name: 'Newest Row', subject_type: 'director', company_name: 'Fast Co', status_key: 'review_required', status_label: 'Newest Result' }],
                    pagination: { limit: 50, offset: 50, returned: 1, total_rows: 51, has_next: false, has_prev: true }
                  });
                  await fastRequest;
                  deferred[0].resolve({
                    metrics: { applications_awaiting_screening: 0, applications_screened: 1, applications_requiring_review: 1 },
                    rows: [{ subject_name: 'Stale Row', subject_type: 'director', company_name: 'Slow Co', status_key: 'review_required', status_label: 'Stale Result' }],
                    pagination: { limit: 50, offset: 0, returned: 1, total_rows: 51, has_next: true, has_prev: false }
                  });
                  await slowRequest;
                  console.log(JSON.stringify({
                  dirtyAfterRender: SCREENING_QUEUE_DIRTY,
                  calledPaths: firstCalledPaths,
                  renderedRows: tbody.rows.length,
                  queueRowLabel: firstRenderedLabel,
                  renderedHtml: firstRenderedHtml,
                  staleGuardPaths: calledPaths,
                  staleGuardQueueRowLabel: SCREENING_QUEUE.rows[0].status_label,
                  staleGuardOffset: SCREENING_QUEUE.pagination.offset
                }));
                })().catch((err) => {
                  console.error(err);
                  process.exit(1);
                });
                """
            ),
        ]
    )


def _show_view_runtime_js(html):
    region = _extract_between(
        html,
        "function showView(name, navItem) {",
        "// ═══════════════════════════════════════════════════════════\n// RENDER FUNCTIONS",
    )
    return "\n".join(
        [
            textwrap.dedent(
                """
                var currentUser = { role: 'co', name: 'Officer Test' };
                var SCREENING_QUEUE = { rows: [] };
                var SCREENING_QUEUE_DIRTY = false;
                var renderScreeningCalls = [];
                function showToast() {}
                function renderApplications() {}
                function renderUsers() {}
                function renderAudit() {}
                function renderRolesPermissions() {}
                function renderScreening(options) { renderScreeningCalls.push(options || {}); }
                function renderMonitoring() {}
                function renderLifecycle() {}
                function renderEDD() {}
                function renderChangeMgmt() {}
                function renderCases() {}
                function renderKPIDashboard() {}
                function renderRegIntel() {}
                function renderRiskModel() {}
                function renderAIChecks() {}
                function renderAgentsPipeline() {}
                function renderResources() {}
                function refreshSupervisorDashboard() {}
                function refreshSupervisorAudit() {}
                function renderAgentHealth() {}
                function renderReportsPage() {}
                function loadEnhancedRequirementRules() {}
                function checkAPIStatus() {}
                function renderSettings() {}
                function toggleMobileMenu() {}
                var topbar = { textContent: '' };
                var screeningView = {
                  classList: {
                    added: [],
                    removed: [],
                    add(v) { this.added.push(v); },
                    remove(v) { this.removed.push(v); }
                  }
                };
                var dashboardView = {
                  classList: {
                    add() {},
                    remove() {}
                  }
                };
                var sidebar = {
                  classList: {
                    contains() { return false; }
                  }
                };
                var navScreening = {
                  classList: {
                    add() {},
                    remove() {}
                  }
                };
                var navItems = [navScreening];
                var document = {
                  querySelectorAll(selector) {
                    if (selector === '.view') return [dashboardView, screeningView];
                    if (selector === '.snav-item') return navItems;
                    return [];
                  },
                  querySelector(selector) {
                    if (selector === '.sidebar') return sidebar;
                    if (selector === '.snav-item[data-view=\"screening-queue\"]') return navScreening;
                    if (selector === '.snav-item[data-view=\"screening\"]') return navScreening;
                    return null;
                  },
                  getElementById(id) {
                    if (id === 'view-screening') return screeningView;
                    if (id === 'topbar-title') return topbar;
                    return null;
                  }
                };
                """
            ),
            region,
            textwrap.dedent(
                """
                showView('screening-queue');
                console.log(JSON.stringify({
                  renderScreeningCalls,
                  title: topbar.textContent
                }));
                """
            ),
        ]
    )


def _activity_log_runtime_js(html):
    region = _extract_between(
        html,
        "var DETAIL_AUDIT_FILTERS =",
        "// ═══════════════════════════════════════════════════════════\n// NOTES",
    )
    return "\n".join(
        [
            textwrap.dedent(
                """
                var currentApp = { id: 11 };
                var BO_AUTH_TOKEN = 'token';
                var container = { innerHTML: '' };
                var window = { _currentDetailApp: null };
                function renderCaseCommandCentre() {}
                function escapeHtml(value) {
                  return String(value == null ? '' : value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
                }
                function firstMeaningfulDetailValue() {
                  for (let i = 0; i < arguments.length; i++) {
                    const value = arguments[i];
                    if (value == null) continue;
                    if (Array.isArray(value) && value.length) return value;
                    if (typeof value === 'string' && value.trim() === '') continue;
                    if (value !== '') return value;
                  }
                  return '';
                }
                function formatNestedObject(obj) {
                  const parts = [];
                  Object.keys(obj || {}).forEach((key) => {
                    const val = obj[key];
                    if (val == null || val === '') return;
                    if (typeof val === 'object') parts.push(key.replace(/_/g, ' ') + ': ' + JSON.stringify(val));
                    else parts.push(key.replace(/_/g, ' ') + ': ' + val);
                  });
                  return parts.length ? parts.join(' | ') : '—';
                }
                async function boApiCall() {
                  return {
                    entries: [
                      {
                        action: 'Screening Review',
                        detail: JSON.stringify({
                          subject_name: 'John Harbor',
                          subject_type: 'ubo',
                          disposition: 'cleared',
                          disposition_code: 'false_positive_cleared',
                          rationale: 'Name and date-of-birth mismatch; provider profile is not the declared UBO.',
                          evidence_reference: 'CA-case-019e1b0f-second-review',
                          source_surface: 'application_detail_screening_tab',
                          four_eyes_status: 'second_review_complete'
                        }),
                        user_name: 'Aisha Sudally',
                        user_role: 'co',
                        timestamp: '2026-05-31T14:22:00Z'
                      },
                      {
                        action: 'Risk Recomputed',
                        detail: 'Reason: officer correction. Score: 48→72, Level: MEDIUM→HIGH',
                        before_state: JSON.stringify({ risk_score: 48, risk_level: 'MEDIUM' }),
                        after_state: JSON.stringify({ risk_score: 72, risk_level: 'HIGH' }),
                        user_name: 'Risk Engine',
                        user_role: 'system',
                        timestamp: '2026-05-31T14:20:00Z'
                      },
                      {
                        action: 'edd_routing.evaluated',
                        detail: JSON.stringify({ route: 'edd', triggers: ['risk_score'], policy_version: 'v1' }),
                        user_name: 'Routing Policy',
                        user_role: 'system',
                        timestamp: '2026-05-31T14:18:00Z'
                      },
                      {
                        action: 'Unexpected Vendor Event',
                        detail: JSON.stringify({ provider_id: 'raw-provider-123', unsafe: '<script>alert(1)</script>' }),
                        user_name: 'System',
                        user_role: 'system',
                        timestamp: '2026-05-31T14:15:00Z'
                      },
                      {
                        action: 'Status Change',
                        detail: 'Application moved to review.',
                        user_name: 'Ops User',
                        user_role: 'co',
                        timestamp: '2026-05-31T14:10:00Z'
                      }
                    ]
                  };
                }
                var document = {
                  getElementById(id) {
                    if (id === 'detail-activity') return container;
                    return null;
                  }
                };
                """
            ),
            region,
            textwrap.dedent(
                """
                loadActivityLog(currentApp).then(function() {
                  const allHtml = container.innerHTML;
                  const firstCardVisible = allHtml.split('<details')[0];
                  setAuditTrailFilter('Risk');
                  const riskHtml = container.innerHTML;
                  setAuditTrailFilter('System');
                  const systemHtml = container.innerHTML;
                  console.log(JSON.stringify({ html: allHtml, firstCardVisible, riskHtml, systemHtml }));
                }).catch(function(err) {
                  console.error(err);
                  process.exit(1);
                });
                """
            ),
        ]
    )


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for runtime tests"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = subprocess.run(
            ["node", script_path],
            cwd=os.path.dirname(BACKOFFICE_PATH),
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _app():
    return {
        "id": 11,
        "ref": "ARF-SCREEN-11",
        "company": "Inline Screening Ltd",
        "brn": "BRN-123",
        "country": "Mauritius",
        "entityType": "SME",
        "screeningReviews": [],
    }


def _row(**overrides):
    base = {
        "application_id": 11,
        "application_ref": "ARF-SCREEN-11",
        "company_name": "Inline Screening Ltd",
        "subject_type": "director",
        "subject_name": "Jane Director",
        "review_required": True,
        "review_actionable": True,
        "review_four_eyes_status": "not_required",
    }
    base.update(overrides)
    return base


class TestInlineScreeningRuntime:
    def test_inline_screening_controls_render_for_unresolved_hit(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "No Match" in result["unresolvedHtml"]
        assert "Match" in result["unresolvedHtml"]
        assert "Escalate" in result["unresolvedHtml"]
        assert "Follow-Up Required" not in result["unresolvedHtml"]
        assert "Save disposition" in result["unresolvedHtml"]
        assert "required for escalation" in result["unresolvedHtml"]
        assert "Upload supporting evidence (optional)" in result["unresolvedHtml"]

    def test_rationale_is_mandatory_and_permission_message_is_present(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "viewer", "name": "Viewer User"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert result["rationaleEmpty"] == "Please enter a rationale before saving this screening disposition."
        assert "You do not have permission to disposition screening results." in result["unresolvedHtml"]
        assert "requirement met" in result["clearCounter"]
        assert "13 / 12 characters" in result["matchCounter"]
        assert "16 / 12 characters" in result["escalateCounter"]

    def test_submitting_state_disables_inline_save_button(self):
        html = _read_backoffice()
        key = "ARF-SCREEN-11|director|Jane Director"
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                    "inlineState": {
                        key: {
                            "disposition": "cleared",
                            "dispositionCode": "false_positive_cleared",
                            "rationale": "Officer reviewed the provider evidence and confirmed a different identity.",
                            "evidenceReference": "Provider case CA-100 and passport copy.",
                            "error": "",
                        }
                    },
                    "inlineSubmitting": {key: True},
                },
            )
        )
        assert "Saving…" in result["unresolvedHtml"]
        assert "disabled" in result["unresolvedHtml"]

    def test_inline_save_button_only_enables_when_rationale_is_valid(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "Save disposition" in result["unresolvedHtml"]
        assert "disabled" in result["unresolvedHtml"]
        assert "Select a screening action to enable save." in result["unresolvedHtml"]
        assert "disabled" in result["invalidClearHtml"]
        assert "Please enter a rationale" not in result["validClearHtml"]
        assert "disabled" not in result["validClearHtml"]
        assert "disabled" in result["invalidMatchHtml"]
        assert "disabled" not in result["validMatchHtml"]

    def test_inline_rationale_input_refreshes_save_button_validation_state(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        refresh = result["inlineRefreshResult"]
        assert refresh["saveDisabled"] is False
        assert refresh["validationDisplay"] == "none"
        assert refresh["validationText"] == ""
        assert refresh["errorDisplay"] == "none"
        assert refresh["errorText"] == ""
        assert "requirement met" in refresh["counterText"]

    def test_resolved_hit_is_rendered_read_only(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "read-only" in result["resolvedHtml"]
        assert "View audit trail" in result["resolvedHtml"]
        assert "Save disposition" not in result["resolvedHtml"]
        assert "No Match" in result["resolvedBadgeHtml"]
        assert "Review Required" not in result["resolvedBadgeHtml"]
        assert result["dedupedQueueStatusHtml"].count("No Match") == 1
        assert "Pending" not in result["resolvedSupportBadgeHtml"]
        assert "No Match" in result["resolvedSupportBadgeHtml"]

    def test_screening_queue_dirty_flag_forces_refetch(self):
        html = _read_backoffice()
        result = _run_node(_queue_runtime_js(html))
        assert result["dirtyAfterRender"] is False
        assert result["renderedRows"] == 1
        assert result["queueRowLabel"] == "No Match"
        assert result["calledPaths"]
        queue_paths = [
            path for path in result["calledPaths"] if path.startswith("/screening/queue?")
        ]
        assert queue_paths
        assert "refresh=" in queue_paths[0]
        assert "limit=50" in queue_paths[0]
        assert "offset=0" in queue_paths[0]
        assert "Pending" not in result["renderedHtml"]
        assert "Loading screening queue" not in result["renderedHtml"]
        assert "No Match" in result["renderedHtml"]
        assert len(result["staleGuardPaths"]) == 2
        assert result["staleGuardQueueRowLabel"] == "Newest Result"
        assert result["staleGuardOffset"] == 50

    def test_screening_queue_sidebar_alias_routes_to_screening_renderer(self):
        html = _read_backoffice()
        result = _run_node(_show_view_runtime_js(html))
        assert result["title"] == "Screening Queue"
        assert result["renderScreeningCalls"] == [{"force": True}]

    def test_provider_identifiers_are_collapsed_under_technical_details(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert "Evidence groups" in result["providerHighlightsHtml"]
        assert "PEP · 2 evidence records" in result["providerHighlightsHtml"]
        assert "Provider Screening Hit · 2 evidence records" in result["providerHighlightsHtml"]
        assert result["providerHighlightsHtml"].count("Vladimir Putin") >= 4
        assert "Show evidence" in result["providerHighlightsHtml"]
        assert "Technical provider details" in result["providerHighlightsHtml"]
        assert "Provider case ID" in result["providerHighlightsHtml"]
        assert "<details" in result["providerHighlightsHtml"]
        assert "Declared vs Provider Match" in result["comparisonHtml"]
        assert "Comparison shown against highest-risk provider match." in result["comparisonHtml"]
        assert "Match" in result["comparisonRows"]
        assert "Likely Match" in result["comparisonRows"]
        assert "Conflict" in result["comparisonRows"]
        assert "Missing Declared Data" in result["comparisonRows"]
        assert "Missing Provider Data" in result["comparisonRows"]
        assert "Not Comparable" in result["comparisonRows"]

    def test_screening_triage_cockpit_orders_subjects_and_focuses_without_mutation(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        assert result["triageSubjectOrder"] == [
            "Jane Director|Review Required",
            "John Harbor|Follow-Up Required",
            "Triage Holdings Ltd|No Match",
        ]
        assert result["capturedFocus"] == {
            "application_ref": "ARF-TRIAGE-12",
            "subject_type": "director",
            "subject_name": "Jane Director",
        }
        assert result["boCallsAfterFocus"] == 0
        assert "Screening Subjects" in result["triageCockpitHtml"]
        assert "Select one subject to review comparison, evidence, and disposition state." in result["triageCockpitHtml"]
        assert "Jane Director" in result["triageCockpitHtml"]
        assert "John Harbor" in result["triageCockpitHtml"]
        assert "Triage Holdings Ltd" in result["triageCockpitHtml"]
        assert "DIR" in result["triageCockpitHtml"]
        assert "UBO" in result["triageCockpitHtml"]
        assert "ENT" in result["triageCockpitHtml"]
        assert "Declared vs Provider Match" in result["triageCockpitHtml"]
        assert "Evidence groups" in result["triageCockpitHtml"]
        assert "Save disposition" in result["triageCockpitHtml"]
        assert "Triage Holdings Ltd" in result["resolvedCockpitHtml"]
        assert "read-only" in result["resolvedCockpitHtml"]
        assert "Save disposition" not in result["resolvedCockpitHtml"]
        assert result["declaredPepZeroHitSummary"] == "Declared PEP · No provider matches"

    def test_application_detail_screening_review_does_not_depend_on_queue_cache(self):
        html = _read_backoffice()
        result = _run_node(
            _runtime_js(
                html,
                {
                    "currentUser": {"role": "co", "name": "Officer Test"},
                    "currentApp": _app(),
                    "row": _row(),
                },
            )
        )
        director_subject = next(
            subject for subject in result["directDetailWithoutQueueSubjects"]
            if subject["type"] == "director" and subject["name"] == "Jane Director"
        )
        assert director_subject["status"] == "Review Required"
        assert director_subject["reviewRequired"] is True
        assert director_subject["reviewActionable"] is True
        assert "Focused subject: <strong>Jane Director</strong> (director)" in result["directDetailWithoutQueueHtml"]
        assert "No Match" in result["directDetailWithoutQueueHtml"]
        assert "Match" in result["directDetailWithoutQueueHtml"]
        assert "Escalate" in result["directDetailWithoutQueueHtml"]
        assert "Save disposition" in result["directDetailWithoutQueueHtml"]
        assert "Follow-Up Required</button>" not in result["directDetailWithoutQueueHtml"]
        fallback_calls = result["fallbackSubmitResult"]["calls"]
        review_call = next(call for call in fallback_calls if call[0] == "POST" and call[1] == "/screening/review")
        assert review_call[2]["application_id"] == "ARF-TRIAGE-12"
        assert review_call[2]["subject_type"] == "director"
        assert review_call[2]["subject_name"] == "Jane Director"
        assert review_call[2]["disposition"] == "cleared"
        assert review_call[2]["disposition_code"] == "false_positive_cleared"
        assert review_call[2]["source_surface"] == "application_detail_screening_tab"
        assert result["fallbackSubmitResult"]["state"]["disposition"] == ""

    def test_activity_log_formats_screening_reviews_for_officers(self):
        html = _read_backoffice()
        result = _run_node(_activity_log_runtime_js(html))
        assert "Screening Review Completed" in result["html"]
        assert "Screening review completed for John Harbor" in result["html"]
        assert "No Match" in result["html"]
        assert "Show technical details" in result["html"]
        assert "Copy technical details" in result["html"]
        assert "Status Change" in result["html"]
        assert "raw-provider-123" not in result["firstCardVisible"]

    def test_activity_log_filters_and_unknown_fallback_are_safe(self):
        html = _read_backoffice()
        result = _run_node(_activity_log_runtime_js(html))
        assert 'data-filter="Risk"' in result["html"]
        assert "Risk score changed from 48 to 72; level changed from MEDIUM to HIGH." in result["html"]
        assert "EDD routing evaluated; route selected: edd." in result["html"]
        assert "Unexpected Vendor Event" in result["html"]
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result["html"]
        assert "Risk score changed from 48 to 72" in result["riskHtml"]
        assert "Screening Review Completed" not in result["riskHtml"]
        assert "Unexpected Vendor Event" in result["systemHtml"]
        assert "Risk Recomputed" not in result["systemHtml"]

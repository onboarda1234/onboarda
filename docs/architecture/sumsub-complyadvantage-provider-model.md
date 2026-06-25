# Sumsub and ComplyAdvantage Provider Responsibility Model

## Purpose

This document records the provider responsibility model for RegMind onboarding, approval gates, screening review, adverse media monitoring, and future fixture work.

The rule is intentionally stricter than the current runtime cutover flags:

- Sumsub is the authoritative provider for IDV, liveness, face match, and identity document checks.
- ComplyAdvantage Mesh is the authoritative provider for sanctions, PEP, watchlists, adverse media, and material screening concerns.
- Legacy Sumsub-hosted AML or screening fields may exist for compatibility, historical records, or tests, but new screening and adverse-media approval decisions must not treat those fields as authoritative unless a future configuration explicitly re-authorizes and documents that model.

## Provider Responsibility Matrix

| Function | Source of truth |
| --- | --- |
| IDV / identity verification | Sumsub |
| Liveness / face match | Sumsub |
| Identity document checks | Sumsub |
| Sanctions screening | ComplyAdvantage Mesh |
| PEP screening | ComplyAdvantage Mesh |
| Watchlists | ComplyAdvantage Mesh |
| Adverse media | ComplyAdvantage Mesh |
| Material screening concern | ComplyAdvantage Mesh + officer disposition |
| IDV approval gate | Sumsub IDV state |
| Screening/adverse-media approval gate | ComplyAdvantage screening truth |
| Company registry enrichment | OpenCorporates / registry provider, not AML truth |
| IP geolocation enrichment | IP geolocation provider, not AML truth |

## Data Model Map

### Sumsub IDV Data

Sumsub IDV state is read through `arie-backend/sumsub_idv_status.py`. It builds an officer-facing IDV projection from durable local data and returns gate fields such as `approval_ready`, `approval_blocking`, and IDV-specific blocking flags.

Current Sumsub IDV storage and projection sources include:

- `sumsub_applicant_mappings`: applicant ID to application/person mapping for deterministic webhook linking.
- `idv_resolutions`: manual IDV resolution and senior exception outcomes.
- `audit_log`: webhook and officer action evidence used by the IDV projection.
- `applications.prescreening_data.screening_report.kyc_applicants`: legacy bundled IDV fields where present.

These fields are authoritative only for IDV decisions. They are not authoritative for sanctions, PEP, watchlists, adverse media, or material screening concerns.

### ComplyAdvantage Screening And Adverse Media Data

ComplyAdvantage Mesh provider truth is normalized through `arie-backend/screening_complyadvantage/normalizer.py` and related adapter, webhook, evidence, and backfill modules.

Current ComplyAdvantage storage and projection sources include:

- `applications.prescreening_data.screening_report`: currently consumed by approval, Case Command Centre, Screening Review, memo, and UI flows.
- `screening_reports_normalized`: normalized provider reports and webhook dual-write records. The schema currently has `is_authoritative = 0`; this table is a projection/cache until a later source-of-truth cutover PR changes that contract.
- `monitoring_alerts`: alert rows created from ComplyAdvantage webhook/backfill/manual alert flows.
- `monitoring_alert_evidence`: structured evidence extracted from normalized ComplyAdvantage provider truth, including source title/name, source URL availability, publication date, snippet, provider references, and redacted evidence JSON.
- `complyadvantage_webhook_deliveries`: receipt, retry, and processing status for ComplyAdvantage webhooks.

### Officer Disposition Data

Screening and adverse-media dispositions are not provider facts. They are officer decisions layered on top of provider truth.

Current disposition sources include:

- `screening_reviews`: screening subject disposition, canonical disposition code, rationale, sensitivity flags, four-eyes state, reviewer identity, and second-review fields.
- `monitoring_alerts.officer_action`, `monitoring_alerts.officer_notes`, `reviewed_at`, `reviewed_by`, `resolved_at`: monitoring alert triage and disposition state.
- `audit_log`: disposition and review audit evidence.

False-positive clearance must come from an officer disposition with rationale and audit evidence. Provider state alone must not clear a material hit.

### Legacy And Non-Authoritative Fields

The following current fields are compatibility or projection surfaces unless a later PR explicitly promotes them:

- `screening_adapter_sumsub.py` and `screening.screen_sumsub_aml(...)`: legacy screening compatibility paths.
- `screening_normalizer.py` provider metadata defaulting to `sumsub`: legacy report normalization.
- `screening_reports_normalized.is_authoritative = 0`: normalized projection/cache, not the current approval source.
- Client-side derived screening state in `arie-backoffice.html`: display projection only.
- `applications.prescreening_data.screening_report.kyc_applicants`: legacy bundled IDV data; the dedicated Sumsub IDV gate should be preferred.

## Approval-Gate Responsibility

Approval gates must keep IDV and financial-crime screening separate:

- IDV blockers come from Sumsub IDV state built by `build_sumsub_idv_statuses(...)` and `build_idv_gate_summary(...)`.
- Screening blockers come from ComplyAdvantage Mesh screening truth when the screening cutover is active.
- Adverse-media blockers come from ComplyAdvantage adverse-media evidence plus officer disposition.
- PEP, sanctions, watchlists, adverse media, and material screening concerns must not be satisfied or overridden by Sumsub IDV success.
- Sumsub IDV failure must not imply a sanctions, PEP, watchlist, or adverse-media hit.
- False positives must be officer-cleared and auditable.
- Provider failures, stale screening, missing evidence, and unresolved matches must not silently pass.

Current code still contains legacy compatibility logic in `security_hardening.py` that reads `applications.prescreening_data.screening_report` and can inspect mixed legacy provider fields. That is a compatibility state, not a permission for new code to treat historical AML fields from Sumsub-hosted compatibility paths as authoritative.

## Operational Flow

1. The client submits onboarding data.
2. Sumsub handles IDV where required: identity verification, liveness, face match, and identity document checks.
3. ComplyAdvantage Mesh handles screening and adverse media: sanctions, PEP, watchlists, adverse media, monitoring, and material screening concern detection.
4. Screening results are normalized and stored into the current application screening report and/or normalized ComplyAdvantage storage depending on runtime path.
5. Adverse-media and monitoring signals are stored in `monitoring_alerts` and structured evidence rows are stored in `monitoring_alert_evidence`.
6. Officers review hits, record screening dispositions in `screening_reviews`, and triage monitoring alerts in `monitoring_alerts`.
7. Approval gates read IDV state from the Sumsub IDV projection and screening/adverse-media state from the ComplyAdvantage screening truth model plus officer dispositions.

## Article And Source Links

ComplyAdvantage adverse-media source links should be stored and displayed when available:

- `monitoring_alert_evidence.source_url`
- `monitoring_alert_evidence.source_url_available`
- `monitoring_alert_evidence.source_url_unavailable_reason`
- `monitoring_alert_evidence.source_title`
- `monitoring_alert_evidence.source_name`
- `monitoring_alert_evidence.publication_date`
- `monitoring_alert_evidence.snippet`

If Mesh does not return an article URL, the UI should show a concise explanation such as `Source article link not available from ComplyAdvantage Mesh payload.` The main officer view should not display raw unavailable fields, provider IDs, or JSON unless the user opens technical details.

Do not claim live Mesh article URL availability unless it has been verified against the live ComplyAdvantage Mesh API.

## Implementation Guidance

New code should follow these rules:

- Use `screening_config.get_provider_responsibility_model()` when a static provider responsibility matrix is needed in tests or UI/API labels.
- Use Sumsub state only for IDV gates and IDV UI.
- Use ComplyAdvantage Mesh state for sanctions, PEP, watchlists, adverse media, and material screening gates.
- Treat legacy AML/screening references from Sumsub-hosted compatibility paths as compatibility inputs only.
- Do not make approval decisions from client-side derived state.
- Do not treat normalized projection tables as authoritative until a later source-of-truth cutover PR changes the schema and all consumers.

## Follow-Up PRs

The broader source-of-truth implementation belongs in a separate PR, not this documentation PR. That PR should:

- Promote a single deterministic ComplyAdvantage screening/adverse-media truth model.
- Add deterministic fixtures for clean, possible match, true match, false positive, stale, expired, provider failure, declared PEP, provider-detected PEP, sanctions, adverse media with source URL, adverse media without source URL, and material adverse-media compliance escalation.
- Wire approval gates, Case Command Centre, Screening Review, monitoring alerts, and tests to the same truth interpretation.
- Preserve Sumsub as IDV-only and prove Sumsub IDV success cannot satisfy or override screening/adverse-media blockers.

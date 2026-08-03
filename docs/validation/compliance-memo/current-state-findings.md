# Compliance Memo current-state findings

Audit source: GitHub `onboarda1234/onboarda`, `main` at `aa16a0f930534293ff7f82689d99a7a7b60bb9c9`.

Audit date: 2026-07-31.

This document records the implementation before the Compliance Memo workspace rationalisation. It is intentionally limited to the onboarding Compliance Memo and does not propose changes to Risk Assessment, Screening, Enhanced Review, the AI Compliance Supervisor, or the Case Command Centre.

## End-to-end workflow

The effective workflow is:

1. An authorised officer generates the memo with `POST /api/applications/:id/memo`.
2. The backend builds the deterministic memo and runs its existing rule, validation, and memo-consistency checks as part of generation.
3. The officer reviews the generated artefact and records an approval reason plus the mandatory sign-off acknowledgement.
4. An Admin or Senior Compliance Officer approves with `POST /api/applications/:id/memo/approve`.
5. The PDF is generated from the canonical latest memo with `GET /api/applications/:id/memo/pdf`.

The manual `POST /memo/validate` and memo-consistency endpoints also exist, but generation already stores validation and consistency results. They are not an additional required human workflow stage.

## Frontend rendering

The onboarding memo is rendered inside the Application Overview in `arie-backoffice.html`.

Current presentation:

- a memo header with generation and PDF download buttons;
- a governance dashboard showing status, readiness, primary blocker, and next action;
- a decision summary assembled from memo JSON;
- a collapsible HTML rendering of every memo section;
- AI-source, advisory, fallback, and risk badges;
- risk, screening, document, ownership, enhanced-review, explainability, red-flag, monitoring, rule-engine, validation, and memo-consistency panels;
- an approval-reason textarea, officer sign-off checkbox, validation button, approval button, and another PDF button.

The application detail response supplies `latest_memo` metadata and `latest_memo_data`; `normalizeMemoPayload()` handles direct and legacy wrapped memo payloads. The UI currently reproduces the memo as HTML instead of displaying the generated PDF.

## Memo generation

`ComplianceMemoHandler` in `arie-backend/server.py`:

- authorises `admin`, `sco`, `co`, and `analyst`;
- enforces Agent 5 availability, rate limiting, authoritative risk integrity, and the document-reliance gate;
- loads active documents, parties, screening review truth, prescreening corrections, and enhanced-review summary;
- computes a deterministic input fingerprint;
- reuses the canonical latest memo when inputs and output profile are unchanged;
- otherwise calls `build_compliance_memo()` in `memo_handler.py`;
- receives memo, rule-engine, validation, and memo-consistency results from that single generation call;
- inserts a new versioned `compliance_memos` row with `review_status='draft'` and the computed validation/consistency fields;
- writes generation and governance audit evidence.

The live generation path is deterministic. The optional Claude memo integration is feature-gated off and is not part of the live path.

## Memo status fields

The `compliance_memos` row currently stores several orthogonal status/control fields rather than a single lifecycle enum:

- `review_status`: `draft`, `reviewed`, `approved`, or `rejected`;
- `validation_status`: `pending`, `pass`, `pass_with_fixes`, or `fail`;
- `supervisor_status`: the existing memo-consistency result;
- `blocked` and `block_reason`;
- `is_stale`, `stale_reason`, `stale_reasons`, `stale_trigger`, and `stale_marked_at`;
- `approved_by`, `approved_at`, and `approval_reason`;
- `version`, `memo_version`, `raw_output_hash`, `pdf_generated_at`, and `created_at`.

`_memo_final_status()` currently derives UI/API values such as `not_generated`, `draft`, `validated`, `requires_fixes`, `validation_failed`, `blocked`, `approved`, `approved_with_findings`, `rejected`, and `stale`.

The requested workspace lifecycle can be derived without a new database field:

- no row -> `NOT_GENERATED`;
- current unapproved row -> `DRAFT` or `AWAITING_OFFICER_SIGNOFF` based on existing persisted gates;
- approved current row -> `FINAL`;
- non-current historical row -> `SUPERSEDED`;
- current stale row -> `STALE` as the overriding condition.

## Approval and sign-off

`MemoApproveHandler` is restricted to `admin` and `sco`. It currently enforces, server-side:

- canonical latest-memo selection;
- application risk integrity;
- sign-off ownership rules;
- freshness/staleness;
- the mandatory officer sign-off payload;
- non-empty `approval_reason`;
- document reliance;
- persisted memo block verdict;
- validation `pass` or senior-authorised `pass_with_fixes`;
- rejection of fallback output;
- existing consistency verdict and escalation gates;
- EDD routing/completion conditions;
- SCO-only conditions where applicable.

Approval atomically sets `review_status='approved'`, approver/timestamp/reviewer and `approval_reason`, then persists audit, sign-off, governance, and ownership evidence. This logic is frozen and must remain unchanged.

The current textarea is coupled directly to approval: there is no standalone save action for a draft officer rationale. The existing `review_notes` column is available but unused by onboarding memo handlers, so it can close that gap without a schema change.

## PDF generation and download

`MemoPDFDownloadHandler`:

- selects the canonical latest memo;
- rechecks freshness and blocks stale export;
- passes memo/application/validation/approval context to `generate_memo_pdf()`;
- generates the PDF on demand with WeasyPrint;
- records `pdf_generated_at`, SHA-256, build metadata, and an audit event;
- returns the bytes as an attachment with memo/version/hash headers.

The current frontend only downloads the file. There is no embedded preview. The current PDF template renders twelve dashboard-like sections and a validation/consistency summary; it has no unmistakable draft watermark and no dedicated final locked presentation.

## Staleness and version handling

Canonical selection is centralised in `memo_governance.py` and ordered by highest `version`, then newest `created_at`, then highest `id`.

Generation is idempotent when the deterministic input hash and output profile are unchanged. Changed input evidence produces a new row/version. Historical rows remain in `compliance_memos` and evidence-pack exports, but there is no lightweight version-history API or memo workspace view.

Staleness is already implemented and audited. Material application corrections, document/evidence changes, screening disposition/truth changes, enhanced-review changes, risk recomputation, and input timestamp/hash mismatches can mark the latest memo stale. Marking stale resets review, validation, consistency, approver, and approval-reason fields. Approval, validation, and PDF export independently recheck freshness and fail closed.

## Database and APIs

Primary table: `compliance_memos`, with PostgreSQL and SQLite definitions in `arie-backend/db.py`. The schema already contains all fields required for the rationalised workspace, including versioning, review notes, approval evidence, and staleness. `applications.inputs_updated_at` is the application snapshot/freshness timestamp.

Current onboarding memo APIs:

- `POST /api/applications/:id/memo` - generate or idempotently reuse the memo;
- `POST /api/applications/:id/memo/validate` - revalidate the stored memo;
- `GET /api/applications/:id/memo/validation` - read validation and approval metadata;
- `POST /api/applications/:id/memo/approve` - officer approval/sign-off;
- `GET /api/applications/:id/memo/pdf` - generate/download the canonical PDF;
- existing memo-consistency endpoints retained for backend compatibility;
- `GET /api/applications/:id` - includes the canonical latest memo and staleness projection;
- the evidence-pack endpoint includes every memo row, but is too broad and sensitive for a lightweight version-history view.

Proven API gaps are limited to saving draft rationale and reading a compact memo version history. Both can reuse the existing memo resource/table. No new lifecycle table or database column is justified.

## Tests

Existing coverage includes:

- canonical version selection, idempotency, governance, and approval reason;
- stale triggers, state reset, regeneration, and approval/PDF hard gates;
- dual-approval race handling;
- sign-off validation and audit persistence;
- validation and senior-approval variants;
- persisted block verdicts;
- memo output concision and appendix retention;
- authoritative risk and screening truth in memo/PDF output;
- PDF validity, escaping, missing sections, approval metadata, and risk display;
- static Application Review UI permission, refresh, and workflow guards.

Baseline targeted result under Python 3.12.13: **244 passed, 10 skipped** across 254 memo/application-review tests. The skipped tests require platform-specific PDF/PostgreSQL facilities. A Python 3.9 run is not valid for this source tree because current modules use Python 3.10+ union syntax.

## Compatibility conclusion

The requested UI rationalisation is compatible with the existing workflow because generation already persists all backend gate results and approval re-enforces them. The implementation can remove the duplicate HTML dashboards while preserving generation, officer review, Admin/SCO approval, audit, staleness, and final PDF download behavior. No approval-logic, Risk Assessment, Screening, Enhanced Review, AI Compliance Supervisor, or Case Command Centre change is required.

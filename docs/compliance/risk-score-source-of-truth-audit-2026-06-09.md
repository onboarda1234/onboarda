# Risk Score Source-of-Truth Audit - 2026-06-09

## Scope

Audited rule-based risk score consistency across Application Review, compliance memo generation, memo PDF export, approval gates, decision records, audit output, and CSV/report exports.

Target case: `ARF-2026-900289`.

## Local Reproduction Status

The specific `ARF-2026-900289` row was not present in the accessible local working tree, and no local SQLite database file was available to query. The configured `/Users/Aisha/Desktop/Onboarda` workspace path was blocked by macOS privacy permissions, so the audit and remediation were performed in the accessible RegMind working tree at `/Users/Aisha/Onboarda-pr410`.

Regression coverage uses an equivalent fixture:

- authoritative application score: `applications.risk_score = 70`
- authoritative risk level: `applications.final_risk_level = VERY_HIGH`
- stale memo snapshot: `memo_data.metadata.risk_score = 42`, `risk_rating = MEDIUM`

## Risk Sources Found

- `applications.risk_score`: authoritative case score for decisioning.
- `applications.risk_level`: legacy/effective score band, superseded by `final_risk_level` when present.
- `applications.final_risk_level`: authoritative elevated/floored final risk band when present.
- `applications.risk_dimensions`: stored rule-engine dimension output.
- `applications.risk_computed_at`: authoritative score calculation timestamp.
- `applications.risk_config_version`: risk configuration version/timestamp used for stored score.
- `applications.prescreening_data`: input source for screening and risk factors, not an authoritative score replacement.
- `compliance_memos.memo_data.metadata`: memo-time risk snapshot, now treated as historical unless it matches application truth.
- `decision_records.risk_level`: normalized decision band; now populated from `final_risk_level` when present.
- `decision_records.extra_json.authoritative_risk_score`: added snapshot of the application score used for the decision.
- `audit_log.before_state` / `after_state`: approval and stale-memo events now include risk score/level where relevant.
- CSV/report export: now selects `COALESCE(applications.final_risk_level, applications.risk_level)` and `applications.risk_score`.

## Root Cause

Application Review uses application risk fields, while memo PDF generation rendered the risk header from `compliance_memos.memo_data.metadata`. If a memo was generated before the application risk score changed, the PDF could display the stale memo snapshot without first comparing it to the authoritative application score.

Existing memo freshness checks covered timestamps and memo input hashes, but they did not directly compare `memo_data.metadata` risk score/level to `applications.risk_score` and `final_risk_level`. Legacy memo rows without current hashes were therefore vulnerable to stale PDF output.

## Affected Surfaces

- Application Review: already reads authoritative application risk and labels recomputed current-config score separately.
- Compliance memo generation: used application risk but did not stamp enough source metadata for audit.
- PDF export: used memo risk metadata for header display.
- Approval gates: mostly used application risk, but high-risk checks still had raw `risk_level` references.
- Decision records: stored risk level only, not score, and did not prefer `final_risk_level`.
- CSV/report exports: selected raw `risk_level`, not final effective risk level.
- Audit output: PDF export audit lacked the risk score actually rendered.

## Fix Implemented

- Added `_application_authoritative_risk_metadata()` as the backend authoritative risk snapshot helper.
- Added memo risk snapshot extraction and direct mismatch detection.
- Mark latest memo stale when memo risk snapshot differs from authoritative application risk.
- Block memo PDF export with `409` when the memo is stale.
- Stamp new memos with:
  - `authoritative_case_risk`
  - `risk_source`
  - `risk_calculated_at`
  - `memo_generated_at`
- PDF header now renders:
  - `Authoritative Case Risk Score`
  - risk level
  - risk calculated timestamp
  - memo generated timestamp
  - PDF generated timestamp
- PDF export audit now records the authoritative risk snapshot and PDF hash.
- Approval high-risk role/dual-approval checks now use the same effective final risk level.
- Decision records now prefer `final_risk_level` and store `authoritative_risk_score` in `extra_json`.
- CSV/report exports now use effective final risk level via `COALESCE(final_risk_level, risk_level)`.

## Tests Added/Updated

- `test_risk_snapshot_mismatch_marks_memo_stale_and_blocks_approval`
- `test_pdf_generator_uses_authoritative_application_risk_over_legacy_memo`
- `test_pdf_generator_fails_closed_when_no_authoritative_risk_exists`
- `test_final_risk_level_is_decision_record_authority`
- `test_backend_pdf_export_checks_memo_risk_staleness_before_rendering`
- `test_reports_export_effective_final_risk_level`

## Validation

Local focused tests:

```text
pytest arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_phase3_memo_integrity.py arie-backend/tests/test_decision_model.py arie-backend/tests/test_risk_display_integrity.py -q
90 passed
```

PDF test module:

```text
pytest arie-backend/tests/test_pdf_generator.py -q
8 skipped - WeasyPrint native libraries not available locally
```

Compile check:

```text
python3 -m py_compile arie-backend/server.py arie-backend/memo_handler.py arie-backend/pdf_generator.py arie-backend/decision_model.py arie-backend/security_hardening.py
passed
```

Base commit inspected: `4782d1246473eac264caecd56d592d8ca8b4db05`.

## Staging Evidence Required

Not completed in this local run because staging credentials/ECS metadata were not available in the workspace.

Before merging/deploying, validate on staging:

- application ID: `ARF-2026-900289`
- Application Review score: expected `70`
- memo metadata score: must be `70`, or memo must be marked stale and require regeneration
- PDF score: must be `70`, or export must be blocked until memo regeneration
- approval gate score/risk level: must use `70` and the effective final risk level
- commit SHA deployed
- ECS task definition
- browser smoke result

## Residual Risks

- Historical memo narrative text may still contain stale risk wording in old `memo_data.sections`; stale detection blocks approval/PDF export when the memo risk metadata disagrees with application truth, but old narrative sections are not rewritten in place.
- Existing legacy memos with no risk metadata are not automatically marked stale solely for missing metadata. PDF rendering uses application truth for the header, and approval remains guarded by risk integrity plus freshness controls.
- Full staging smoke for `ARF-2026-900289` is still required.

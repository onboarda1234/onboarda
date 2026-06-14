# API / PDF Smoke

## Branch-Stage Local Smoke

Representative memo generation was executed locally without staging mutations or provider calls.

Result:

- One authoritative recommendation: pass
- Blocked screening no longer produces `APPROVE_WITH_CONDITIONS`: pass
- Pending screening is not risk-decreasing/mitigating evidence: pass
- Default memo materially shorter: pass
- Appendix evidence retained: pass
- Officer note rough text sanitized from formal memo: pass

Raw output:

- `runtime_json/memo_after_pr5b_summary.json`
- `runtime_json/memo_after_pr5b.json`

## PDF Smoke

Native local PDF generation is blocked by missing/crashing WeasyPrint/Pango libraries. A unit-level PDF renderer smoke was added with a fake WeasyPrint adapter and passed:

- Confirms decision-paper PDF HTML includes `SCREENING RESOLUTION REQUIRED`.
- Confirms `APPROVE WITH CONDITIONS` is absent for the blocked case.
- Confirms `Appendix Evidence Index` is rendered.
- Confirms content hash footer remains present.

Staging PDF generation must still be run after merge/deploy.

## Corrective Branch API / PDF Smoke

The corrective browser-defect patch re-ran local deterministic memo generation
without staging mutations or provider calls.

Result:

- LOW canonical risk score renders as LOW in formal memo text: pass
- `HIGH risk with score 22/100` is absent from generated memo and fake-PDF HTML: pass
- Canonical blockers are present in memo metadata and sections: pass
- Blocked screening/document conditions remain approval blockers: pass
- `pass_with_fixes` and approval-blocked states are covered by browser/static tests: pass
- Fake-PDF renderer smoke still passes through `test_pr5b_memo_concision.py`: pass

Corrective staging API/PDF smoke is pending until the corrective PR is merged,
deployed, and `/api/version` matches the corrective merge SHA.

## Corrective PR #483 Staging API / PDF Smoke

PR #483 was merged and deployed. Staging `/api/version` matched merged main SHA
`7cf095eeeb619b95fbe08764da529f00c7225b94`.

Result: fail.

What passed:

- `/api/version` matched deployed SHA.
- `POST /api/applications/ARF-PR4-AUTO-7f861903/memo` returned `200`.
- PDF download returned `200` with `%PDF` content.
- Closed-remediation API regression subset passed.

What failed:

- The memo endpoint reused memo `357` generated under
  `memo_build_git_sha=4e2262dc14db86a6e3caacb617182fbe8579ae5c`.
- Reused memo text still contained `HIGH risk with score 25/100`.
- Smoke summary reported `contains_high_low_score_mismatch=true`.

Evidence:

- `runtime_json/staging_pr5b_corrective_version.json`
- `runtime_json/staging_pr5b_corrective_generated_memo_summary_redacted.json`
- `runtime_json/staging_pr5b_corrective_api_pdf_smoke_result.json`
- `runtime_json/staging_pr5b_corrective_closed_regression_smoke_redacted.json`
- `staging_pr5b_corrective_generated_memo.pdf`

Disposition:

- PR-5B remains incomplete.
- A second corrective patch was required to invalidate idempotent reuse for
  memos generated under older output profiles.

## Second Corrective Branch API / PDF Smoke

Branch: `codex/pr5b-memo-output-cache-invalidation`

Local deterministic validation before PR:

- Current-profile memo reuse remains allowed.
- Old-profile memo reuse is rejected even when source-input hash matches.
- Missing-profile memo reuse is rejected even when source-input hash matches.
- Memo generation fingerprint changes when output profile version changes.
- PR-5B concise memo output still renders LOW canonical risk as LOW and keeps
  blocker/validation semantics intact.

Staging API/PDF smoke must be re-run after the second corrective PR is merged,
deployed, and `/api/version` matches the new merged main SHA.

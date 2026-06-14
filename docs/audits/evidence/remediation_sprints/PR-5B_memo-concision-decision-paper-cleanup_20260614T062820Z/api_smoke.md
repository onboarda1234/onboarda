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

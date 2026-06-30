# Reconciliation Discrepancies

The Word report was found on Desktop, copied through Finder into the evidence folder, and extracted. Discrepancies below compare the report claims to GitHub metadata and available closure evidence.

## P1 - Exact-SHA classification for #622

- Current report claim: Report says Closed PASS after #624.
- Verified fact: Evidence supports functional closure after #624, but final validation ran on newer SHA 7f7fbe65 containing #622/#624, not exact #622 merge SHA.
- Recommended correction: Change to PASS WITH VALIDATION LIMITATION after #624, or explicitly state newer-main validation.

## P1 - Exact-SHA classification for #624

- Current report claim: Report says Closed PASS.
- Verified fact: Closure report says #624 direct deploy was superseded by #625 and validation ran on newer SHA 7f7fbe65 containing #624.
- Recommended correction: Change to PASS WITH VALIDATION LIMITATION or explicitly state ancestor/newer-main validation.

## P1 - Earlier broad PR ranges need citations

- Current report claim: Report marks #433-#449, #436-#439, #440-#444, #469-#477, #516-#518 as Closed/Closed PASS style streams.
- Verified fact: GitHub confirms merged PRs, but this pass did not locate direct closure evidence for every PR in those ranges; the report itself says exact details should be reconciled.
- Recommended correction: Add PR titles, merge SHAs, closure paths, or downgrade those stream rows to “merged; closure evidence pending citation”.

## P1 - Missing KYC-DOCS-FINAL-LIVE-BROWSER-SMOKE-1 row

- Current report claim: Report does not include this final KYC Docs workstream.
- Verified fact: Available evidence shows KYC-DOCS-FINAL-LIVE-BROWSER-SMOKE-1 PASS WITH LIMITATION on current origin/main ea33cdf.
- Recommended correction: Add this validation row and limitation: no genuine missing-policy row in scanned staging fixtures.

## P2 - Current staging direct version evidence in this run auth-gated

- Current report claim: Report relies on /api/version closure standard.
- Verified fact: This recon run could not directly authenticate /api/version; public health worked, unauthenticated version returned 401. Closure reports provide authenticated evidence.
- Recommended correction: Cite closure report version files; do not claim this recon run independently authenticated current /api/version unless credentials are supplied.

## P2 - Co-founder readiness wording needs strict scope qualifier

- Current report claim: Report says strong progress and serious pilot conversations, with risk note against production readiness.
- Verified fact: This is broadly accurate, but should explicitly say controlled staging-backed pilot only; production/uncontrolled rollout remains blocked by Monitoring integration, CA production validation and Go/No-Go.
- Recommended correction: Use the recommended pilot-readiness wording in findings.

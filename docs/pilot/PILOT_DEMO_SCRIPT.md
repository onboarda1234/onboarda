# Pilot Canonical Dataset v1 — Controlled Demo Script

Status: **41 synthetic fixtures seeded on AWS staging; demo-completion PR remains draft-only**

This walkthrough uses only deterministic, synthetic, non-production `RM-PILOT-*` records. It does not activate RSMP, call a provider, recompute an existing application, or change staging data. The application references remain permanent across every approved seed.

## Operator preparation

- Confirm the displayed environment is staging and every record is marked **Pilot Canonical Dataset / Synthetic / Non-production / Fixture**.
- Open `RM-PILOT-041` first and keep the application list filtered to `RM-PILOT-*`.
- Do not edit, approve, regenerate, rescreen or export real/pilot data during the walkthrough.

## 1. Happy path — RM-PILOT-041 (7 minutes)

1. Show the completed onboarding submission and verified KYC documents.
2. Show the cleared synthetic screening disposition.
3. Open the authoritative risk breakdown: score **12.0**, tier **LOW**, Fast Lane, with no floor or unresolved sentinel.
4. Open the approved deterministic compliance memo. Point out that AI Supervisor is excluded from the controlled pilot and its retained synthetic evidence is not an active validated workflow.
5. Show the final approval record.
6. Generate the authorised evidence pack and point out the PDF risk/screening/memo sections and CSV audit trail.
7. Finish with the completed periodic review and dismissed monitoring false positive.

## 2. Medium risk — RM-PILOT-006 (3 minutes)

- Show international trading and cross-border services.
- Confirm score **43.3**, tier **MEDIUM**, Standard Review and the stored per-service evidence.
- Contrast this with the Low-risk happy path without changing any record.

## 3. PEP — RM-PILOT-015 (3 minutes)

- Show the nested declared **Domestic PEP** role.
- Confirm factor score 4, final score **55.0**, **HIGH** floor, EDD and dual-control approval.
- Note that RM-PILOT-016 through 019 permanently cover the other four approved PEP roles.

## 4. Sanctions — RM-PILOT-024 (3 minutes)

- Show the post-onboarding synthetic sanctions monitoring alert.
- Show the escalated screening disposition, **VERY_HIGH** risk, EDD and blocked approval route.
- Confirm all evidence is synthetic and no provider was called.

## 5. Officer correction — RM-PILOT-037 (4 minutes)

1. Show the initial misspelled registered entity name.
2. Show the fulfilled correction request and applicant-supplied proof of address.
3. Open the officer correction before/after values, reason, evidence source and downstream impact.
4. Show the audit sequence: request, applicant correction, officer verification and final disposition.
5. Confirm the final score remains **12.0 LOW** and the application is approved with no unresolved control.

## 6. Evidence export — RM-PILOT-039 (3 minutes)

- Generate the backend-authoritative evidence package.
- Show the PDF case, client submission, risk assessment, screening and memo sections.
- Show the CSV audit trail and file hashes in the package manifest.
- Confirm score, tier, screening disposition and memo agree with stored backend evidence.

## 7. AI Supervisor scope boundary — RM-PILOT-040 (1 minute)

- Show the explicit **Excluded from controlled pilot** notice on the deterministic memo.
- Explain that synthetic Supervisor evidence is retained only for future development and testing.
- Do not present a seeded verdict or recommendation as an active, validated or pilot-ready workflow.
- Do not run or enable the Supervisor feature.

## 8. Periodic review — RM-PILOT-005, 008, 014 and 041 (3 minutes)

- Show the completed Low-risk reviews on RM-PILOT-005 and RM-PILOT-041.
- Show the open Medium-risk family-office review on RM-PILOT-008.
- Contrast it with the open High-risk Private Banking review on RM-PILOT-014.
- Confirm dates, priority, fixture labels and intentionally suppressed synthetic notifications.

## 9. Monitoring — RM-PILOT-004, 024, 025 and 041 (3 minutes)

- Show the dismissed false positive on RM-PILOT-004.
- Show the sanctions-monitoring escalation on RM-PILOT-024.
- Contrast it with the open material adverse-media alert and officer-review requirement on RM-PILOT-025.
- Close with RM-PILOT-041's dismissed false positive and confirm that every alert is clearly synthetic; do not trigger provider refresh.

## Optional compliance appendix

- Trust evidence and EDD: `RM-PILOT-028`.
- Source-of-wealth corroboration: `RM-PILOT-029`.
- Lane B/fail-closed controls: `RM-PILOT-033` through `RM-PILOT-035`.
- Volume compliance review without a High floor: `RM-PILOT-012`.

No pilot-readiness or production-readiness claim is made.

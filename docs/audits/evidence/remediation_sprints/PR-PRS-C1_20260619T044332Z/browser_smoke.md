# PR-PRS-C1 Browser Smoke

- URL: `https://staging.regmind.co/backoffice`
- Login: staging QA account (`sco`); password/token omitted
- Browser: Playwright Chromium, authenticated through the real back-office login form
- Back-office application detail API was queried from the authenticated browser session for the synthetic application risk values.

## Screenshots

- Confirmed Risk Elevation Propagates: `docs/audits/evidence/remediation_sprints/PR-PRS-C1_20260619T044332Z/screenshots/01_confirmed_risk_elevation_propagates.png`
- No Automatic Downgrade: `docs/audits/evidence/remediation_sprints/PR-PRS-C1_20260619T044332Z/screenshots/02_no_automatic_downgrade.png`
- Material-Change Rescore Gate: `docs/audits/evidence/remediation_sprints/PR-PRS-C1_20260619T044332Z/screenshots/03_material_change_rescore_gate.png`
- Next Cycle Uses Final Risk Cadence: `docs/audits/evidence/remediation_sprints/PR-PRS-C1_20260619T044332Z/screenshots/04_next_cycle_cadence_final_risk.png`
- No-Change Regression: `docs/audits/evidence/remediation_sprints/PR-PRS-C1_20260619T044332Z/screenshots/05_no_change_regression.png`

## Confirmations

- Confirmed MEDIUM -> HIGH periodic-review outcome propagated to canonical application risk and wrote `periodic_review.canonical_risk_recomputed`.
- Previous HIGH canonical risk was preserved when the review confirmed MEDIUM; audit recorded `downgrade_prevented=true`.
- `material_change_identified` without risk decision/rationale returned 409 with `material_change_risk_decision_required`; documented rationale allowed completion.
- Next pending review used final HIGH risk cadence: 12 months, due `2027-01-01` from the onboarding anniversary.
- `no_change` completion left canonical application risk unchanged and wrote no canonical-risk recompute audit for that review.

## Browser Detail Reads

```json
{
  "downgrade": {
    "company_name": "PRPRSC1-STAGING-20260619045040 Downgrade Floor Ltd",
    "final_risk_level": "HIGH",
    "id": "prprsc1-staging-20260619045040-downgrade-floor",
    "ref": "PRPRSC1-STAGING-20260619045040-DOWNGRADE-FLOOR",
    "risk_level": "HIGH",
    "status": "approved"
  },
  "elevation": {
    "company_name": "PRPRSC1-STAGING-20260619045040 Elevation Ltd",
    "final_risk_level": "HIGH",
    "id": "prprsc1-staging-20260619045040-elevate",
    "ref": "PRPRSC1-STAGING-20260619045040-ELEVATE",
    "risk_level": "HIGH",
    "status": "approved"
  },
  "no_change": {
    "company_name": "PRPRSC1-STAGING-20260619045040 No Change Ltd",
    "final_risk_level": "HIGH",
    "id": "prprsc1-staging-20260619045040-no-change",
    "ref": "PRPRSC1-STAGING-20260619045040-NO-CHANGE",
    "risk_level": "HIGH",
    "status": "approved"
  }
}
```

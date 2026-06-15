# Browser Smoke

Environment: local demo smoke server

Browser engine: Playwright Chromium

Raw result: `runtime_json/browser_smoke_result.json`

## Result

Passed locally.

## Validated

- Login succeeded as admin.
- Agent 1 settings show the Evidence Control Layer and Document Policy Registry.
- Registry summary is visible.
- EDD policy search shows Source of Wealth and `DOC-EDD-SOW-v1`.
- Change Management section shows Director change and UBO change evidence.
- Periodic Review section shows periodic review attestation.
- Technical/unknown section shows `Unclassified document / unknown policy` and automated reliance blocking language.
- Application Review document card shows dominant `Review required` reliance badge for a document with a material OCR warning.
- Routine `File format` pass is hidden by default.
- Material `Low OCR confidence` warning is visible by default.
- Expanding technical details shows routine technical passes, Agent execution ID, evidence hash, and the full check result list.
- Old `Pilot Evidence Classification` and `Overall Result` labels are absent.
- No browser console errors, page errors, or HTTP 500 responses were observed.

## Screenshots

- `screenshots/agent1_policy_registry_default.png`
- `screenshots/agent1_policy_registry_edd_sow_filter.png`
- `screenshots/agent1_policy_registry_change_management.png`
- `screenshots/agent1_policy_registry_unknown_unclassified.png`
- `screenshots/application_review_evidence_control_card.png`
- `screenshots/application_review_technical_details_expanded.png`

## Staging Status

Staging browser smoke remains pending until the PR is merged and the merged main image is deployed.

# Browser Smoke

## Local pre-PR smoke

Target: `http://localhost:10052/backoffice`

Validation method:

- local Python 3.11 server
- Playwright Core + Chrome
- rendered `renderStandardKycDocumentTaxonomy(...)` with fixture data that exercises verified, review-required, expired, missing, and enhanced-evidence rows

Observed:

- compact row-based document review layout rendered
- default rows did not render `document-review-fields`
- `Details` remained collapsed by default
- collapsed `Details` rendered as a compact inline control, not a full-width panel
- opening `Details` exposed policy ID/version, agent run ID, evidence hash, and verification timestamp
- direct `View` and `Download` actions rendered for uploaded rows
- missing rows showed `No document uploaded` and disabled `View`/`Download`
- helper note rendered; large KYC advisory banner did not
- portal-slot documents rendered expected slot labels and did not show `Unclassified`

Artifacts:

- `screenshots/local_compact_document_review_default.png`
- `screenshots/local_compact_document_review_details_open.png`
- `runtime_json/local_browser_smoke.json`

## Staging post-merge smoke

Pending deployment to main.

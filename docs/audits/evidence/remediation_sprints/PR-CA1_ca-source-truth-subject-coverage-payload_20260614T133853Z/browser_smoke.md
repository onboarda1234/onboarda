# PR-CA1 Browser Smoke

## Local Static Browser Smoke

Tool: Playwright Python with Chromium, headless.

URL: `http://127.0.0.1:8765/arie-backoffice.html`

Stubbed endpoint:

- `/api/config/environment` returned `{"environment":"local-smoke"}`.

Checks:

- Provider filter displays `ComplyAdvantage Mesh screening source`.
- Provider filter displays `Sumsub IDV/KYC source`.
- `formatProviderName('complyadvantage')` returns `ComplyAdvantage Mesh`.
- `formatProviderName('mesh')` returns `ComplyAdvantage Mesh`.
- Blank provider formatting does not fabricate CA.
- `Unknown Provider` fallback is present.
- API integration modal label `ComplyAdvantage Mesh AML / Media / Monitoring` is present.
- Legacy `ComplyAdvantage KYB / Media / Monitoring` label is absent.
- No page errors or console errors.

Result: passed.

Screenshot:

- `screenshots/backoffice-provider-label-http-smoke.png`

Note:

- An initial `file://` smoke also passed the label checks but produced an expected browser console error because `file://` cannot fetch `/api/config/environment`. The HTTP smoke above is the authoritative local browser result.

## Staging Browser Smoke

Status: pending after merge/deploy.

Required:

- Login as permitted officer/admin.
- Open provider status, screening queue, and application screening tab.
- Confirm ComplyAdvantage Mesh terminology is consistent.
- Confirm Sumsub is shown only as IDV/KYC where applicable.
- Confirm unknown provider does not show as CA.
- Save screenshots.
- Check console/network errors.

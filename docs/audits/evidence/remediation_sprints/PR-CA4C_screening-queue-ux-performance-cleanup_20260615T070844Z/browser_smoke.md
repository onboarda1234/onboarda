# PR-CA4C Browser Smoke Evidence

## Local Browser Smoke

Method:

- Static local server for `arie-backoffice.html`.
- Playwright browser automation with authenticated UI state and controlled queue fixture.
- URL: `http://127.0.0.1:8765/arie-backoffice.html#screening`

Raw evidence:

- `runtime_json/local_browser_smoke.json`
- `screenshots/local_screening_queue_filter_bar_authenticated.png`

Verified:

- Search placeholder is `Search subject, company, ARF, or Mesh reference`.
- Search input is wide enough for the placeholder in the tested viewport.
- Separate Application reference filter is not visible.
- Default type filter does not show legacy `Individual`.
- Legacy `Company sanctions screening` wording is absent.
- Replacement `Entity AML screening pending` wording is visible.
- Queue rows render.
- Active page title is `Screening Queue`.
- No console messages were captured.

Result:

```text
passed
```

## Staging Browser Smoke

Status:

- Pending until PR merge, staging deployment, and `/api/version` confirmation.

Required post-merge checks:

- Screening Queue loads with the available staging dataset.
- Load time is captured.
- Queue is visibly responsive.
- Filter bar is simpler.
- No Application Reference filter is visible.
- Queue search placeholder is clear and universal.
- `Individual` filter is hidden or renamed to `Other person` only when applicable.
- Legacy `Company sanctions screening pending` wording is gone.
- Entity/ComplyAdvantage Mesh screening wording is clear.
- View/detail still opens full evidence.
- No console/network errors.


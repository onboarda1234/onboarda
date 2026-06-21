# R5 Live Browser Smoke

Date: 2026-06-21T03:14Z

Target: `https://staging.regmind.co`

Staging version during smoke:

- `git_sha`: `20e4fb4cbd9b24f91382eb66696c42c7a7cf072c`
- `image_tag`: `20e4fb4cbd9b24f91382eb66696c42c7a7cf072c`
- `environment`: `staging`

Method:

- Current staging HTML was loaded first for the before-state.
- PR HTML was then loaded through Playwright route interception for `/backoffice` and `/backoffice/supervisor`; all APIs and navigation used live staging.
- Authenticated as staging SCO test account via the same JWT/session-injection method used by prior PR-PRS browser smokes.

Result: PASS

1. Before-state proof: current staging `arie-backoffice.html` has no `rel="icon"` declaration, and `GET /favicon.ico` returns `404 {"error":"Not found"}`.
2. PR HTML `/backoffice`: no `GET /favicon.ico` request/404 was observed.
3. No new console errors, page errors, failed requests, or `>=400` responses were introduced.
4. Back office loaded normally; authenticated queue navigation, application detail open, and Lifecycle tab navigation passed.
5. PR HTML `/backoffice/supervisor`: no `GET /favicon.ico` request/404 and no console/network errors.

Artifacts:

- `browser_smoke_results.json`
- `network_console_raw.json`
- `network_console_report.html`
- `screenshots/before-current-staging-backoffice.png`
- `screenshots/after-pr-html-backoffice.png`
- `screenshots/after-pr-html-supervisor-subroute.png`
- `screenshots/network-console-before-after.png`

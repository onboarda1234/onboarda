# PR-MON-PROTECTED-BASELINE-1 — Founder Manual Browser Acceptance

Timebox: **under 10 minutes**
Target: AWS staging after deployment of the exact PR merge SHA
Role: approved staging administrator
Safety: inspect only. Do not approve, reject, disposition, upload, download, or
otherwise mutate a protected record. Keep credentials, tokens, and customer
information out of screenshots.

Every record field, checkbox, and evidence item below is mandatory. An
unexecuted, unavailable, or ambiguous check is **FAIL**, not a limitation.

Preflight, before any navigation:

- [ ] Open browser Developer Tools, select **Console** and **Network**, enable
      **Preserve log**, clear both logs, and keep Developer Tools open for the
      entire run.
- [ ] Confirm the final evidence will retain redacted Console/Network captures
      with request path/status and error text only. Do not export a raw HAR or
      capture authorization headers, cookies, tokens, or response bodies.
- [ ] Sign in through the approved back-office UI. Before the exact-deployment
      check, the only expected mutation request is
      `POST /api/auth/officer/login`; do not navigate to a protected record yet.
      Confirm authentication succeeds.

If the complete run cannot be inspected with preserved Console and Network
logs, stop and return **FAIL**.

Record:

- Founder / date:
- Expected merge SHA:
- `/api/version.git_sha`:
- `/api/version.image_tag`:
- Evidence folder or links:

## 0. Exact deployment gate — stop on mismatch

- [ ] Open `GET /api/version` and confirm
      `git_sha == image_tag == expected merge SHA` using the complete SHA.

If any value differs, stop immediately and return **FAIL** without performing
the remaining checks.

## 1. Application Review — 3 minutes

- [ ] Confirm **Applications** loads after the authenticated exact-SHA gate.
- [ ] Open canonical application `RM-PILOT-039`; confirm Application Detail and
      all seven protected tabs render: Overview, KYC & Documents, Screening,
      Compliance Supervisor, Alerts, Lifecycle, and Activity.
- [ ] On **KYC & Documents**, confirm three current canonical documents render
      with their document identity/type, party association, review status, and
      verification status. No loading/error placeholder remains.
- [ ] On **Overview**, confirm the memo is approved; Generate and Validate retain
      their current enabled state, while Approve Memo is disabled as already
      approved.
- [ ] Confirm Approve, Reject, and Submit to Compliance remain disabled for the
      terminal approved application, with an appropriate reason.
- [ ] Confirm authoritative risk evidence and Evidence Pack CSV/PDF controls are
      visible. Do not download or invoke them.

Expected result: **No observable behaviour change.**

## 2. Screening Queue and Review — 3 minutes

- [ ] Open **Screening Queue**; confirm the queue, search, status/type/PEP
      filters, result count, and pagination controls render.
- [ ] Confirm the default officer view excludes `RM-PILOT-*` and
      `ARF-QAFIX-*` fixtures.
- [ ] Enable the existing fixture-view toggle, without changing a record, and
      search `ARF-QAFIX-004`. Confirm its failed/degraded provider state is
      truthful and never shown as clear; no disposition action is offered.
- [ ] Search `ARF-QAFIX-006`, open the populated review, and confirm ranked hit
      evidence plus provider references render without loading/error state.
- [ ] Confirm current, historical, and stale evidence remain distinguishable;
      the case remains `pending_second_review`.
- [ ] In both queue and detail, confirm the exact second-review action set:
      one enabled **Clear as False Positive** action; **Confirm True Match**,
      **Escalate**, and **Request More Information** are absent. Do not click.
- [ ] Confirm Agent 3 output is labelled advisory and has not changed any
      disposition.

## 3. RSMP — 2 minutes

- [ ] Open **Risk Scoring Model** as administrator. This manual pass proves
      administrator access; non-admin denial remains covered by the protected
      automated authorization tests.
- [ ] Confirm the page is read-only: no editable input, Save, Apply, or
      client-side recompute control is present.
- [ ] In the browser Network panel, inspect the successful authenticated
      `GET /api/config/risk-model` response. Confirm the page uses that backend
      projection, including D1–D5 and its configuration source/version, with no
      loading or unavailable state.
- [ ] Inspect authenticated
      `GET /api/applications/RM-PILOT-039?show_fixtures=true`; confirm its
      `risk_report_evidence` and application-specific risk dimensions,
      composite score, floor/escalation reasons, approval route, and EDD route
      agree with Application Detail and remain `43.3 / MEDIUM`. Do not manually
      recompute the score.
- [ ] Confirm the application's configuration version/model policy reference
      agrees with the policy projection from `GET /api/config/risk-model`.
- [ ] Inspect `GET /api/config/risk-model`,
      `GET /api/config/environment`, and `GET /api/screening/status`; confirm:
      `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY=true`,
      `ENABLE_MONITORING_DASHBOARD=true`,
      `ENABLE_CA_RESCREEN=false`, and
      `ENABLE_CA_PROFILE_HYDRATION=false`.

## 4. Evidence and verdict — 1 minute

- [ ] Capture one redacted screenshot for Applications, Screening Queue,
      Screening Review, and Risk Scoring Model.
- [ ] Capture the preserved Console and Network result at the end of the run;
      record every material console error, failed API request, or unexpected
      4xx/5xx response. Expected result: none.
- [ ] Audit API request methods. After the single expected login POST, every
      protected-resource `/api/` request must be `GET`. Any `POST`, `PUT`,
      `PATCH`, or `DELETE`, or any upload, download, decision, disposition, or
      export endpoint request, is an immediate **FAIL**, even if it returns 2xx.
- [ ] Confirm no customer or fixture record was mutated and fixture records
      remain excluded after returning to the default queue view.

Founder verdict: `PASS` / `PASS WITH LIMITATION` / `FAIL`
Limitations or findings:

`PASS WITH LIMITATION` is allowed only for a non-behavioural evidence-format
limitation after every checkbox and evidence item was completed unambiguously
and the exact deployment gate passed. It may not excuse a skipped surface,
missing response, uncertain control state, console/network error, or missing
required screenshot.

Any P0, P1, P2, false-clear state, fixture leakage, mutable RSMP control, or
unexpected protected-module behaviour is **FAIL** and stops closure. Any
unchecked or ambiguous item is also **FAIL**.

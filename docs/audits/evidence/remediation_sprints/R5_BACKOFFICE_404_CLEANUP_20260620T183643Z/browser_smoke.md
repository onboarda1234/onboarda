# R5 — Browser Smoke

This fix changes only `arie-backoffice.html` (a static HTML `<head>` line). It is **not** exercisable by the Python/CI test suite, and this environment has no authenticated staging/browser access. The browser smoke below must be run with the patched HTML loaded against staging (same pattern as PR-PRS-D/F smokes).

## Pre-check (static, done here)
- Edit applied and confirmed present in `arie-backoffice.html` (`<link rel="icon" ...>` at the top of `<head>`).
- HTML well-formed: a single self-closing-style `<link>` inserted between `<title>` and the existing `<link rel="preconnect">`; no structural change.
- Data URI is a valid 1×1 transparent PNG (`data:image/png;base64,iVBORw0KGgo...`), no special characters that could break the attribute.

## Smoke checklist (run by operator/Codex on staging)
1. Load the back office (`/backoffice`) authenticated; open DevTools → Network + Console.
2. **No `GET /favicon.ico` 404** appears (the request should not be made at all, or be served from the inline icon).
3. **No new console errors** and **no other failed requests** introduced.
4. Back office **loads normally**; **login** and **navigation** (queue, application detail, lifecycle tab) still work.
5. Repeat on a sub-route (e.g. `/backoffice/supervisor`) — same result (same HTML).

## Expected result
All 5 pass: the `/backoffice` `/favicon.ico` 404 is gone; everything else behaves identically.

## Status
- Code fix: **applied**, root cause **confirmed (code-deterministic)**.
- Live browser smoke: **pending operator/Codex execution** on staging.

# R5 — /backoffice 404: Root Cause

**Classification:** missing favicon / browser default request (cosmetic, P2, not pilot-blocking).

## Exact failing request
- **URL:** `GET /favicon.ico`
- **HTTP status:** `404`
- **Initiator:** browser default — issued automatically by the browser for the `/backoffice` page.
- **Page context:** `/backoffice` (and its sub-routes `/backoffice/{kpis,supervisor,...}`, which serve the same HTML).

## How it was identified (code-deterministic)
The browser-audit artifacts live outside this environment, so the cause was confirmed by code inspection — which is deterministic for this class of issue:

1. `/backoffice` is served by `BackOfficeHandler`, returning `arie-backoffice.html`
   - `arie-backend/server.py:32534` `(r"/backoffice", BackOfficeHandler)`
   - `arie-backend/server.py:32535` `(r"/backoffice/(?:kpis|...|ai-agents)", BackOfficeHandler)`
   - `arie-backend/server.py:17067` opens `arie-backoffice.html`
2. **No favicon route exists** — `grep -ni favicon arie-backend/server.py` → no matches. Routes are only `/`, `/portal`, `/backoffice`, `/backoffice/<subpaths>`, `/static/(.*)`.
3. **The page declared no favicon** — `arie-backoffice.html` `<head>` contained only `<title>` + Google Fonts links; no `<link rel="icon">`.
4. **No other asset request in the page** could 404: 0 `<img>` tags, no `manifest`/`apple-touch-icon`, no `.png/.ico/.svg`, no `/static/` references. The only external resources are Google Fonts (`fonts.googleapis.com` / `fonts.gstatic.com`, which load 200) and `mailto:`/external `href`s (not resource loads).

⇒ With no declared favicon and no `/favicon.ico` route, the browser's automatic `GET /favicon.ico` returns 404 on every `/backoffice` load. This is the reported cosmetic console/network 404. It has **no functional impact**.

## Fix
Add a single self-contained inline favicon `<link>` to the back-office `<head>` (1×1 transparent PNG data URI). The browser uses the declared icon and no longer requests `/favicon.ico` → the 404 disappears.

- No backend change, no routing change, no new served file, no catch-all, no error suppression, no auth change.
- One HTML edit covers `/backoffice` and all sub-routes (same document).
- Visually unchanged (transparent icon == prior default behaviour, minus the failed request).

## Out of scope (noted, not changed)
The client portal (`/portal`) likely emits the same benign `/favicon.ico` 404 for the same reason. Per the R5 tiny-scope mandate it was **not** touched; logged as an optional future one-line cleanup.

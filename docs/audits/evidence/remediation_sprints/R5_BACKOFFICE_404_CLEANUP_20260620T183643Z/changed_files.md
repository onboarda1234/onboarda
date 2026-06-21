# R5 — Changed Files

| File | Change | Lines |
|------|--------|-------|
| `arie-backoffice.html` | Added one inline favicon `<link rel="icon">` (1×1 transparent PNG data URI) in `<head>`, just after `<title>` | +4 (1 link + 3 comment lines) |

## Diff (logical)
```html
<title>RegMind — Back Office</title>
<!-- R5: inline favicon suppresses the browser's default /favicon.ico request
     (no favicon route exists), removing the cosmetic 404 on /backoffice.
     Self-contained data URI; no backend/routing/visual change. -->
<link rel="icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=">
<link rel="preconnect" href="https://fonts.googleapis.com">
```

## What was NOT changed
- No backend / `server.py` route, static, or auth changes.
- No `/favicon.ico` route added; no redirect/alias; no catch-all.
- No global error suppression.
- No product logic, Periodic Review lifecycle, memo-gate, risk/scoring, screening, approval, or provider changes.
- Portal (`arie-portal.html`) untouched.

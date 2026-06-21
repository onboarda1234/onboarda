# R5 — Console Before / After

## Before (reported by browser audit)
```
[/backoffice] Failed to load resource: the server responded with a status of 404 ()
```
- Request: `GET /favicon.ico` → `404`
- No page errors; purely cosmetic console/network noise.

## After (expected — to be confirmed by browser smoke)
- The `<head>` now declares `<link rel="icon" href="data:image/png;base64,...">`.
- The browser uses the inline data-URI icon and **does not** issue `GET /favicon.ico`.
- Console shows **no** `/favicon.ico` 404 and **no** new errors.

## Why "after" is expected to be clean
Browsers only auto-request `/favicon.ico` when no icon is declared in the document. With an inline `data:` icon present, no network request is made for the favicon, so the 404 cannot occur. This is a standard, well-established behaviour and is not browser-version-specific for `data:` icons.

> Live confirmation is performed in the browser smoke (see `browser_smoke.md`), executed against staging where the page can actually be loaded.

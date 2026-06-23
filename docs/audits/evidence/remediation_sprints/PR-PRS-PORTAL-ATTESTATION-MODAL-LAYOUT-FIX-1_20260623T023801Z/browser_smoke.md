# Browser Smoke

Command:

```bash
node docs/audits/evidence/remediation_sprints/PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1_20260623T023801Z/logs/portal_attestation_modal_smoke.js
```

Mode:
- Served patched `arie-portal.html` locally.
- Routed local `/api/*` calls to `https://staging.regmind.co/api/*`.
- Created a disposable staging client/application/periodic-review fixture.
- Authenticated as the disposable client with a signed staging JWT.
- Opened the actual portal periodic-review task through the portal dashboard.
- Cleaned up fixture rows in `finally`.

Result: PASS

Checks:
- 1440x900: modal opened fully, title/questions/Yes-No controls visible, no left clipping, body scroll usable.
- 1280x800: modal not covered by sidebar, close button visible, body scroll usable.
- 1024x768: no horizontal clipping or page overflow, form usable.
- Close button: closes modal; sidebar and dashboard remain usable after close.
- Console/page errors: none.
- Bad API responses: none.

Geometry highlights:

| Viewport | Card left/right | Modal above sidebar | Horizontal overflow | Body scroll |
| --- | ---: | --- | --- | --- |
| 1440x900 | 260 / 1180 | yes (`modalZ=5000`, `sidebarZ=90`) | no (`scrollWidth=1440`) | yes |
| 1280x800 | 180 / 1100 | yes (`modalZ=5000`, `sidebarZ=90`) | no (`scrollWidth=1280`) | yes |
| 1024x768 | 52 / 972 | yes (`modalZ=5000`, `sidebarZ=90`) | no (`scrollWidth=1024`) | yes |

Screenshots:
- `screenshots/after-1440.png`
- `screenshots/after-1280.png`
- `screenshots/after-1024.png`

Raw report:
- `logs/browser_smoke.raw.json`
- `logs/portal_attestation_modal_smoke.js`

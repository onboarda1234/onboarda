# Test Results

Focused static test:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_portal_periodic_review_attestation_static.py -q
```

Result:

```text
4 passed in 0.33s
```

Related portal periodic-review static suite:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_portal_periodic_review_attestation_static.py \
  arie-backend/tests/test_portal_periodic_review_documents_static.py \
  arie-backend/tests/test_portal_periodic_review_notification_static.py \
  -q
```

Result:

```text
9 passed in 0.80s
```

Browser smoke:

```bash
node docs/audits/evidence/remediation_sprints/PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1_20260623T023801Z/logs/portal_attestation_modal_smoke.js
```

Result:

```text
ok: true
viewport_1440: true
viewport_1280: true
viewport_1024: true
close_button: true
no_console_errors: true
no_bad_api_responses: true
cleanup.cleaned: true
```

Smoke runner syntax:

```bash
node --check docs/audits/evidence/remediation_sprints/PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1_20260623T023801Z/logs/portal_attestation_modal_smoke.js
```

Result: PASS

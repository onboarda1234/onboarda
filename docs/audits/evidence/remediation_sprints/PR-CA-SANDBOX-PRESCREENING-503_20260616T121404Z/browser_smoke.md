# Browser Smoke

Pre-merge status: pending.

This file must be updated after the PR is merged to `main` and staging is redeployed.

Required post-deploy browser checks:

1. Confirm staging `/api/version` matches merged SHA.
2. Confirm `/api/screening/status` shows CA Sandbox or explicitly labelled safe mode.
3. Create one clean low-risk synthetic portal application.
4. Submit prescreening.
5. Confirm submit does not return 503.
6. Confirm application appears in back office.
7. Confirm screening status is visible and not false-clear if incomplete.
8. Confirm no console/network/server errors.


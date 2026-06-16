# API Smoke

Pre-merge status: pending.

This file must be updated after the PR is merged to `main` and staging is redeployed.

Required post-deploy checks:

- `/api/version`
- `/api/health`
- `/api/liveness`
- `/api/screening/status`
- `/api/screening/status?probe=1`
- One controlled clean synthetic portal prescreening submit


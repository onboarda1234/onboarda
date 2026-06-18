# Browser Smoke

Task: PR-PORTAL-PILOT-BOUNDARY-1

Timestamp UTC: 20260618T030901Z

Status: not run for closure.

Reason: the closure rule requires authenticated portal browser smoke after the PR is merged to `main` and staging is deployed from that merged SHA. This branch is local and not deployed.

Required post-deploy browser checks:

- Approved state is clean.
- Pre-approval state is clean.
- KYC state is clean.
- RMI state is clean.
- No client-visible risk, AI, high-risk, or internal control wording.
- No bad person identifier polling requests.
- No blocking console or network errors.

Local substitute evidence:

- Static rendered-state wording tests passed.
- Static routing tests prove pricing acceptance routes by backend status.
- Static polling tests prove invalid person identifiers are rejected before upload, link, and polling paths.

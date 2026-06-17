# Browser Smoke

Status: Pending post-merge staging deployment.

Required checks:

- `/api/version.git_sha` and `image_tag` match merge SHA.
- Reject empty reason is blocked.
- Reject valid reason persists and remains on KYC Documents.
- Re-Verify triggers verification and remains on KYC Documents.
- Top-level upload succeeds and remains on KYC Documents.
- Missing-slot upload succeeds and remains on KYC Documents where available.
- View opens inline/new tab and does not download.
- Download still uses the download endpoint.
- More dropdown closes on outside click.
- Classification A/B/C/D/E/F/G remains intact.
- No blocking console/network errors.

# PR-CA1 Staging Deploy

Status: pending.

This evidence cannot be completed before the PR is merged.

Required post-merge steps:

1. Pull latest `main`.
2. Record merged main SHA.
3. Deploy merged main to staging.
4. Confirm staging `/api/version` returns:
   - `git_sha` equal to merged main SHA.
   - `image_tag` equal to merged main SHA.
5. Run staging API smoke.
6. Run staging browser smoke because provider/status UI was touched.

Current verdict:

- CA-001: PARTIALLY FIXED, pending merged-main staging validation.
- CA-005: PARTIALLY FIXED, pending merged-main staging validation.
- CA-006: PARTIALLY FIXED, pending merged-main staging validation.
- CA-008 / CA-UX-012: PARTIALLY FIXED, pending merged-main staging validation.
- CA-012: PARTIALLY FIXED, pending merged-main staging validation.

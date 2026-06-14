# PR-CA3 Staging Deploy Evidence

Status: pending.

Required after merge:

1. Pull latest `main`.
2. Record merged main SHA.
3. Deploy merged main to staging.
4. Confirm deployment image/tag.
5. Confirm `/api/version` returns `git_sha` and `image_tag` equal to merged main SHA.

No staging closure has been claimed before merge/deploy validation.

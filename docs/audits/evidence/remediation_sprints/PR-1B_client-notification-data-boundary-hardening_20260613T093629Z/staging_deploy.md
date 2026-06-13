# PR-1B Staging Deploy Evidence

Not completed at branch stage.

Required before closing `FSI-001`:

1. PR-1B is merged into `main`.
2. Merged `main` is deployed to staging.
3. Staging `/api/version` reports `git_sha` and `image_tag` equal to the merged main SHA.
4. Staging API smoke proves `/api/notifications` no longer leaks internal/officer content.
5. Staging browser smoke proves client portal notifications no longer render internal/officer content.

Current closure status: `PARTIALLY FIXED`.

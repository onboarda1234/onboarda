# PR-1 Staging Deploy Evidence

Not completed in this branch-level PR preparation.

FSI-001 must not be marked `CLOSED` until:

1. PR-1 is reviewed and merged into `main`.
2. Merged `main` is deployed to staging.
3. Staging `/api/version` reports `git_sha` and `image_tag` equal to the merged main SHA.
4. Staging API smoke tests pass with client and back-office tokens.
5. Staging browser smoke tests pass where applicable.

Current closure status from this evidence pack: `PARTIALLY FIXED`.

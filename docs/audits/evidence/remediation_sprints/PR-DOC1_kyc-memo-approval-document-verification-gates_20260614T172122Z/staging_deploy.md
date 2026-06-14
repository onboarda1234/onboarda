# PR-DOC1 Staging Deploy

Branch-stage status: not deployed.

Required after PR merge:

1. Pull latest `main`.
2. Record merged main SHA.
3. Deploy merged main to staging.
4. Confirm `/api/version` returns `git_sha` and `image_tag` equal to the merged main SHA.

Current evidence:

- Merged main SHA: not available.
- Deployment mechanism: not run.
- Staging `/api/version`: not run.
- Verdict: DOC-001 cannot be marked `CLOSED` from branch-stage evidence.


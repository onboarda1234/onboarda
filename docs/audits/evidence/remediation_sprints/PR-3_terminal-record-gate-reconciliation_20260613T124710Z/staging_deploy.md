# Staging Deploy

## Branch Stage

Not yet applicable.

PR-3 has not been merged into main and has not been deployed to staging.

## Required Post-Merge Evidence

After PR-3 merge:

1. Pull latest main.
2. Record merged main SHA.
3. Deploy merged main to staging.
4. Capture ECS task/image evidence where available.
5. Confirm authenticated `/api/version` returns:
   - `git_sha` equal to merged main SHA
   - `image_tag` equal to merged main SHA
6. Save raw redacted `/api/version` response under `runtime_json/`.

FSI-003 must not be marked closed until this staging evidence exists.

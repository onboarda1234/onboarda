# PR-5 Staging Deploy Evidence

Branch-stage status: not deployed.

Required after merge:

1. Merge PR-5 into `main`.
2. Deploy merged `main` to staging.
3. Confirm `/api/version` returns:
   - `git_sha` equal to merged main SHA
   - `image_tag` equal to merged main SHA
4. Save raw redacted version output under `runtime_json/`.

FSI-005 and FSI-006 must remain `PARTIALLY FIXED` until this deployed-main proof exists.

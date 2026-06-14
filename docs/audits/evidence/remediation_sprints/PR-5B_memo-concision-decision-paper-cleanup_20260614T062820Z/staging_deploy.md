# Staging Deploy

Not yet performed at branch stage.

PR-5B remains incomplete until:

1. PR is merged into main.
2. Merged main is deployed to staging.
3. Staging `/api/version` returns `git_sha` and `image_tag` equal to merged main SHA.
4. Representative staging memo is generated/retrieved.
5. Staging PDF export succeeds.
6. Staging API/PDF smoke passes.
7. Staging browser smoke passes.

# PR-CA4C Staging Deploy Evidence

Status:

- Pending. This PR has not yet been merged to `main` or deployed to staging.

Required evidence after merge:

- Merged main SHA.
- Deployment mechanism.
- Deployment timestamp.
- Staging `/api/version` response proving:
  - `git_sha` equals merged main SHA.
  - `image_tag` equals merged main SHA.
- Staging API smoke result.
- Staging browser smoke result.


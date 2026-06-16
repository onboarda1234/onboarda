# API Smoke

Pending merged-`main` staging validation.

Required checks:

- authenticated `/api/version`
- `git_sha == merge_sha`
- `image_tag == merge_sha`
- core back-office endpoints return expected data

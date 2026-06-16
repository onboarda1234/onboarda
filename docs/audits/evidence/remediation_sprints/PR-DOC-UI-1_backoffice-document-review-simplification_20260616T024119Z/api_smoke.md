# API Smoke

Status: pending post-merge staging deployment.

Required after merge:

- Confirm `/api/version` returns merged main SHA for `git_sha`.
- Confirm `/api/version` returns the same merged main SHA for `image_tag`.
- Confirm portal document upload API succeeds without `documents_uploaded_by_fkey`.
- Confirm uploaded document verification status endpoint returns pending/running/verified/review-required without raw internal messages.


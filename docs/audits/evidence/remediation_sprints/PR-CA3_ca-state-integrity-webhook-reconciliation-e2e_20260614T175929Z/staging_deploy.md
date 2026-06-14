# PR-CA3 Staging Deploy Evidence

Status: deployed for merged PR #491; closure failed due API/runtime smoke contradiction.

Required after merge:

1. Pull latest `main`.
2. Record merged main SHA.
3. Deploy merged main to staging.
4. Confirm deployment image/tag.
5. Confirm `/api/version` returns `git_sha` and `image_tag` equal to merged main SHA.

## Merged PR #491 deployment

- PR: `https://github.com/onboarda1234/onboarda/pull/491`
- Merged main SHA: `9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca`
- Deployment workflow: `Deploy to Staging`
- GitHub Actions run: `https://github.com/onboarda1234/onboarda/actions/runs/27507930395`
- Deploy job result: PASS
- Backend ECS deployment: PASS
- Verification worker ECS deployment: PASS
- Deployment health check: PASS
- Portal/backoffice check: PASS

## /api/version

Authenticated staging `/api/version` returned:

```json
{
  "git_sha": "9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca",
  "image_tag": "9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca"
}
```

Verdict:

- `git_sha` equals merged main SHA: PASS
- `image_tag` equals merged main SHA: PASS

## Closure note

Deployment proof passed, but PR-CA3 closure did not pass because staging API/runtime smoke found application detail screening truth drift from the approval gate for material input changes after CA screening. See `api_smoke.md`.

Corrective branch pending: `codex/pr-ca3-corrective-input-staleness`.

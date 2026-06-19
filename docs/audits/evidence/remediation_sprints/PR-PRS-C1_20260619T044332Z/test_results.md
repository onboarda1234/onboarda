# PR-PRS-C1 Test Results

## Pre-Merge PR #540 Required Checks

```text
CodeRabbit	pass	0		Review completed
docker-validate	pass	52s	https://github.com/onboarda1234/onboarda/actions/runs/27803317020/job/82279546355	
lint-and-test	pass	17m7s	https://github.com/onboarda1234/onboarda/actions/runs/27803317020/job/82277931073	
pdf-tests	pass	37s	https://github.com/onboarda1234/onboarda/actions/runs/27803317020/job/82279546332
```

## Post-Merge Main CI

- Run: `27803878377`
- Head SHA: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Status: `completed` / `success`

| Job | Status | Conclusion | Started | Completed |
| --- | --- | --- | --- | --- |
| `lint-and-test` | `completed` | `success` | `2026-06-19T03:40:00Z` | `2026-06-19T03:55:50Z` |
| `docker-validate` | `completed` | `success` | `2026-06-19T03:55:52Z` | `2026-06-19T03:56:40Z` |
| `pdf-tests` | `completed` | `success` | `2026-06-19T03:55:52Z` | `2026-06-19T03:56:24Z` |

## Staging Deploy Workflow

- Run: `27803878415`
- Head SHA: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Status: `completed` / `success`

| Job | Status | Conclusion | Started | Completed |
| --- | --- | --- | --- | --- |
| `ci / lint-and-test` | `completed` | `success` | `2026-06-19T03:40:01Z` | `2026-06-19T03:57:57Z` |
| `ci / pdf-tests` | `completed` | `success` | `2026-06-19T03:58:01Z` | `2026-06-19T03:58:39Z` |
| `ci / docker-validate` | `completed` | `success` | `2026-06-19T03:57:59Z` | `2026-06-19T03:58:49Z` |
| `deploy` | `completed` | `success` | `2026-06-19T03:58:52Z` | `2026-06-19T04:19:33Z` |

## Staging Smoke Summary

- Authenticated `/api/version.git_sha`: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Authenticated `/api/version.image_tag`: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- API smoke scenarios: `5/5` passed
- Browser smoke: 5 screenshots captured from authenticated staging back office.
- Smoke result JSON: `logs/api_smoke_staging_results.json`

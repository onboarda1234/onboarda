# PR-PRS-B Test Results

Evidence timestamp: `2026-06-18T16:57:06Z`

## Local Validation

- Full suite: `cd arie-backend && python3.11 -m pytest tests/ -q`
- Result: `5564 passed, 17 skipped in 325.71s (0:05:25)`
- Log: `logs/pytest_full.log`

- Focused PR-PRS-B suite: `python3.11 -m pytest tests/test_pr_prs_b_evidence_gates.py -v`
- Result: `14 passed in 0.11s`
- Log: `logs/pytest_pr_prs_b_focused.log`

## GitHub Verification

- PR: `#537` (`feat/pr-prs-b-evidence-gates`)
- PR CI before merge: green
  - CodeRabbit: success
  - `lint-and-test`: success
  - `docker-validate`: success
  - `pdf-tests`: success
- Merge commit: `69effaafce6e14dd493497e692c290f69018dcb5`
- Main CI after merge: success
  - `lint-and-test`: success
  - `docker-validate`: success
  - `pdf-tests`: success
- Deploy to staging: success
  - Backend ECS rolling update: success
  - Verification worker ECS rolling update: success
  - Deployment health check: success
  - Portal/backoffice check: success

## Staging Version

- Endpoint: `https://staging.regmind.co/api/version`
- `git_sha`: `69effaafce6e14dd493497e692c290f69018dcb5`
- `image_tag`: `69effaafce6e14dd493497e692c290f69018dcb5`
- `environment`: `staging`


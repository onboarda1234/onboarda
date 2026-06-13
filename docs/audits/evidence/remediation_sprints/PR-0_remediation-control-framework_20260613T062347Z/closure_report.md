# PR Closure Report

## PR name

`PR-0 - Remediation Control Framework and Evidence Protocol`

## Linked remediation IDs

- None. PR-0 does not close any product defect.

## Original issue summary

The 2026-06-13 master remaining reconciliation found that RegMind remains NO-GO for paid pilot due to unresolved live-product issues, including client/API boundary failures, logout token revocation failure, approved-record gate inconsistency, screening readiness contradictions, memo governance gaps, Sumsub/IDV reconciliation risk, incomplete role-matrix proof, KYB provider uncertainty, and worker/runtime baseline mismatch.

PR-0 establishes the mandatory operating process, evidence structure, validation checklist, closure template, evidence-pack convention, remediation sequence, and helper script for future remediation PRs.

## Re-diagnosis result

- Current `origin/main` SHA: `b387898d26bf143bfd94489e7b5807f2ff6d5e62`
- Branch name: `codex/pr0-remediation-control-framework`
- Branch commit SHA: recorded in the PR description and final delivery output after this report is committed
- Does the issue still exist on current `origin/main`? Not applicable. PR-0 is process-only and intentionally does not reclassify or close product defects.
- Evidence: `git fetch origin` and branch creation from `origin/main` at `b387898d26bf143bfd94489e7b5807f2ff6d5e62`.

## Root cause

The remediation program needed an explicit closure discipline so future PRs cannot mark issues closed from branch-only code changes, local-only tests, stale staging behavior, or old audit evidence. The missing control was a process and evidence-governance gap, not a product-code defect.

## Files changed

- `docs/remediation/remaining-remediation-operating-protocol.md`
- `docs/remediation/pr-closure-template.md`
- `docs/remediation/remediation-pr-checklist.md`
- `docs/remediation/remaining-remediation-sequence.md`
- `docs/audits/evidence/remediation_sprints/README.md`
- `docs/audits/evidence/remediation_sprints/PR-0_remediation-control-framework_20260613T062347Z/closure_report.md`
- `scripts/remediation/create_evidence_pack.py`

## Behaviour before fix

There was no single mandatory protocol requiring every remediation PR to diagnose from current `origin/main`, prove closure only after merged-main staging deployment, confirm `/api/version`, run staging API/browser smoke tests where applicable, and preserve a standard evidence pack before marking issues closed.

## Behaviour after fix

Future remediation PRs have a documented mandatory lifecycle, severity-based definition of done, closure template, PR checklist, evidence-pack folder convention, initial PR sequence, and helper script for creating evidence-pack folders.

The protocol explicitly states that GitHub `origin/main` is the source of truth and that local, branch-only, stale, or old-staging evidence is insufficient for issue closure.

## Tests added/updated

- No product tests added. PR-0 is documentation/process only.
- Added `scripts/remediation/create_evidence_pack.py` helper for future evidence-pack setup.

## Targeted test results

Command:

```bash
git diff --check
```

Result:

```text
PASS
```

Command:

```bash
python3 scripts/remediation/create_evidence_pack.py PR-TEST sample-pack --base-dir /tmp/regmind-remediation-pack-smoke --timestamp 20260613T000000Z
```

Result:

```text
PASS - helper created the expected evidence-pack folder structure in /tmp
```

Command:

```bash
find . -maxdepth 4 \( -name '.markdownlint*' -o -name 'markdownlint.*' -o -name 'package.json' -o -name 'pyproject.toml' -o -name 'Makefile' \) -print | sort
```

Result:

```text
No markdown lint/check command is configured in the repository. `arie-backend/package.json` only defines a placeholder test script, and `arie-backend/pyproject.toml` does not configure markdown linting.
```

## Full suite results

Command:

```bash
Not run
```

Result:

```text
Not required for PR-0 because only documentation and a lightweight helper script changed.
```

## Browser test results, if applicable

Not applicable. PR-0 does not change product UI or workflows.

## Staging deploy evidence

- Merged main SHA: not applicable before PR merge
- Deployment mechanism: not applicable for PR-0 branch validation
- ECS/task/image evidence, if applicable: not applicable
- Deployed at: not applicable

PR-0 does not close product defects. Future defect-closing PRs must deploy merged `main` to staging and prove `/api/version` alignment before closure.

## /api/version evidence

Endpoint:

```text
Not applicable for PR-0 branch validation
```

Result:

```json
{
  "git_sha": "not_applicable",
  "image_tag": "not_applicable"
}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

Not checked for PR-0 because no product defect is being closed and the PR is not merged/deployed yet.

## API smoke test evidence

- Endpoint(s): not applicable
- Role/token type: not applicable
- Expected: not applicable
- Actual: not applicable
- Raw evidence path: not applicable

## Browser smoke test evidence, if applicable

- URL: not applicable
- Role: not applicable
- Expected: not applicable
- Actual: not applicable
- Screenshot path: not applicable
- Console/network notes: not applicable

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-0_remediation-control-framework_20260613T062347Z/`

## Remaining risks

- PR-0 creates the remediation control framework but does not fix FSI-001, FSI-002, FSI-003, FSI-007, FSI-005, FSI-006, FSI-011, FSI-012, KYCB, POST-INFRA, or any other product defect.
- Future PRs must comply with the protocol; the protocol alone does not make RegMind pilot-ready.

## Items not closed by this PR

- All live-product remediation items remain open unless closed by a future defect-specific PR with merged-main staging evidence.

## Final closure verdict

`NOT APPLICABLE`

Rationale:

PR-0 is a process-control PR. It closes no product defect and must not be used to mark any remediation item complete.

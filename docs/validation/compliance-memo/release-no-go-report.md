# Compliance Memorandum release gate failure (historical attempt)

> Superseded on 2026-08-01 after environment remediation. The authoritative
> post-review final-tree restart completed with 8,910 passed, 11 skipped, 4 expected
> failures, and zero failures/errors. This report is retained only as evidence
> of the correctly halted infrastructure-failure attempt.

Date: 2026-08-01

Branch: `codex/compliance-memo-workspace`

Baseline: `aa16a0f930534293ff7f82689d99a7a7b60bb9c9`
Verdict: **NO-GO**

## Failed validation

The mandatory full automated-test gate did not complete successfully.

- Collected: 8,883 tests (1 collection skip).
- State when stopped: 650 passed, 1 skipped, 1,930 setup/teardown errors.
- The cascade began at 7% and the run was stopped under the release instruction that any failed gate must halt merge and deployment.
- No final commit, push, pull request, merge, deployment, or staging mutation occurred.

## Root cause

The validation host ran out of filesystem space during SQLite fixture creation. The first concrete failure was:

```text
sqlite3.OperationalError: unable to open database file
OSError: [Errno 28] No space left on device
```

Pytest later failed while truncating its own capture file with the same `Errno 28`.

At diagnosis, the root filesystem reported only **121 MiB available** and **99% capacity used**. The disposable Python environment was 209 MiB; the PostgreSQL test database was only 7.6 MiB and was not the source of the capacity exhaustion. After deleting only the exact temporary renderer/virtual-environment directories created by this task, available space recovered to 344 MiB, which remains insufficient for a trustworthy 8,883-test run.

## Affected files and surfaces

No product-source defect is established by this failure.

The first error surfaced through:

- `arie-backend/tests/test_application_enhanced_requirements.py` (`enhanced_app_db` / `_fresh_db` fixture)
- `arie-backend/db.py` (`init_db` → `_ensure_company_registry_schema`)
- pytest capture teardown

The remaining errors are a resource-exhaustion cascade across later test fixtures, not evidence of 1,930 independent product regressions.

## Gates completed before failure

- Obsolete prior-title terminology audit: clean across tracked text sources.
- Draft and final sample PDF regeneration: passed and deterministic.
- PDF visual review: all six pages passed for title, hierarchy, watermark, final lock state, headers, footers, page breaks, tables, clipping, overflow, and overlap.
- Draft SHA-256: `1ac94cad698f2485dde34c6b10c906c07ff0c4338eab749d47a59859904505e4`.
- Final SHA-256: `3720b008f766d493621b0fe855d34c3fb63879a5d1b3c7dfe5584d761173cd18`.

Browser, PR/CI, merge, deployment, and post-deployment gates were deliberately not attempted after the automated-test failure.

## Recommended remediation

1. Free at least 10 GiB on the validation host, or run the release gate on a clean CI runner with equivalent PostgreSQL and PDF dependencies.
2. Recreate a clean Python environment and disposable SSL-enabled PostgreSQL test database.
3. Rerun all 8,883 tests to completion and require a zero-failure result.
4. Rerun focused workflow, approval, audit, browser, and PDF equivalence checks.
5. Only after every pre-merge gate passes: commit, push, open one PR, wait for required checks, merge to `main`, and allow the existing `Deploy to Staging` workflow to deploy the merge SHA.
6. Complete the authenticated staging workflow and preview/download byte-equivalence validation before issuing a GO verdict.

## GO / NO-GO recommendation

**NO-GO.** The implementation is not releasable until the full automated suite completes successfully in a non-exhausted environment. The available PDF evidence is positive, but it cannot substitute for the failed mandatory release gate.

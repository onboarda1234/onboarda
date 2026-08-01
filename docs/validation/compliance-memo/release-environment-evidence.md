# Release validation environment evidence

Initial remediation capture: `2026-08-01T05:53:06Z`

Final-tree gate restart capture: `2026-08-01T06:30:22Z`

| Evidence | Value |
|---|---|
| Execution mode | Remediated local validation host (approved alternative to a clean CI runner) |
| Host identity | `Aishas-MacBook-Air.local` / `Aisha’s MacBook Air (2)` |
| Operating system | macOS 26.3.1 (25D771280a), arm64 |
| Root filesystem | 228 GiB total; **10.14 GiB available** immediately before the authoritative final-tree gate |
| Memory | 8 GiB physical; 32% system-wide free at capture |
| Python | CPython 3.12.13 in `/tmp/regmind-release-venv` |
| Test framework | pytest 9.0.3 |
| PDF renderer | WeasyPrint 69.0 |
| PostgreSQL client/server | PostgreSQL 16.14 (Homebrew), SSL `on` |
| Disposable test database | `regmind_release_validation_20260801_r2` (fresh for the final-tree gate) |
| `TEST_POSTGRES_DSN` policy | Localhost database above, `sslmode=require`; password-free local role; DSN contains no secret |
| Branch | `codex/compliance-memo-workspace` |
| Working-tree baseline SHA | `c667f95ff8ae892bcc8cafe27efa1151cc7d92f6` |
| `origin/main` SHA | `c667f95ff8ae892bcc8cafe27efa1151cc7d92f6` |

## Environment remediation

Before remediation, the root filesystem had only 121 MiB available and the first release attempt failed with `Errno 28`.

The restart removed only named disposable caches: Google/Brave render caches, Codex/WhatsApp/Python/updater caches, and npm download caches. Browser profiles, credentials, repository data, Codex bundled runtimes, and Playwright binaries were preserved. Temporary validation directories and the failed-run database were also removed before rebuilding the clean environment.

Available space was verified at 12 GiB after dependencies and the disposable PostgreSQL database were created. No product source was changed to accommodate the infrastructure failure.

GitHub advanced from `aa16a0f` to `c667f95` during the first clean run. The implementation branch was fast-forwarded without conflict, a second fresh PostgreSQL database was created, available space was reverified at 10.14 GiB, and the complete gate was restarted from zero. The earlier green run was not used as the merge gate.

## Authoritative full automated gate

- Final source tree: `c667f95ff8ae892bcc8cafe27efa1151cc7d92f6` plus the uncommitted Compliance Memorandum changes.
- Runner identifier: `Aishas-MacBook-Air.local` / local validation run started `2026-08-01T06:30:22Z`.
- Database policy: PostgreSQL-only tests ran with `TEST_POSTGRES_DSN` against PostgreSQL 16.14 over SSL.
- Collected: 8,923 tests plus one collection-time policy skip.
- Result: **8,909 passed, 11 skipped, 4 expected failures, 0 failed, 0 errors**.
- Duration: 1,178.17 seconds (19m 38s).
- Infrastructure errors: none.
- Failed test names/root causes: none.
- Existing warnings: 312 deprecation warnings; non-failing and unrelated to this release scope.
- Raw output: `full-test-run.log` in this evidence directory.

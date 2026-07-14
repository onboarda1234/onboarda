# RSMP Tier 0A CI Isolation Fix

**Founder / accountable executive:** Aisha Sudally

**Approval date:** 2026-07-14

**Decision:** APPROVED; activation remains OFF; no production-readiness claim.

**Canonical Markdown SHA-256:** `e291a5d0fe71fd7c2aeb4afa4a6fdf93236d6dd383ffddf45992d3345427e201`

**Scope:** PR #753 test isolation only
**Status:** Draft / HOLD — no merge, deployment, activation, or recomputation

## Root cause

The full backend suite imports `risk_controlled_values` before tests that call `importlib.reload(environment)`. Reloading `environment` creates a new `environment.flags` singleton. The already-imported `risk_controlled_values` module retains its original `flags` singleton.

The Tier 0A mapping fixture changed only `environment.flags._cache`. Under full-suite ordering, the runtime accessor `mapping_fidelity_enabled()` read the other retained singleton, so it remained `False`. This produced seven fixture setup errors and one formatted-volume assertion failure. The focused suite passed because no preceding reload split the singleton identities.

## Test-only correction

The Tier 0A mapping test module now:

- identifies both live flag singleton identities without changing runtime code;
- sets the environment override and both singleton caches together when a test needs an explicit flag state;
- snapshots the activation environment variable and each cache entry before every test;
- restores their exact prior presence and value in a `finally` block after every test.

Runtime feature-flag resolution is unchanged. `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY` remains `False` by default in every environment.

## Canonical hash method

The recorded SHA-256 covers the entire UTF-8 Markdown file with LF line endings after replacing the value on the `Canonical Markdown SHA-256` line with the literal `{{CANONICAL_SHA256}}`.

## Validation

- Formerly failing ordered reproduction: `tests/test_environment.py` followed by `tests/test_rsmp_tier0a_mapping_fidelity.py` — 68 passed.
- Updated Tier 0A mapping and dry-run suites — 42 passed, including the 77 approved aliases, 105 quarantines, nine rejects, signed score contracts, unsolicited-referral isolation, OFF-by-default behavior, and canonical document hashes.
- Unchanged stacked PR #755 routing, mapping, and dry-run suites — 36 passed, including volume-specific, sector, PEP, and ownership isolation.
- Full backend suite excluding the separately executed PDF file — 6,998 passed, 49 skipped, and four expected failures; no test failure or setup error.
- Separate PDF suite — eight passed.
- `git diff --check` — passed.
- PostgreSQL service and Docker-image validation remain subject to the draft GitHub Actions run after the controlled PR #753 push; no local Docker daemon was available.

No runtime alias, scoring, schema, migration, configuration, or activation change is included in this correction. No production-readiness claim is made.

# RSMP Tier 0A CI Isolation Fix

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

## Validation

- Formerly failing ordered reproduction: `tests/test_environment.py` followed by `tests/test_rsmp_tier0a_mapping_fidelity.py` — 68 passed.
- Tier 0A dry-run and mapping suites together — 25 passed.
- Full backend suite and GitHub Actions result are recorded in PR #753 after execution.

No runtime alias, scoring, schema, migration, configuration, or activation change is included in this correction. No production-readiness claim is made.

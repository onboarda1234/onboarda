# Feature-Flag Lifecycle Governance (RDI-023)

**Status:** Active · **Introduced:** 2026-07-26 · **Owner:** Platform Engineering

## Why this exists

Register item **RDI-023** (MEDIUM): feature flags carried no owner, introduction
version, expiry or sunset state, so a *permanent environment differentiator*
(config that will always exist to separate demo / staging / production) was
indistinguishable from a *temporary rollout flag* (one that should be deleted
once fully shipped or abandoned). Left unmanaged, temporary flags rot into
permanent-looking dead conditionals that nobody dares remove.

## The registry

`arie-backend/environment.py` declares `FLAG_LIFECYCLE`, mapping **every
governed flag** (see `all_governed_flags()`) to four attributes. The governed
surface is the union of two resolution paths:

- **FeatureFlags-declared flags** — the `_DEFAULT_FLAGS` per-environment table
  resolved by `FeatureFlags` (`all_declared_flags()`).
- **Externally-resolved flags** — `ENABLE_*` / `*_ENABLED` flags that sibling
  modules read directly via `os.environ.get()` rather than through
  `FeatureFlags` (`_EXTERNALLY_RESOLVED_FLAGS`), e.g. the ComplyAdvantage
  screening toggles in `screening_config.py`, the AI gates in `config.py`,
  `ENABLE_CLAUDE_MEMO`, and the monitoring/periodic-review schedulers. These are
  registered so the registry covers the whole flag surface, not just the half
  `FeatureFlags` owns — `environment.py` does not resolve them.

Each entry carries:

| Field | Meaning |
|-------|---------|
| `owner` | Functional-domain owner (e.g. `platform`, `compliance`, `kyc`, `screening`, `ai-pipeline`, `demo-tooling`). |
| `introduced` | Version/point the flag was added. `pre-registry` = predates this registry; exact version not backfilled. |
| `classification` | `permanent` (config differentiator, no sunset) or `temporary` (rollout / experiment / deliberate activation). |
| `sunset` | For `temporary` flags: the condition that ends the flag's life. Must be `None` for `permanent` flags. |

### Classification rule of thumb

- **`permanent`** — the flag exists to make demo/staging/production behave
  differently and always will (e.g. `ENABLE_SUMSUB_LIVE`, `ENABLE_REAL_SCREENING`,
  `ENABLE_DEBUG_ENDPOINTS`). These are config, not debt.
- **`temporary`** — the flag gates a rollout, an experiment, or a deliberate
  activation and should eventually be deleted (e.g. the `FF_*` upload-latency
  flags, the pilot-scope-vetoed enterprise modules, `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY`).
  Every temporary flag **must** state its sunset condition.

## It is pure metadata — no behaviour change

Nothing in `FLAG_LIFECYCLE` is read by `FeatureFlags` resolution,
`get_environment_info()`, or any request path. Flag *values* resolve exactly as
before (environment default → env-var override). The registry cannot change
runtime behaviour or the `/api/config/environment` client contract, so the
frozen Application Review and change-controlled Screening surfaces that read
flags are untouched.

## Enforcement

`arie-backend/tests/test_feature_flag_lifecycle.py` fails CI if:

1. any governed flag has no `FLAG_LIFECYCLE` entry (a new flag can't merge
   unclassified);
2. the registry references a flag nothing governs (stale entry after a removal);
3. an entry is missing `owner`, `introduced`, or a valid `classification`;
4. a `temporary` flag has no sunset condition, or a `permanent` flag declares one;
5. a pilot-scope-vetoed enterprise module is classified anything but `temporary`;
6. the lifecycle metadata leaks into the client `/api/config/environment`
   response, or is consulted by flag resolution;
7. an entry in `_EXTERNALLY_RESOLVED_FLAGS` is fictional (not referenced by any
   sibling module) or is actually a `FeatureFlags`-declared flag.

## Adding a flag

1. Add the flag as usual: either a default in `_DEFAULT_FLAGS` (and any
   specialised tuple, e.g. `UPLOAD_LATENCY_FLAGS`), **or** — for a flag a
   sibling module reads directly via `os.environ.get()` — add its name to
   `_EXTERNALLY_RESOLVED_FLAGS`.
2. Add a `FLAG_LIFECYCLE` entry via the `_perm(...)` or `_temp(...)` helper.
   Record a real `introduced` version for new flags rather than the
   `pre-registry` sentinel.
3. For a `temporary` flag, write a concrete sunset condition — the event or
   milestone at which the flag (and its conditional) is removed.

## Retiring a temporary flag

When a temporary flag's sunset condition is met, delete the flag from its
resolution site (`_DEFAULT_FLAGS` or the sibling module + its entry in
`_EXTERNALLY_RESOLVED_FLAGS`), remove its `FLAG_LIFECYCLE` entry, and delete the
now-dead conditional in code. The orphan-entry guard (rule 2) keeps the registry
honest if the entry is forgotten.

## Backfill honesty note

`owner` values were assigned by functional domain at registry creation, inferred
from each flag's purpose — they are not named individuals and should be
confirmed as team ownership is formalised. `introduced` is `pre-registry` for
all existing flags: they predate this registry and were not individually
archaeologised. Neither shortcut weakens the core RDI-023 outcome — permanent
config is now distinguishable from temporary rollout debt, and no flag can be
added without that distinction being made.

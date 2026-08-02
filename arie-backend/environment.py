"""
Onboarda Platform — Environment Configuration & Feature Flags
=========================================================
Single codebase, strict separation between Demo, Staging, and Production.
"""

import os
import sys
import logging

logger = logging.getLogger("arie.environment")

UPLOAD_LATENCY_FLAGS = (
    "FF_POLLING_SLOW",
    "FF_SIZE_CAP_CLIENT_REJECT",
    "FF_UX_SPLIT_UPLOAD_VERIFY",
    "FF_UPLOAD_ASYNC",
    "FF_ASYNC_VERIFY",
    "FF_GATE03_INDEXED_DEDUP",
    "FF_PRESIGNED_UPLOAD",
)

CLIENT_SAFE_UPLOAD_LATENCY_FLAGS = (
    "FF_SIZE_CAP_CLIENT_REJECT",
    "FF_UX_SPLIT_UPLOAD_VERIFY",
)

_UPLOAD_LATENCY_DEFAULTS = {flag: False for flag in UPLOAD_LATENCY_FLAGS}

# PR-MON-FEATURE-FLAGS-1 introduced these governed Monitoring switches.  The
# document-renewal request service is the first deliberately approved consumer:
# it gates every request/reminder/upload mutation and still defaults OFF in
# every environment.  The remaining three switches remain governance-only.
MONITORING_FEATURE_DEFINITIONS = (
    (
        "ENABLE_DOCUMENT_RENEWAL_AUTOMATION",
        "Document Renewal",
        "Document Renewal",
    ),
    (
        "ENABLE_AGENT1_REFRESH_VERIFICATION",
        "Agent 1 Refresh Verification",
        "Agent 1",
    ),
    (
        "ENABLE_MONITORING_SCREENING_CHANGE",
        "Screening Monitoring",
        "Screening Monitoring",
    ),
    (
        "ENABLE_MONITORING_AUTO_RESOLUTION",
        "Automatic Resolution",
        "Automatic Closure",
    ),
)

MONITORING_FEATURE_FLAGS = tuple(
    flag for flag, _label, _consumer in MONITORING_FEATURE_DEFINITIONS
)

_MONITORING_FEATURE_DEFAULTS = {
    flag: False for flag in MONITORING_FEATURE_FLAGS
}

# RSMP Tier 0A scoring changes must be activated deliberately after the
# staging config diff and read-only historical dry run have been approved.
RSMP_TIER0A_ACTIVATION_FLAG = "ENABLE_RSMP_TIER0A_MAPPING_FIDELITY"

# ══════════════════════════════════════════════════════════════
# 1. ENVIRONMENT DETECTION
# ══════════════════════════════════════════════════════════════

VALID_ENVIRONMENTS = ("development", "testing", "demo", "staging", "production")

# Common aliases, canonicalized BEFORE validation (audit H8 / PR-13).
# Previously ENVIRONMENT=prod was rejected here and fell back to
# 'development' — silently stripping a production box of every production
# safety gate (validate_config, validate_environment, PII checks) while
# other modules still treated the raw string as production. Aliasing to the
# canonical name is the fail-safe direction.
_ENV_ALIASES = {
    "prod": "production",
    "stage": "staging",
    "dev": "development",
    "test": "testing",
}


def canonicalize_environment(raw) -> str:
    """Return the canonical environment name for any raw ENVIRONMENT value.

    Lowercases/strips and maps known aliases (prod→production, stage→staging,
    dev→development, test→testing). This is the single source of truth for
    environment-name normalization — config.py uses it too, so
    config.ENVIRONMENT and environment.ENV can never disagree again.

    RDI-001 (CRITICAL, fail-closed): a MISSING/empty value defaults to
    'development' (the safe local-dev default, preserved). But a NON-EMPTY
    value that is not a valid environment even after alias resolution is a
    MISCONFIGURATION — previously it was logged and silently coerced to
    'development', which on a deployed box silently stripped every
    production-only guard (mock-mode block, required real API keys, real
    screening, forbidden-flag checks) while leaving a typo like
    ENVIRONMENT='producton' looking intentional. There is no legitimate use
    for an unrecognised non-empty environment name, so refuse to start rather
    than boot with the wrong safety profile. This runs at import time
    (environment.ENV and config.ENVIRONMENT), so a bad value is fatal before
    any request is served — matching config.py's sys.exit(1) precedent for a
    missing production JWT_SECRET.
    """
    # Missing/empty -> safe local-dev default (unchanged).
    if raw is None or str(raw).strip() == "":
        return "development"

    env = str(raw).lower().strip()
    env = _ENV_ALIASES.get(env, env)
    if env not in VALID_ENVIRONMENTS:
        logger.critical(
            "FATAL: Invalid ENVIRONMENT=%r — not one of %s (after alias "
            "resolution). A misconfigured environment name silently strips "
            "production safety guards; refusing to start. Set ENVIRONMENT/ENV "
            "to a valid value.",
            env, VALID_ENVIRONMENTS,
        )
        sys.exit(1)
    return env


def get_environment() -> str:
    """Get current environment from ENV variable. Defaults to 'development' for safety."""
    return canonicalize_environment(os.environ.get("ENVIRONMENT") or os.environ.get("ENV"))

ENV = get_environment()

def is_development() -> bool:
    return ENV == "development"


def is_testing() -> bool:
    """Returns True when running automated tests with ENVIRONMENT=testing."""
    return ENV == "testing"

def is_demo() -> bool:
    return ENV == "demo"

def is_staging() -> bool:
    return ENV == "staging"

def is_production() -> bool:
    return ENV == "production"


# ══════════════════════════════════════════════════════════════
# 2. FEATURE FLAGS
# ══════════════════════════════════════════════════════════════

# Default flags per environment
_DEFAULT_FLAGS = {
    "development": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": True,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_SAR_STR": False,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_SUPERVISOR_DASHBOARD": False,
        "ENABLE_SUPERVISOR_AUDIT": False,
        "ENABLE_KPI_DASHBOARD": False,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": False,
        "ENABLE_SUMSUB_SANDBOX": True,
        "ENABLE_REAL_SCREENING": False,
        "ENABLE_SIMULATED_SCREENING": True,
        "REQUIRE_REAL_API_KEYS": False,
        "ENABLE_DEBUG_ENDPOINTS": True,
        "ENABLE_SHORTCUT_LOGIN": True,
    },
    "demo": {
        "ENABLE_DEMO_MODE": True,
        "ENABLE_DEMO_BANNER": True,
        "ENABLE_DEMO_DATA_SEEDING": True,
        "ENABLE_MOCK_FALLBACKS": True,
        "ENABLE_ROLE_SWITCHER": True,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_SAR_STR": False,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_SUPERVISOR_DASHBOARD": False,
        "ENABLE_SUPERVISOR_AUDIT": False,
        "ENABLE_KPI_DASHBOARD": False,
        "ENABLE_KPI_DEMO_DATA": True,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": False,  # Use sandbox in demo
        "ENABLE_SUMSUB_SANDBOX": True,
        "ENABLE_REAL_SCREENING": False,
        "ENABLE_SIMULATED_SCREENING": True,
        "REQUIRE_REAL_API_KEYS": False,
        "ENABLE_DEBUG_ENDPOINTS": True,
        "ENABLE_SHORTCUT_LOGIN": True,
    },
    "staging": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": False,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": False,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": False,
        "ENABLE_SAR_STR": False,
        "ENABLE_AI_SUPERVISOR": False,
        "ENABLE_SUPERVISOR_DASHBOARD": False,
        "ENABLE_SUPERVISOR_AUDIT": False,
        "ENABLE_KPI_DASHBOARD": False,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": True,
        "ENABLE_SUMSUB_SANDBOX": False,
        "ENABLE_REAL_SCREENING": True,
        "ENABLE_SIMULATED_SCREENING": False,
        "REQUIRE_REAL_API_KEYS": True,
        "ENABLE_DEBUG_ENDPOINTS": False,
        "ENABLE_SHORTCUT_LOGIN": False,
    },
    "production": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": False,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": False,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": False,
        "ENABLE_SAR_STR": False,
        "ENABLE_AI_SUPERVISOR": False,
        "ENABLE_SUPERVISOR_DASHBOARD": False,
        "ENABLE_SUPERVISOR_AUDIT": False,
        "ENABLE_KPI_DASHBOARD": False,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": True,
        "ENABLE_SUMSUB_SANDBOX": False,
        "ENABLE_REAL_SCREENING": True,
        "ENABLE_SIMULATED_SCREENING": False,
        "REQUIRE_REAL_API_KEYS": True,
        "ENABLE_DEBUG_ENDPOINTS": False,
        "ENABLE_SHORTCUT_LOGIN": False,
    },
}

_DEFAULT_FLAGS["testing"] = dict(_DEFAULT_FLAGS["development"])
for _env_flags in _DEFAULT_FLAGS.values():
    _env_flags.update(_UPLOAD_LATENCY_DEFAULTS)
    _env_flags.update(_MONITORING_FEATURE_DEFAULTS)
    _env_flags.setdefault(RSMP_TIER0A_ACTIVATION_FLAG, False)


class FeatureFlags:
    """
    Feature flag manager with environment defaults and env var overrides.

    Resolution order:
    1. Environment variable (e.g. ENABLE_DEMO_MODE=true)
    2. Default for current environment
    """

    def __init__(self, env: str = None):
        self._env = env or ENV
        self._defaults = _DEFAULT_FLAGS.get(self._env, _DEFAULT_FLAGS["demo"])
        self._cache = {}
        self._load_all()

    def _load_all(self):
        """Load all flags with env var overrides."""
        for flag, default in self._defaults.items():
            env_val = os.environ.get(flag)
            if env_val is not None:
                self._cache[flag] = env_val.lower() in ("true", "1", "yes", "on")
            else:
                self._cache[flag] = default

    def is_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        if flag in self._cache:
            return self._cache[flag]
        # Check env var for unknown flags
        env_val = os.environ.get(flag)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes", "on")
        return False

    def get_all(self) -> dict:
        """Return all flags as a dictionary (safe for API response)."""
        return dict(self._cache)

    def get_client_safe_flags(self) -> dict:
        """Return flags safe to expose to frontend.

        Item 33: enterprise flags are reported through the pilot-scope veto,
        so the client never believes a module is available while every one of
        its routes 403s. Without this the frozen Application Review supervisor
        tab would call the API and paint a load error instead of its
        Coming Soon card.
        """
        safe_keys = [
            "ENABLE_DEMO_MODE", "ENABLE_DEMO_BANNER", "ENABLE_PHASE2_FEATURES",
            "ENABLE_REGULATORY_INTELLIGENCE_FULL", "ENABLE_MONITORING_DASHBOARD",
            "ENABLE_SAR_WORKFLOW", "ENABLE_SAR_STR", "ENABLE_AI_SUPERVISOR",
            "ENABLE_SUPERVISOR_DASHBOARD", "ENABLE_SUPERVISOR_AUDIT",
            "ENABLE_KPI_DASHBOARD",
            "ENABLE_KPI_DEMO_DATA",
            "ENABLE_ROLE_SWITCHER", "ENABLE_DOCUMENT_AI_ANALYSIS",
        ]
        safe_keys.extend(CLIENT_SAFE_UPLOAD_LATENCY_FLAGS)
        resolved = {k: self._cache.get(k, False) for k in safe_keys}
        if pilot_scope_active(self._env):
            for flag in _PILOT_SCOPE_VETOED_FLAGS:
                if flag in resolved:
                    resolved[flag] = False
        return resolved

    def get_upload_latency_client_flags(self) -> dict:
        """Return the exact upload-latency flag allowlist safe for clients."""
        return {k: self._cache.get(k, False) for k in CLIENT_SAFE_UPLOAD_LATENCY_FLAGS}


# Singleton
flags = FeatureFlags()


def get_monitoring_feature_state(feature_flags=None) -> dict:
    """Return the evaluated, governance-only Monitoring feature state."""
    resolved_flags = feature_flags or flags
    return {
        flag: resolved_flags.is_enabled(flag)
        for flag in MONITORING_FEATURE_FLAGS
    }


def get_monitoring_feature_status(feature_flags=None) -> dict:
    """Return the read-only operator view for Monitoring feature governance."""
    state = get_monitoring_feature_state(feature_flags)
    return {
        "read_only": True,
        "activation_controls": False,
        "features": {
            flag: {
                "label": label,
                "enabled": state[flag],
                "state": "ON" if state[flag] else "OFF",
            }
            for flag, label, _consumer in MONITORING_FEATURE_DEFINITIONS
        },
    }


_monitoring_feature_state_logged = False


def log_monitoring_feature_state_once(feature_flags=None) -> bool:
    """Log one startup record containing every evaluated Monitoring flag."""
    global _monitoring_feature_state_logged
    if _monitoring_feature_state_logged:
        return False

    state = get_monitoring_feature_state(feature_flags)
    labels = {
        flag: label
        for flag, label, _consumer in MONITORING_FEATURE_DEFINITIONS
    }
    summary = "; ".join(
        f"{labels[flag]}: {'ON' if state[flag] else 'OFF'}"
        for flag in MONITORING_FEATURE_FLAGS
    )
    logger.info("Monitoring Feature Flags — %s", summary)
    _monitoring_feature_state_logged = True
    return True


# ══════════════════════════════════════════════════════════════
# 2b. PILOT SCOPE GUARD (register item 33)
# ══════════════════════════════════════════════════════════════
# Enterprise-scope modules that are gated by a feature flag (SAR/STR,
# Regulatory Intelligence, AI Compliance Supervisor, Supervisor Audit) must not
# be reachable in a pilot deployment. Roadmap agents 8-10 are blocked
# unconditionally elsewhere and KPI/Supervisor-Dashboard have no server route,
# so neither needs — or gets — this veto. Today that rests on per-module
# feature flags whose deployed values live outside version control
# (render.yaml pins three of them `sync: false`, i.e. hand-set in a hosting
# dashboard). PILOT_SCOPE is the veto ABOVE those flags: when active, the
# enterprise modules stay disabled no matter what any individual flag says.
#
# Defaults are behaviour-preserving: ON for staging/production (where every
# enterprise flag already defaults False, so this is a no-op that merely makes
# the exclusion enforceable rather than conventional) and OFF for
# development/demo/testing (which deliberately enable some of those modules).
_PILOT_SCOPE_DEFAULTS = {
    "development": False,
    "demo": False,
    "testing": False,
    "staging": True,
    "production": True,
}

# Deliberately NOT prefixed ENABLE_ — this is a scope veto, not a feature
# toggle, and the pilot-lockdown tests assert no "ENABLE_" substring leaks
# into the disabled-module response bodies.
PILOT_SCOPE_VAR = "PILOT_SCOPE"

# The client-visible flags the veto suppresses, so /api/config/environment
# never advertises a module whose routes refuse to serve.
_PILOT_SCOPE_VETOED_FLAGS = (
    "ENABLE_SAR_WORKFLOW",
    "ENABLE_SAR_STR",
    "ENABLE_REGULATORY_INTELLIGENCE_FULL",
    "ENABLE_AI_SUPERVISOR",
    "ENABLE_SUPERVISOR_DASHBOARD",
    "ENABLE_SUPERVISOR_AUDIT",
)


def pilot_scope_active(env: str = None) -> bool:
    """True when enterprise-scope modules must be refused regardless of flags.

    Fail-closed parsing (the inverse of FeatureFlags): the guard turns OFF only
    on an explicit, well-formed disable value. A typo (`PILOT_SCOPE=flase`) or
    any unrecognised string leaves the guard ON, because the failure mode of a
    stuck-on guard is a refused enterprise module, while the failure mode of a
    stuck-off guard is an exposed one.
    """
    raw = os.environ.get(PILOT_SCOPE_VAR)
    if raw is not None:
        normalised = raw.strip().lower()
        if normalised in ("false", "0", "no", "off"):
            return False
        if normalised not in ("true", "1", "yes", "on"):
            # Coercing silently is what makes a typo dangerous elsewhere; say
            # so. The value still resolves ON (fail-closed), so this is a
            # warning, not the fatal treatment canonicalize_environment gives
            # an unrecognised ENVIRONMENT.
            logger.warning(
                "Unrecognised %s=%r — treating as ACTIVE (enterprise modules "
                "refused). Use an explicit 'false' to disable the guard.",
                PILOT_SCOPE_VAR, raw,
            )
        return True
    return _PILOT_SCOPE_DEFAULTS.get(env or ENV, True)


def get_backoffice_runtime_config() -> dict:
    """Return safe runtime settings derived from backend-only flags."""
    polling_slow = flags.is_enabled("FF_POLLING_SLOW")
    return {
        "applications_refresh_ms": 120000 if polling_slow else 30000,
        "applications_stale_threshold_s": 180 if polling_slow else 60,
    }


# ══════════════════════════════════════════════════════════════
# 2c. FEATURE-FLAG LIFECYCLE REGISTRY (register item RDI-023)
# ══════════════════════════════════════════════════════════════
# RDI-023: without lifecycle metadata a PERMANENT environment differentiator
# (config that will always exist to separate demo/staging/production) is
# indistinguishable from a TEMPORARY rollout flag (one that should be deleted
# once fully shipped or abandoned). Over time the temporary ones rot into
# permanent-looking dead conditionals that nobody dares remove.
#
# This registry attaches, to EVERY governed flag — the FeatureFlags-declared
# flags AND the flags sibling modules resolve directly via os.environ.get()
# (see all_governed_flags() / _EXTERNALLY_RESOLVED_FLAGS) — the four attributes
# the audit asked for: owner, the point it was introduced, a lifecycle
# classification, and — for temporary flags — the sunset condition that ends
# its life.
#
# It is PURE METADATA. Nothing here is read by FeatureFlags resolution, by
# get_environment_info(), or by any request path, so it cannot change runtime
# behaviour or the client contract. Its teeth are a guard test
# (test_feature_flag_lifecycle.py) that fails if a flag has no entry, or a
# temporary flag has no sunset condition — so a new flag cannot merge without
# being classified.
#
# Honesty notes:
#   * `owner` is a FUNCTIONAL-DOMAIN owner inferred from each flag's purpose at
#     registry creation, not a named individual — confirm/adjust as team
#     ownership is formalised.
#   * `introduced` is INTRODUCED_PRE_REGISTRY for every existing flag: these
#     predate the registry and their exact introduction version was not
#     archaeologised during the RDI-023 backfill. New flags should record a
#     real version/date.

FLAG_PERMANENT = "permanent"   # environment/config differentiator — no sunset
FLAG_TEMPORARY = "temporary"   # staged rollout / experiment / deliberate
                               # activation — MUST carry a removal condition

_FLAG_CLASSIFICATIONS = (FLAG_PERMANENT, FLAG_TEMPORARY)

# Sentinel: the flag predates this registry; exact introduction not backfilled.
INTRODUCED_PRE_REGISTRY = "pre-registry"


def _perm(owner, notes):
    return {"owner": owner, "introduced": INTRODUCED_PRE_REGISTRY,
            "classification": FLAG_PERMANENT, "sunset": None, "notes": notes}


def _temp(owner, sunset, notes, *, introduced=INTRODUCED_PRE_REGISTRY):
    return {"owner": owner, "introduced": introduced,
            "classification": FLAG_TEMPORARY, "sunset": sunset, "notes": notes}


FLAG_LIFECYCLE = {
    # ── Permanent environment/config differentiators (demo vs real behaviour) ──
    "ENABLE_DEMO_MODE": _perm("demo-tooling", "Master demo toggle; permanent env split."),
    "ENABLE_DEMO_BANNER": _perm("demo-tooling", "Demo UI banner; permanent env split."),
    "ENABLE_DEMO_DATA_SEEDING": _perm("demo-tooling", "Seed demo fixtures; forbidden in prod."),
    "ENABLE_MOCK_FALLBACKS": _perm("platform", "Mock external providers; forbidden in prod."),
    "ENABLE_ROLE_SWITCHER": _perm("demo-tooling", "Dev/demo role switch; forbidden in prod."),
    "ENABLE_MONITORING_DASHBOARD": _perm("compliance", "Monitoring surface; on in every env."),
    "ENABLE_DOCUMENT_AI_ANALYSIS": _perm("ai-pipeline", "Document AI checks; on in every env."),
    "ENABLE_SUMSUB_LIVE": _perm("kyc", "Live Sumsub vs sandbox; permanent env split."),
    "ENABLE_SUMSUB_SANDBOX": _perm("kyc", "Sandbox Sumsub; forbidden in prod."),
    "ENABLE_REAL_SCREENING": _perm("screening", "Real AML screening; permanent env split."),
    "ENABLE_SIMULATED_SCREENING": _perm("screening", "Simulated screening; forbidden in prod."),
    "REQUIRE_REAL_API_KEYS": _perm("platform", "Require real credentials; permanent env split."),
    "ENABLE_DEBUG_ENDPOINTS": _perm("platform", "Debug endpoints; forbidden in prod."),
    "ENABLE_SHORTCUT_LOGIN": _perm("demo-tooling", "Shortcut login; forbidden in prod."),
    "ENABLE_KPI_DEMO_DATA": _perm("demo-tooling", "KPI demo data; forbidden in prod."),

    # ── Temporary: umbrella rollout, now on in every env (retirement candidate) ──
    "ENABLE_PHASE2_FEATURES": _temp(
        "platform", "Retire once Phase 2 is the unconditional baseline (already True in every env).",
        "Umbrella rollout flag, now on everywhere; a retirement candidate."),

    # ── Temporary: enterprise modules, pilot-scope vetoed (see
    #    _PILOT_SCOPE_VETOED_FLAGS) — gated, not yet GA ──
    "ENABLE_REGULATORY_INTELLIGENCE_FULL": _temp(
        "compliance", "Remove gate at Regulatory Intelligence enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),
    "ENABLE_SAR_WORKFLOW": _temp(
        "compliance", "Remove gate at SAR/STR enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),
    "ENABLE_SAR_STR": _temp(
        "compliance", "Remove gate at SAR/STR enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),
    "ENABLE_AI_SUPERVISOR": _temp(
        "ai-pipeline", "Remove gate at AI Supervisor enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),
    "ENABLE_SUPERVISOR_DASHBOARD": _temp(
        "ai-pipeline", "Remove gate at Supervisor Dashboard enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),
    "ENABLE_SUPERVISOR_AUDIT": _temp(
        "ai-pipeline", "Remove gate at Supervisor Audit enterprise GA.",
        "Enterprise module; pilot-scope vetoed."),

    # ── Temporary: enterprise module, not yet routed to pilot (NOT vetoed) ──
    "ENABLE_KPI_DASHBOARD": _temp(
        "compliance", "Remove gate once the KPI dashboard ships to pilot.",
        "Enterprise module; no server route yet."),

    # ── Temporary: governed Monitoring workflows ──
    "ENABLE_DOCUMENT_RENEWAL_AUTOMATION": _temp(
        "compliance",
        "Remove the gate after Document Renewal automation reaches approved GA.",
        "Document Renewal request workflow; every mutation is fail-closed "
        "behind this flag and the default remains OFF.",
        introduced="PR-MON-FEATURE-FLAGS-1; first consumer "
        "PR-MON-DOC-RENEWAL-REQUEST-1",
    ),
    "ENABLE_AGENT1_REFRESH_VERIFICATION": _temp(
        "compliance",
        "Remove the gate after Agent 1 refresh verification reaches approved GA.",
        "Future Agent 1 consumer; no workflow is wired in this PR.",
        introduced="PR-MON-FEATURE-FLAGS-1",
    ),
    "ENABLE_MONITORING_SCREENING_CHANGE": _temp(
        "screening",
        "Remove the gate after Screening Monitoring reaches approved GA.",
        "Future Screening Monitoring consumer; no workflow is wired in this PR.",
        introduced="PR-MON-FEATURE-FLAGS-1",
    ),
    "ENABLE_MONITORING_AUTO_RESOLUTION": _temp(
        "compliance",
        "Remove the gate after Automatic Closure reaches approved GA.",
        "Future Automatic Closure consumer; no workflow is wired in this PR.",
        introduced="PR-MON-FEATURE-FLAGS-1",
    ),

    # ── Temporary: deliberate activation flag (RSMP Tier 0A) ──
    RSMP_TIER0A_ACTIVATION_FLAG: _temp(
        "compliance", "Remove after Tier 0A scoring is permanently activated post staging dry-run.",
        "Activated deliberately after the staging config diff + historical dry run are approved."),

    # ── Temporary: upload-latency staged rollout / experiments (FF_*) ──
    "FF_POLLING_SLOW": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_SIZE_CAP_CLIENT_REJECT": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_UX_SPLIT_UPLOAD_VERIFY": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_UPLOAD_ASYNC": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_ASYNC_VERIFY": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_GATE03_INDEXED_DEDUP": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),
    "FF_PRESIGNED_UPLOAD": _temp("platform", "Remove when upload-latency rollout concludes.", "Upload-latency rollout flag."),

    # ── Externally-resolved flags (RDI-023 completeness) ──
    # These ENABLE_*/*_ENABLED flags are resolved by os.environ.get() in SIBLING
    # modules, not by FeatureFlags. environment.py does NOT resolve them; they
    # are registered here only so the lifecycle registry is complete. The module
    # that owns each resolution is named in its notes. See _EXTERNALLY_RESOLVED_FLAGS.
    "ENABLE_SCREENING_ABSTRACTION": _temp(
        "screening", "Remove once the ComplyAdvantage screening abstraction is the unconditional path.",
        "screening_config.py — OFF by default in every env; selects the CA Mesh AML provider path."),
    "ENABLE_CA_RESCREEN": _temp(
        "screening", "Remove once Mesh rescreen is the default existing-customer path (SRP-2a Phase D).",
        "screening_config.py — OFF by default; requires 'monitor on demand' on the CA account."),
    "ENABLE_CA_PROFILE_HYDRATION": _temp(
        "screening", "Remove once ComplyAdvantage profile hydration is standard (Phase G).",
        "screening_config.py — OFF by default; display/audit enrichment only."),
    # NOTE: the draft Claude-memo enablement flag (resolved in
    # claude_memo_integration.py) is DELIBERATELY NOT registered here. Its
    # lifecycle is governed by a stronger, dedicated control — the H1/PC-4
    # memo-truthfulness guard (tests/test_h1_memo_claim_truthfulness.py) forbids
    # that flag from being set or defaulted on ANY config surface, environment.py
    # included, so its literal name must not appear in this file at all. See the
    # doc + the exclusion invariant in tests/test_feature_flag_lifecycle.py.
    "ENABLE_HYBRID_INCONCLUSIVE_GATE": _temp(
        "ai-pipeline", "Remove gate once HYBRID rules-first per-check evaluators are authored and approved (P12-7).",
        "config.py — OFF by default; live verification behaviour unchanged until enabled."),
    "ENABLE_AI_CIRCUIT_BREAKER": _temp(
        "ai-pipeline", "Remove flag once the cross-call Anthropic breaker is permanently activated (P11-5).",
        "config.py — OFF by default; per-call retry/backoff already active."),
    "ENABLE_AI_PROMPT_FENCING": _temp(
        "ai-pipeline", "Remove flag once prompt fencing is permanently activated (P11-5).",
        "config.py — OFF by default; live prompts byte-identical until enabled."),
    "MONITORING_AUTOMATION_ENABLED": _perm(
        "compliance", "monitoring_automation.py — operational scheduler toggle; env-defaulted ON in staging/production."),
    "DOCUMENT_HEALTH_SCHEDULER_ENABLED": _temp(
        "compliance", "Remove once the document-health scheduler rollout reaches Phase D / GA (M3.1).",
        "document_health_scheduler.py — explicit opt-in; OFF by default in every env during staged rollout."),
    "PERIODIC_REVIEW_MEMO_RECOVERY_ENABLED": _perm(
        "compliance", "server.py — operational recovery-sweep toggle for periodic-review memos."),
    "PERIODIC_REVIEW_NOTIFICATIONS_ENABLED": _perm(
        "compliance", "server.py — operational periodic-review notification toggle; default ON."),
}


def all_declared_flags() -> set:
    """Every feature-flag name the platform declares a default for in
    _DEFAULT_FLAGS, across all environments — i.e. the FeatureFlags-resolved
    surface. Derived the same way FeatureFlags resolves, so the guard test and
    the registry can never silently disagree about which flags exist."""
    names = set()
    for env_flags in _DEFAULT_FLAGS.values():
        names.update(env_flags.keys())
    return names


# Flags resolved by os.environ.get() in SIBLING modules rather than by
# FeatureFlags (RDI-023 completeness). environment.py does NOT resolve these —
# the named module does; they are listed so the lifecycle registry can cover the
# whole flag surface, not just the FeatureFlags-declared half.
# NOTE: the draft Claude-memo enablement flag (claude_memo_integration.py) is
# intentionally absent — it is governed by the stronger H1/PC-4 memo-truthfulness
# guard, which forbids its literal name from appearing on any config surface
# (this file included). See the exclusion invariant in the guard test.
_EXTERNALLY_RESOLVED_FLAGS = (
    "ENABLE_SCREENING_ABSTRACTION",           # screening_config.py
    "ENABLE_CA_RESCREEN",                      # screening_config.py
    "ENABLE_CA_PROFILE_HYDRATION",            # screening_config.py
    "ENABLE_HYBRID_INCONCLUSIVE_GATE",        # config.py
    "ENABLE_AI_CIRCUIT_BREAKER",              # config.py
    "ENABLE_AI_PROMPT_FENCING",               # config.py
    "MONITORING_AUTOMATION_ENABLED",          # monitoring_automation.py
    "DOCUMENT_HEALTH_SCHEDULER_ENABLED",      # document_health_scheduler.py
    "PERIODIC_REVIEW_MEMO_RECOVERY_ENABLED",  # server.py
    "PERIODIC_REVIEW_NOTIFICATIONS_ENABLED",  # server.py
)


def all_governed_flags() -> set:
    """The complete RDI-023 surface the lifecycle registry must classify: the
    FeatureFlags-declared flags PLUS the externally-resolved flags that sibling
    modules read directly from the environment."""
    return all_declared_flags() | set(_EXTERNALLY_RESOLVED_FLAGS)


def get_flag_lifecycle(flag: str) -> dict:
    """Return the lifecycle metadata for a flag, or None if unregistered."""
    return FLAG_LIFECYCLE.get(flag)


# ══════════════════════════════════════════════════════════════
# 3. SAFETY GUARDS — CRITICAL
# ══════════════════════════════════════════════════════════════

# These flags must NEVER be enabled in production
_PRODUCTION_FORBIDDEN_FLAGS = [
    "ENABLE_DEMO_MODE",
    "ENABLE_DEMO_BANNER",
    "ENABLE_DEMO_DATA_SEEDING",
    "ENABLE_MOCK_FALLBACKS",
    "ENABLE_ROLE_SWITCHER",
    "ENABLE_SIMULATED_SCREENING",
    "ENABLE_DEBUG_ENDPOINTS",
    "ENABLE_SHORTCUT_LOGIN",
    "ENABLE_KPI_DEMO_DATA",
    "ENABLE_SUMSUB_SANDBOX",
]

# These env vars must be set in production
_PRODUCTION_REQUIRED_VARS = [
    "ANTHROPIC_API_KEY",
    "SUMSUB_APP_TOKEN",
    "SUMSUB_SECRET_KEY",
    "JWT_SECRET",
    "DATABASE_URL",
]


def validate_environment() -> list:
    """
    Validate environment configuration at startup.
    Returns list of errors. Empty list = safe to start.

    CRITICAL: In production, this will BLOCK startup if unsafe.
    """
    errors = []
    warnings = []

    if is_production():
        # Check forbidden flags
        for flag in _PRODUCTION_FORBIDDEN_FLAGS:
            if flags.is_enabled(flag):
                errors.append(
                    f"CRITICAL: Flag '{flag}' is enabled in production. "
                    f"This is FORBIDDEN. Set {flag}=false or remove it."
                )

        # Check required variables
        for var in _PRODUCTION_REQUIRED_VARS:
            if not os.environ.get(var):
                errors.append(
                    f"CRITICAL: Required variable '{var}' is not set in production."
                )

        # Ensure mock fallbacks are impossible
        if os.environ.get("CLAUDE_MOCK_MODE", "").lower() in ("true", "1"):
            errors.append(
                "CRITICAL: CLAUDE_MOCK_MODE=true in production. "
                "AI must use real API calls."
            )

    elif is_staging():
        # Staging should not have demo data
        if flags.is_enabled("ENABLE_DEMO_DATA_SEEDING"):
            warnings.append("WARNING: ENABLE_DEMO_DATA_SEEDING is on in staging.")
        if flags.is_enabled("ENABLE_MOCK_FALLBACKS"):
            warnings.append("WARNING: ENABLE_MOCK_FALLBACKS is on in staging.")

        # Staging must have PII encryption key — fail closed
        if not os.environ.get("PII_ENCRYPTION_KEY"):
            errors.append(
                "CRITICAL: PII_ENCRYPTION_KEY is not set in staging. "
                "Staging must behave like production for encryption."
            )

        # Staging should have real API keys
        for var in _PRODUCTION_REQUIRED_VARS:
            if not os.environ.get(var):
                warnings.append(f"WARNING: '{var}' not set in staging.")

    # Log results
    for w in warnings:
        logger.warning(w)
    for e in errors:
        logger.error(e)

    return errors


def enforce_startup_safety():
    """
    Call at application startup. Blocks production if unsafe.
    Logs warnings for staging. Passes silently for demo.
    """
    logger.info(f"═══ Onboarda Platform Environment: {ENV.upper()} ═══")
    logger.info(f"Feature flags loaded: {len(flags.get_all())} flags")
    log_monitoring_feature_state_once()

    errors = validate_environment()

    if errors and is_production():
        logger.critical("═══ STARTUP BLOCKED — UNSAFE PRODUCTION CONFIGURATION ═══")
        for e in errors:
            logger.critical(e)
        logger.critical("Fix the configuration above before starting in production.")
        sys.exit(1)
    elif errors and is_staging():
        logger.critical("═══ STARTUP BLOCKED — UNSAFE STAGING CONFIGURATION ═══")
        for e in errors:
            logger.critical(e)
        logger.critical("Fix the configuration above before starting in staging.")
        sys.exit(1)

    # Log active flags summary
    enabled = [
        key for key, value in flags.get_all().items()
        if value and key not in MONITORING_FEATURE_FLAGS
    ]
    disabled = [
        key for key, value in flags.get_all().items()
        if not value and key not in MONITORING_FEATURE_FLAGS
    ]
    logger.info(f"Enabled flags ({len(enabled)}): {', '.join(enabled)}")
    logger.info(f"Disabled flags ({len(disabled)}): {', '.join(disabled)}")


# ══════════════════════════════════════════════════════════════
# 4. ENVIRONMENT-SPECIFIC CONFIGURATION
# ══════════════════════════════════════════════════════════════

def get_database_url() -> str:
    """Get database URL for current environment."""
    if is_production():
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL required in production")
        return url
    elif is_staging():
        return os.environ.get("STAGING_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_staging.db"))
    elif is_testing():
        return os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_test.db"))
    else:
        return os.environ.get("DEMO_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_demo.db"))


def get_s3_bucket() -> str:
    """Get S3 bucket name for current environment."""
    if is_production():
        return os.environ.get("S3_BUCKET", "arie-production-documents")
    elif is_staging():
        return os.environ.get("S3_BUCKET_STAGING", "arie-staging-documents")
    elif is_testing():
        return os.environ.get("S3_BUCKET_TESTING", "arie-testing-documents")
    else:
        return os.environ.get("S3_BUCKET_DEMO", "arie-demo-documents")


def get_sumsub_base_url() -> str:
    """Get Sumsub API URL — sandbox for demo, live for production."""
    if flags.is_enabled("ENABLE_SUMSUB_SANDBOX"):
        return "https://api.sumsub.com"  # Sumsub sandbox uses same URL with test credentials
    return os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com")


def get_jwt_secret() -> str:
    """Get JWT secret — must be unique per environment."""
    import logging as _logging
    _jwt_logger = _logging.getLogger("arie.security")

    if is_production():
        secret = os.environ.get("JWT_SECRET")
        if not secret or len(secret) < 32:
            raise RuntimeError("JWT_SECRET must be set and >= 32 chars in production")
        return secret
    elif is_staging():
        secret = os.environ.get("JWT_SECRET_STAGING", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for staging — using generated fallback. Set JWT_SECRET env var.")
            secret = "staging-fallback-" + os.urandom(16).hex()
        return secret
    elif is_testing():
        secret = os.environ.get("JWT_SECRET_TESTING", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.")
            secret = "testing-fallback-" + os.urandom(16).hex()
        return secret
    else:
        secret = os.environ.get("JWT_SECRET_DEMO", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for demo — using generated fallback. Set JWT_SECRET env var.")
            secret = "demo-fallback-" + os.urandom(16).hex()
        return secret


# ── API Credentials (Sprint 2.5: single access point) ──

def get_sumsub_app_token() -> str:
    return os.environ.get("SUMSUB_APP_TOKEN", "")

def get_sumsub_secret_key() -> str:
    return os.environ.get("SUMSUB_SECRET_KEY", "")

def get_sumsub_level_name() -> str:
    return os.environ.get("SUMSUB_LEVEL_NAME", "basic-kyc-level")

def get_sumsub_individual_level_name() -> str:
    """Individual/director/UBO KYC level.

    Resolution order:
      1. SUMSUB_INDIVIDUAL_LEVEL_NAME  (explicit individual override)
      2. SUMSUB_LEVEL_NAME             (backward-compatible fallback)
      3. 'id-and-liveness'             (safe default for Sumsub sandbox)
    """
    return (
        os.environ.get("SUMSUB_INDIVIDUAL_LEVEL_NAME")
        or os.environ.get("SUMSUB_LEVEL_NAME")
        or "id-and-liveness"
    )

def get_sumsub_aml_level_name() -> str:
    """AML-only screening level for director/UBO person AML.

    This level requires only first name, last name, and date of birth —
    no ID document, no liveness, no phone.  AML screening runs automatically
    once the applicant is moved to ``pending``.

    Resolution order:
      1. SUMSUB_AML_LEVEL_NAME  (explicit override)
      2. 'aml-screening'        (default — matches Phase 1 Sumsub config)
    """
    return os.environ.get("SUMSUB_AML_LEVEL_NAME", "aml-screening")

def is_sumsub_aml_entitlement_proven() -> bool:
    """Return whether AML/KYB entitlement for Sumsub has been explicitly proven.

    Sumsub IDV/KYC credentials alone are not treated as AML, PEP, sanctions, or
    KYB entitlement.  The proof flag is intentionally separate from level-name
    configuration so a legacy default cannot make officer-facing screening
    labels look reliance-grade.
    """
    value = os.environ.get("SUMSUB_AML_ENTITLEMENT_PROVEN")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "proven"}

def get_sumsub_company_level_name() -> str:
    """Company/KYB level.  Empty string means 'not configured'."""
    return os.environ.get("SUMSUB_COMPANY_LEVEL_NAME", "")

def get_opencorporates_api_key() -> str:
    return os.environ.get("OPENCORPORATES_API_KEY", "")

def get_ip_geolocation_api_key() -> str:
    return os.environ.get("IP_GEOLOCATION_API_KEY", "")

def get_screening_validity_days() -> int:
    """Configurable screening validity period in days.

    Screening results older than this threshold are considered expired
    and will block approval until a re-screen is performed.

    Resolution order:
      1. SCREENING_VALIDITY_DAYS env var  (explicit override)
      2. 90 days                          (regulatory default)

    Returns at least 1 to prevent misconfiguration from disabling the gate.
    """
    try:
        days = int(os.environ.get("SCREENING_VALIDITY_DAYS", "90"))
    except (ValueError, TypeError):
        days = 90
    return max(1, days)


def get_cors_origin() -> str:
    """Get allowed CORS origin for current environment."""
    if is_production():
        return os.environ.get("CORS_ORIGIN", "https://app.ariefinance.mu")
    elif is_staging():
        return os.environ.get("CORS_ORIGIN_STAGING", "https://staging.ariefinance.mu")
    elif is_testing():
        return os.environ.get("CORS_ORIGIN_TESTING", "http://localhost:3000")
    else:
        return os.environ.get("CORS_ORIGIN_DEMO", "https://demo.ariefinance.mu")


# ══════════════════════════════════════════════════════════════
# 5. ENVIRONMENT INFO API (for frontend)
# ══════════════════════════════════════════════════════════════

def get_environment_info() -> dict:
    """
    Return environment info safe to send to frontend.
    Used by /api/config/environment endpoint.
    """
    info = {
        "environment": ENV,
        "is_demo": is_demo(),
        "is_production": is_production(),
        "features": flags.get_client_safe_flags(),
        "upload_latency_flags": flags.get_upload_latency_client_flags(),
        "monitoring_feature_flags": get_monitoring_feature_status(),
        "backoffice": get_backoffice_runtime_config(),
        "version": os.environ.get("APP_VERSION", "1.0.0-pilot"),
    }
    return info

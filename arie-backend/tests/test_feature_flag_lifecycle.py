"""RDI-023 — every feature flag carries lifecycle metadata.

The finding: flags had no owner, introduction point, classification or sunset,
so a permanent environment differentiator was indistinguishable from a
temporary rollout that should have been deleted. environment.FLAG_LIFECYCLE now
classifies every declared flag; these guards keep it complete and honest, and
prove the registry is PURE METADATA that never leaks into runtime behaviour or
the client contract (so the frozen Application Review / Screening surfaces that
read flags are untouched).
"""
import environment as env


def _entries():
    return env.FLAG_LIFECYCLE


# ── Completeness: registry and declared flags are in exact correspondence ────

def test_every_declared_flag_has_lifecycle_metadata():
    declared = env.all_declared_flags()
    missing = sorted(declared - set(_entries()))
    assert not missing, (
        "RDI-023: these flags have no FLAG_LIFECYCLE entry — classify them "
        f"(permanent config vs temporary rollout) before merge: {missing}"
    )


def test_registry_has_no_orphan_entries():
    declared = env.all_declared_flags()
    orphans = sorted(set(_entries()) - declared)
    assert not orphans, (
        "RDI-023: FLAG_LIFECYCLE references flags no environment declares — a "
        f"stale entry after a flag removal: {orphans}"
    )


# ── Each entry is well-formed ────────────────────────────────────────────────

def test_every_entry_has_owner_introduced_and_valid_classification():
    for flag, meta in _entries().items():
        assert meta.get("owner"), f"{flag}: owner must be set"
        assert isinstance(meta["owner"], str) and meta["owner"].strip(), f"{flag}: owner blank"
        assert meta.get("introduced"), f"{flag}: introduced must be set"
        assert meta.get("classification") in env._FLAG_CLASSIFICATIONS, (
            f"{flag}: classification must be one of {env._FLAG_CLASSIFICATIONS}"
        )


def test_temporary_flags_carry_a_sunset_and_permanent_flags_do_not():
    """The core RDI-023 distinction: a temporary flag must declare how its life
    ends; a permanent config differentiator must not pretend to have a sunset."""
    for flag, meta in _entries().items():
        if meta["classification"] == env.FLAG_TEMPORARY:
            assert meta.get("sunset"), (
                f"{flag}: temporary flags MUST declare a sunset condition — "
                "otherwise a rollout flag rots into a permanent dead conditional"
            )
            assert str(meta["sunset"]).strip()
        else:  # permanent
            assert meta.get("sunset") is None, (
                f"{flag}: permanent config differentiators do not sunset "
                "(set sunset=None); reclassify as temporary if it should be removed"
            )


# ── Cross-check: classification aligns with the pilot-scope reality ──────────

def test_pilot_scope_vetoed_modules_are_classified_temporary():
    """Enterprise modules the pilot-scope veto refuses are gated, not-yet-GA
    rollouts — they must be temporary, not permanent config."""
    for flag in env._PILOT_SCOPE_VETOED_FLAGS:
        meta = _entries().get(flag)
        assert meta, f"{flag}: pilot-vetoed flag missing from lifecycle registry"
        assert meta["classification"] == env.FLAG_TEMPORARY, (
            f"{flag}: a pilot-scope-vetoed enterprise module is a gated rollout "
            "and must be classified temporary, not permanent"
        )


# ── Pure metadata: no behaviour / contract change ────────────────────────────

def test_lifecycle_registry_does_not_leak_into_the_client_contract():
    """RDI-023 must not alter the /api/config/environment response the frozen
    frontend consumes: lifecycle owners/sunsets are internal governance data."""
    info = env.get_environment_info()
    blob = repr(info)
    assert "sunset" not in blob
    assert "pre-registry" not in blob
    assert "classification" not in blob
    # The feature list itself is unchanged in shape: still a flat name->bool map.
    for key, value in info["features"].items():
        assert isinstance(value, bool), f"{key} should resolve to a bool, got {value!r}"


def test_lifecycle_metadata_is_not_consulted_by_flag_resolution():
    """is_enabled must resolve purely from env defaults + env vars — never from
    the lifecycle registry — so classification can never change a flag's value."""
    ff = env.FeatureFlags("production")
    # A permanent, prod-forbidden flag stays False; a temporary rollout flag
    # stays at its env default. Neither is influenced by its classification.
    assert ff.is_enabled("ENABLE_DEMO_MODE") is False
    assert ff.is_enabled("FF_UPLOAD_ASYNC") is False

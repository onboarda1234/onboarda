"""Tripwire for the uuid-hex/fixture-heuristic flake class.

Background (CI incident 2026-07-18): test refs built from raw ``uuid4().hex``
occasionally contain ``e2e``, which matches the back-office fixture-exclusion
heuristic (``fixture_filter.FIXTURE_APP_REF_PATTERNS``) and silently hides the
test's own seeded applications from fixture-filtered list/report endpoints —
a ~0.15%-per-draw random CI failure that cannot be reproduced locally.

These tests keep the whole class dead:
1. the shared safe generator can never produce a needle match, and
2. no test file builds a ref-like string from raw uuid hex again — new
   offenders fail here with an explanation instead of a mystery CI failure.
"""
import os
import re

from fixture_safe_refs import FIXTURE_SAFE_ALPHABET, fixture_safe_suffix

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# A ref-ish string literal (known prefixes used across the suite) on the same
# statement as raw uuid hex. The safe helper and this file are exempt.
REF_PREFIX_RE = re.compile(r"['\"](?:ARF|DU|RM|QRT|EX13|SMOKE)[A-Z0-9]*-")
RAW_UUID_HEX_RE = re.compile(r"uuid4\(\)\.hex")
EXEMPT_FILES = {"fixture_safe_refs.py", os.path.basename(__file__)}


# Every ref prefix used with fixture_safe_suffix across the suite. The
# composed ref must be needle-free — deploy run 29679007460 failed because a
# suffix starting "9000" completed the prefix-anchored needle
# "arf-2026-9000" once composed with "ARF-2026-".
REAL_REF_PREFIXES = (
    "ARF-2026-",
    "ARF-SET-",
    "ARF-SUP-SCHEMA-",
    "DU-",
    "QRT-corr-",
    "EX13-",
)


def test_safe_suffix_never_matches_fixture_needles():
    from fixture_filter import FIXTURE_APP_REF_PATTERNS

    needles = [p.strip("%").lower() for p in FIXTURE_APP_REF_PATTERNS if p.strip("%")]
    assert needles, "fixture needle list unexpectedly empty"
    for prefix in REAL_REF_PREFIXES:
        for _ in range(10_000):
            suffix = fixture_safe_suffix(8, prefix=prefix)
            assert set(suffix) <= set(FIXTURE_SAFE_ALPHABET)
            composed = (prefix + suffix).lower()
            for needle in needles:
                assert needle not in composed, (prefix, suffix, needle)


def test_no_test_builds_ref_like_strings_from_raw_uuid_hex():
    offenders = []
    for name in sorted(os.listdir(TESTS_DIR)):
        if not name.endswith(".py") or name in EXEMPT_FILES:
            continue
        path = os.path.join(TESTS_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if RAW_UUID_HEX_RE.search(line) and REF_PREFIX_RE.search(line):
                    offenders.append(f"{name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Test refs must not be built from raw uuid4().hex — a random suffix "
        "containing 'e2e' matches the fixture-exclusion heuristic and hides "
        "the test's own data from list/report endpoints (see CI incident in "
        "fixture_safe_refs.py). Use fixture_safe_refs.fixture_safe_suffix() "
        "instead.\n" + "\n".join(offenders)
    )

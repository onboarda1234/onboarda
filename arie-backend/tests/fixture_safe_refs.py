"""Fixture-safe random suffixes for test reference numbers.

Incident (2026-07-18, CI runs 29633106317 / 29633919398): tests that build
application refs from ``uuid4().hex`` occasionally draw a suffix containing
``e2e`` (e.g. ``DU-9e131e2e-001``). The back-office fixture-exclusion
heuristic (``fixture_filter.FIXTURE_APP_REF_PATTERNS``, ``LIKE '%e2e%'``)
then silently hides the test's own seeded applications from list/report
endpoints — HTTP 200 with zero rows — failing whichever test drew the bad
suffix (~0.15% per draw). Always build ref suffixes with
``fixture_safe_suffix()`` instead of raw uuid hex.

``test_fixture_safe_ref_hygiene.py`` enforces this repo-wide.
"""
import random

# No 'e' in the alphabet: 'e2e' (and every other current needle, which all
# contain letters outside this set) cannot form. The re-draw loop below stays
# as a belt-and-braces guard against future needle additions.
FIXTURE_SAFE_ALPHABET = "0123456789abcdf"


def fixture_safe_suffix(length=8):
    """Random lowercase suffix that can never match a fixture-ref needle."""
    from fixture_filter import FIXTURE_APP_REF_PATTERNS

    needles = [p.strip("%").lower() for p in FIXTURE_APP_REF_PATTERNS if p.strip("%")]
    while True:
        suffix = "".join(random.choice(FIXTURE_SAFE_ALPHABET) for _ in range(length))
        if not any(needle in suffix for needle in needles):
            return suffix

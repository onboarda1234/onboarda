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
# First character is always a letter: prefix-anchored needles that end in
# digits (e.g. "arf-2026-9000%") could otherwise be COMPLETED by a suffix that
# starts with the right digits — deploy run 29679007460 failed on exactly
# that: suffix "90009f1f" composed to "arf-2026-90009f1f", matching the
# needle "arf-2026-9000". A letter first breaks any digit-run bridge across
# the prefix boundary.
FIXTURE_SAFE_FIRST_CHARS = "abcdf"


def fixture_safe_suffix(length=8, prefix=""):
    """Random lowercase suffix whose composed ref can't match a fixture needle.

    Pass the ref ``prefix`` the caller will prepend (e.g. ``"ARF-2026-"``) so
    the re-draw loop checks the needles against the COMPOSED ref, not just the
    suffix — needles like ``arf-2026-9000%`` only match once combined.
    """
    from fixture_filter import FIXTURE_APP_REF_PATTERNS

    needles = [p.strip("%").lower() for p in FIXTURE_APP_REF_PATTERNS if p.strip("%")]
    while True:
        suffix = random.choice(FIXTURE_SAFE_FIRST_CHARS) + "".join(
            random.choice(FIXTURE_SAFE_ALPHABET) for _ in range(max(length - 1, 0))
        )
        composed = (str(prefix) + suffix).lower()
        if not any(needle in composed for needle in needles):
            return suffix

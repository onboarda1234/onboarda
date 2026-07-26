"""R3-BSA-025 — WeasyPrint CVE-2026-49452 is fixed by upgrade, not allowlisted.

CVE-2026-49452 / GHSA-jhhc-3hcp-qhm5 was fixed upstream in **WeasyPrint 69.0**
(released 2026-06-02). The earlier remediation only *bounded* a CI allowlist on
the assumption "no fixed release exists" — which was false. This batch pins the
fixed version and removes the pip-audit exception entirely. These guards keep
that closure honest:

  1. FIXED-VERSION GUARD — WeasyPrint is pinned `>= 69.0` in BOTH
     requirements.txt and the runtime lock, so a downgrade cannot silently
     re-open the vulnerability now that there is no allowlist to catch it.
  2. NO-ALLOWLIST GUARD — ci.yml must NOT carry a `--ignore-vuln` for this CVE:
     we rely on the pinned fix, so pip-audit must be free to flag any future
     regression.
  3. MITIGATION GUARD (defense-in-depth) — production code must still never
     enable `presentational_hints` (the mode the advisory identified). The CVE
     is fixed, but keeping that mode unused costs nothing and guards against a
     future advisory in the same surface.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "arie-backend"
CI = REPO / ".github" / "workflows" / "ci.yml"

CVE_ID = "CVE-2026-49452"
FIXED_VERSION = (69, 0)  # WeasyPrint 69.0 fixed CVE-2026-49452 (2026-06-02)

# Production Python across the WHOLE backend, including subpackages
# (supervisor/, screening_complyadvantage/, prescreening/, utils/, tools/,
# migrations/, …). A non-recursive scan would miss a WeasyPrint call in any of
# them, so recurse and exclude only tests and bytecode.
_EXCLUDED_PARTS = {"tests", "__pycache__", ".pytest_cache"}
_PROD_PY = sorted(
    p for p in BACKEND.rglob("*.py")
    if not _EXCLUDED_PARTS.intersection(p.parts)
    and p.name != "conftest.py"
)


def _ci_text():
    assert CI.exists(), "ci.yml missing"
    return CI.read_text(encoding="utf-8")


def _parse_ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def _pinned_weasyprint(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(?i)^weasyprint==([0-9][0-9.]*)", line.strip())
        if m:
            return _parse_ver(m.group(1))
    return None


# ── 1. Fixed-version guard: pin >= the release that fixes the CVE ────────────

def test_weasyprint_pinned_at_or_above_the_fixed_version():
    req = _pinned_weasyprint(BACKEND / "requirements.txt")
    lock = _pinned_weasyprint(BACKEND / "requirements.lock")
    assert req is not None, "weasyprint must be pinned in requirements.txt"
    assert lock is not None, "weasyprint must be pinned in requirements.lock"
    fixed = ".".join(map(str, FIXED_VERSION))
    assert req >= FIXED_VERSION, (
        f"R3-BSA-025: requirements.txt pins weasyprint=={'.'.join(map(str, req))}, "
        f"below the {CVE_ID} fix ({fixed}). Do not downgrade — there is no "
        "allowlist to catch a reopened vulnerability."
    )
    assert lock >= FIXED_VERSION, (
        f"R3-BSA-025: requirements.lock pins weasyprint below {fixed} — regenerate "
        "the lock from requirements.txt."
    )
    assert req == lock, (
        f"weasyprint version drift: requirements.txt={'.'.join(map(str, req))} "
        f"lock={'.'.join(map(str, lock))} — regenerate the lock."
    )


# ── 2. No-allowlist guard: rely on the fix, not an exception ──────────────────

def test_ci_does_not_allowlist_the_fixed_cve():
    ci = _ci_text()
    assert f"--ignore-vuln {CVE_ID}" not in ci, (
        f"R3-BSA-025: {CVE_ID} is fixed in WeasyPrint {'.'.join(map(str, FIXED_VERSION))}; "
        "remove the pip-audit --ignore-vuln exception so a future regression is caught."
    )


# ── 3. Mitigation guard (defense-in-depth): the vulnerable mode stays unused ──
#
# CVE-2026-49452 triggered on any truthy `presentational_hints`, and WeasyPrint
# accepts it via **kwargs / a variable / positionally — so a scan for the literal
# `=True` alone is evadable (dict key, `=1`, `=bool(x)`, `=flag`, line-split). We
# forbid the token `presentational_hints` from appearing in production code
# UNLESS it is an explicit `= False`. That one rule defeats every spelling.
_PRESENTATIONAL_HINTS = re.compile(r"presentational_hints")
_SAFE_DISABLE = re.compile(r"presentational_hints\s*=\s*False\b")


def test_no_production_source_enables_presentational_hints():
    """Defense-in-depth: keep the advisory's vulnerable mode unused, in any
    spelling, in any backend subpackage — even though the CVE is now fixed."""
    offenders = []
    for path in _PROD_PY:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _PRESENTATIONAL_HINTS.search(line) and not _SAFE_DISABLE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, (
        f"R3-BSA-025: `presentational_hints` set to a non-False value is the mode "
        f"the {CVE_ID} advisory identified. Remove it (or write an explicit `=False`):\n"
        + "\n".join(offenders)
    )


def test_the_guard_scans_every_weasyprint_call_site():
    """Defends the mitigation guard against BOTH a vacuous pass (scan hits
    nothing) AND a refactor that moves a render call into a subpackage:
    independently discover every production file that calls write_pdf( and
    assert each is inside the scanned set. Coverage holds by construction —
    call sites are drawn FROM _PROD_PY — as long as _PROD_PY recurses (it does)."""
    callers = [p for p in _PROD_PY
               if "write_pdf(" in p.read_text(encoding="utf-8")]
    assert callers, "no write_pdf( call sites found — the mitigation scan would be vacuous"
    names = {p.name for p in callers}
    assert "pdf_generator.py" in names, names
    assert "evidence_pack_export.py" in names, names

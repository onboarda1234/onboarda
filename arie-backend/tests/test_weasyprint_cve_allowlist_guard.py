"""R3-BSA-025 — WeasyPrint CVE-2026-49452 allowlist is bounded and justified.

CI allowlists `CVE-2026-49452` / `GHSA-jhhc-3hcp-qhm5` for WeasyPrint 68.1
because (a) there is no fixed upstream release and (b) the vulnerable mode —
`presentational_hints=True` — is not enabled anywhere in the product. Both
premises can silently rot: a future call could switch the flag on, or the
dated exception could outlive its review window while no one notices.

These guards make the exception self-policing:

  1. MITIGATION GUARD — no production source enables `presentational_hints`,
     so the "vulnerable mode unused" justification stays true. Removing the
     mitigation (turning the flag on) fails CI.
  2. EXPIRY GUARD — the allowlist carries a hard `Review/expiry:` date in
     ci.yml; this test reads that date (single source of truth) and FAILS once
     it passes, forcing a re-audit / upgrade / conscious extension instead of
     an exception that quietly lives forever. (Audit fix directive, BSA-025:
     "fail CI when the dated exception expires".)

The expiry date lives ONLY in ci.yml — this test parses it, so bumping the CI
comment automatically re-arms the guard; there is no second copy to drift.
"""
import datetime
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "arie-backend"
CI = REPO / ".github" / "workflows" / "ci.yml"

CVE_ID = "CVE-2026-49452"

# Production Python that could invoke WeasyPrint. Tests are excluded — a test
# is allowed to reference the flag to prove it is forbidden.
_PROD_PY = sorted(
    p for p in BACKEND.glob("*.py")
    if p.name != "conftest.py"
)


def _ci_text():
    assert CI.exists(), "ci.yml missing"
    return CI.read_text(encoding="utf-8")


# ── 1. Mitigation guard: the vulnerable mode must stay unused ────────────────

def test_no_production_source_enables_presentational_hints():
    """CVE-2026-49452 affects `presentational_hints=True`. The allowlist is
    only honest while no production call enables it."""
    offenders = []
    pat = re.compile(r"presentational_hints\s*=\s*True")
    for path in _PROD_PY:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "R3-BSA-025: presentational_hints=True is the mode vulnerable to "
        f"{CVE_ID}; the CI allowlist assumes it is never enabled. Remove it or "
        "revisit the allowlist:\n" + "\n".join(offenders)
    )


def test_the_guard_actually_scans_the_pdf_generators():
    """Defends the guard above: if the PDF modules were renamed/moved so the
    scan hit nothing, the mitigation claim would be vacuously 'true'."""
    scanned = {p.name for p in _PROD_PY}
    assert "pdf_generator.py" in scanned
    assert "evidence_pack_export.py" in scanned
    # And WeasyPrint really is invoked in the scanned set (write_pdf present),
    # so "no presentational_hints" is a statement about live call sites.
    joined = "\n".join(p.read_text(encoding="utf-8") for p in _PROD_PY)
    assert "write_pdf(" in joined


# ── 2. Expiry guard: the dated exception cannot outlive its review ───────────

def test_ci_allowlists_the_cve_with_a_documented_reason():
    ci = _ci_text()
    assert f"--ignore-vuln {CVE_ID}" in ci, (
        f"CI must allowlist {CVE_ID} via pip-audit --ignore-vuln"
    )
    # The justification (why it is safe to ignore) must be recorded next to it.
    assert "presentational_hints" in ci, (
        "the allowlist must document WHY the CVE is ignored (unused vulnerable mode)"
    )


def _allowlist_expiry(ci):
    m = re.search(r"Review/expiry:\s*(\d{4}-\d{2}-\d{2})", ci)
    assert m, "ci.yml must carry a `Review/expiry: YYYY-MM-DD` date for the allowlist"
    return datetime.date.fromisoformat(m.group(1))


def test_allowlist_has_not_expired():
    """When today passes the CI review date this FAILS on purpose — the fix is
    to re-audit CVE-2026-49452 (upgrade WeasyPrint if a fixed release exists,
    otherwise re-confirm the mitigation and bump the single date in ci.yml)."""
    ci = _ci_text()
    expiry = _allowlist_expiry(ci)
    today = datetime.date.today()
    assert today <= expiry, (
        f"R3-BSA-025: the {CVE_ID} allowlist expired on {expiry} (today {today}). "
        "Re-audit the advisory: upgrade WeasyPrint if a fixed release now exists, "
        "or re-confirm presentational_hints is still unused and bump the "
        "`Review/expiry:` date in .github/workflows/ci.yml."
    )

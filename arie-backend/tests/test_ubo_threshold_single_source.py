"""C10b (PR-D): the UBO ownership threshold has exactly ONE source.

verification_matrix.UBO_THRESHOLD_PCT is the single source. Python
surfaces import or derive from it; the UI check registers and the
server risk-indicator ladder cannot import Python, so they are pinned
here as MIRRORS — when the constant changes (C10/PR-E, founder + MLRO
sign-off), this suite fails listing every mirror that must move with
it. That is the design: PR-E becomes a one-line change plus the
consciously-updated mirrors this file enumerates.
"""

import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "arie-backend"


def _pct_str():
    # Same rendering as every production f-string (:.0f), so a fractional
    # threshold cannot diverge between code and this suite.
    from verification_matrix import UBO_THRESHOLD_PCT
    return f"{UBO_THRESHOLD_PCT:.0f}"


# ── The single source ──────────────────────────────────────────────

def test_exactly_one_assignment_in_backend_python():
    assignments = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts or "venv" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*UBO_THRESHOLD_PCT\s*(?::[^=]+)?=\s*[0-9]", line):
                assignments.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert assignments == ["arie-backend/verification_matrix.py:{}".format(
        assignments[0].rsplit(":", 1)[1] if assignments else "?")], (
        f"UBO_THRESHOLD_PCT must be assigned exactly once, in "
        f"verification_matrix.py — found: {assignments}"
    )


def test_current_value_is_the_fatf_25_until_pr_e():
    # C10/PR-E tripwire: changing the threshold is a regulatory decision
    # (founder + MLRO). PR-E flips this assertion deliberately.
    from verification_matrix import UBO_THRESHOLD_PCT
    assert UBO_THRESHOLD_PCT == 25.0


def test_python_surfaces_share_the_constant():
    import verification_matrix
    import document_verification
    from supervisor import agent_executors, schemas

    assert document_verification.UBO_THRESHOLD_PCT == verification_matrix.UBO_THRESHOLD_PCT
    assert agent_executors.UBO_THRESHOLD_PCT == verification_matrix.UBO_THRESHOLD_PCT
    # Schema default derives from the constant.
    from supervisor.schemas import _UBO_THRESHOLD_PCT_DEFAULT
    assert _UBO_THRESHOLD_PCT_DEFAULT == verification_matrix.UBO_THRESHOLD_PCT


def test_matrix_strings_derive_from_the_constant():
    from verification_matrix import ALL_DOC_CHECKS, UBO_THRESHOLD_PCT
    pct = int(UBO_THRESHOLD_PCT)
    doc15b = next(c for c in ALL_DOC_CHECKS["reg_sh"]["checks"] if c["id"] == "DOC-15B")
    assert doc15b["label"] == f"UBO Identification (≥{pct}%)"
    assert f"≥{pct}%" in doc15b["logic"]


def test_engine_messages_derive_from_the_constant():
    src = (BACKEND / "document_verification.py").read_text(encoding="utf-8")
    doc15b_region = src[src.index('elif id_ == "DOC-15B"'):src.index('elif id_ == "DOC-18"')]
    assert "UBO_THRESHOLD_PCT" in doc15b_region
    assert "≥25%" not in doc15b_region, "DOC-15B messages must derive, not hardcode"


def test_db_agent_seed_strings_derive_from_the_constant():
    src = (BACKEND / "db.py").read_text(encoding="utf-8")
    assert '"UBO threshold qualification ≥25% (rule)"' not in src
    assert src.count('f"UBO threshold qualification ≥{UBO_THRESHOLD_PCT:.0f}% (rule)"') == 2


# ── Pinned mirrors (cannot import Python — must move with PR-E) ────

def test_backoffice_mirrors_match_the_constant():
    pct = _pct_str()
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    mirrors = [
        # fallback check register (reg_sh entry)
        f"label:'UBO Identification (\\u2265{pct}%)'",
        f"rule:'Any shareholder holding \\u2265 {pct}% must be identified as a declared UBO'",
        # agent panel check list
        f"UBO threshold qualification ≥{pct}% (rule)",
        # risk-indicator ladder labels (bottom rung; PR-E must decide
        # whether the indicator ladder follows the qualification threshold)
        f"'UBOs > {pct}%'",
        f"'Ownership >{pct}%'",
    ]
    missing = [m for m in mirrors if m not in html]
    assert missing == [], f"back-office UBO threshold mirrors out of sync: {missing}"


def test_portal_mirror_matches_the_constant():
    pct = _pct_str()
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")
    assert f"label: 'UBO Identification (\\u2265{pct}%)'" in html
    assert f"rule: 'Any shareholder holding \\u2265 {pct}% must be identified as a declared UBO'" in html


def test_supervisor_borderline_band_derives_from_the_constant():
    # Review finding (both reviewers): the periodic-review borderline-UBO
    # band was a hardcoded 20..30 with "near 25% threshold" copy — a
    # threshold-derived surface that would go silently stale at PR-E.
    src = (BACKEND / "supervisor" / "agent_executors.py").read_text(encoding="utf-8")
    assert "UBO_THRESHOLD_PCT - 5 <= pct <= UBO_THRESHOLD_PCT + 5" in src
    assert "(near 25% threshold)" not in src, "borderline copy must derive"


def test_portal_ubo_hint_and_data_collection_copy_mirrors():
    pct = _pct_str()
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")
    # Soft advisory JS hint in the realtime UBO mapping panel.
    assert f"if (pctValue > {pct})" in html
    # KNOWN DIVERGENCE, pinned deliberately: the applicant-facing UBO
    # data-collection copy says "\u2265 20% ownership" while the engine
    # threshold is 25% — conservative in direction (collects more), and
    # already at the C10/PR-E FSC target. PR-E resolves the divergence by
    # moving the engine to 20; this pin makes the pair visible until then.
    assert html.count("\u2265 20% ownership") >= 1


def test_server_indicator_ladder_bottom_rung_matches_the_constant():
    # The ladder (>25 / >50 / >75) is a risk-indicator tiering, not the
    # qualification comparison — PR-D deliberately leaves its SQL alone.
    # This pin makes PR-E confront the bottom rung explicitly.
    pct = _pct_str()
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert f"COALESCE(g.ownership_pct, 0) > {pct} THEN 1 ELSE 0 END AS ownership_above_25" in src
    assert f'"UBO ownership above {pct}%"' in src

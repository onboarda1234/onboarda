"""PR-F (C3): one canonical display name per document across all registers.

Founder-decided canon: memarts = Memorandum of Association,
reg_sh = Shareholder Register, fin_stmt = Financial Statements /
Management Accounts, licence = Regulatory Licence (singular),
entity bankref = Company Bank Reference Letter,
person bankref = Personal Bank Reference Letter.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = Path(__file__).resolve().parents[2]

CANON = {
    "memarts": "Memorandum of Association",
    "reg_sh": "Shareholder Register",
    "fin_stmt": "Financial Statements / Management Accounts",
    "licence": "Regulatory Licence",
}

RETIRED_VARIANTS = [
    "Memorandum & Articles",
    "Memorandum &amp; Articles",
    "Memorandum and Articles",
    "Register of Shareholders",
    "Regulatory Licence(s)",
    "Licence / Regulatory Approval",
    "Regulatory Licence or Certificate",
    "Bank Reference Letter (Entity)",
    "Bank Reference Letter (PEP)",
    "Latest Annual Financial Statements",
]


def test_matrix_seed_carries_the_canon():
    from verification_matrix import build_ai_checks_seed
    names = {(cat, dt): name for cat, dt, name, _ in build_ai_checks_seed()}
    for dt, canon in CANON.items():
        assert names[("entity", dt)] == canon
    assert names[("entity", "bankref")] == "Company Bank Reference Letter"
    assert names[("person", "bankref")] == "Personal Bank Reference Letter"


def test_no_retired_variant_survives_in_the_uis():
    for rel in ("arie-backoffice.html", "arie-portal.html"):
        html = (ROOT / rel).read_text(encoding="utf-8")
        offenders = [v for v in RETIRED_VARIANTS if v in html]
        assert offenders == [], f"{rel} still carries retired name(s): {offenders}"


def test_backend_registers_carry_the_canon():
    from document_policy_registry import POLICY_DEFINITIONS

    labels = {p["key"]: p["label"] for p in POLICY_DEFINITIONS
              if p["key"] in ("memarts", "reg_sh", "fin_stmt", "licence", "bankref")}
    assert labels["reg_sh"] == "Shareholder Register"
    assert labels["fin_stmt"] == "Financial Statements / Management Accounts"
    assert labels["licence"] == "Regulatory Licence"
    assert labels["bankref"] == "Company Bank Reference Letter"


def test_enhanced_requirement_labels_carry_the_canon():
    src = (ROOT / "arie-backend" / "enhanced_requirements.py").read_text(encoding="utf-8")
    assert src.count('"Company Bank Reference Letter"') >= 4, (
        "seed defaults AND the force-update path must both carry the canon (H2)"
    )
    assert 'f"Personal Bank Reference Letter - {subject_name}"' in src


def test_alias_maps_accept_canonical_and_legacy_inbound_names():
    import server
    for inbound, expected in [
        ("Regulatory Licence", "licence"),
        ("Company Bank Reference Letter", "bankref"),
        ("Personal Bank Reference Letter", "bankref"),
        ("Register of Shareholders", "reg_sh"),   # legacy must keep resolving
        ("Memorandum and Articles", "memarts"),   # legacy must keep resolving
        ("Shareholder Register", "reg_sh"),
        ("Financial Statements / Management Accounts", "fin_stmt"),
    ]:
        assert server.DOCUMENT_TYPE_NORMALIZE.get(inbound.lower()) == expected, inbound

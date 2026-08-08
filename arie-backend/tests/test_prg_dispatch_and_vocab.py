"""PR-G (C4 + C5): ID-based rule dispatch and one check-type vocabulary.

C4 — run_rule_checks dispatches name checks on rule_type (proven identical
set to the old label tuple) and resolves the declared value from each
check's OWN matrix-declared ps_field. The old shared chain put the company
name first, so on production prescreening context (which merges the
person's full_name into the application dict) every person Name Match
compared the person's document against the COMPANY name and failed.

C5 — the seed vocabulary, the admin select, the server whitelist and the
static fallback registers agree; the round-trip preserves check ids.
"""

import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from document_verification import run_rule_checks  # noqa: E402
from verification_matrix import ALL_DOC_CHECKS, CheckStatus, build_ai_checks_seed  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

NOT_IMPLEMENTED = "Rule check not implemented"

# Every rule_type="name" check: (engine doc_type, category, check id, ps_field)
NAME_CHECKS = [
    ("cert_inc", "entity", "DOC-05", "registered_entity_name"),
    ("memarts", "entity", "DOC-08", "registered_entity_name"),
    ("fin_stmt", "entity", "DOC-21", "registered_entity_name"),
    ("poa", "entity", "DOC-02", "registered_entity_name"),
    ("board_res", "entity", "DOC-24", "authorised_signatory"),
    ("bankref", "entity", "DOC-32", "registered_entity_name"),
    ("licence", "entity", "DOC-33", "registered_entity_name"),
    ("passport", "person", "DOC-51", "full_name"),
    ("national_id", "person", "DOC-55", "full_name"),
    ("poa", "person", "DOC-62", "full_name"),
    ("cv", "person", "DOC-57", "full_name"),
    ("bankref", "person", "DOC-66", "full_name"),
    ("sow", "person", "DOC-59", "full_name"),
]

# Production-shaped context: person fields MERGED into the application dict
# (server.build_document_verification_context), company name present.
MERGED_PS = {
    "registered_entity_name": "Meridian Holdings Ltd",
    "company_name": "Meridian Holdings Ltd",
    "full_name": "John Alexander Smith",
    "authorised_signatory": "Jane Q. Signer",
    "regulatory_licences": "EMI licence Malta",
}


def _result(results, check_id):
    matches = [r for r in results if r.get("id") == check_id]
    assert len(matches) == 1, f"{check_id}: expected exactly one result, got {matches!r}"
    return matches[0]


# ── C4: all 13 name checks execute (none falls to the fallback) ────

def test_every_name_check_executes_without_fallback():
    for doc_type, category, check_id, _ in NAME_CHECKS:
        results = run_rule_checks(doc_type, category,
                                  {"entity_name": "Anything"}, MERGED_PS, "LOW")
        r = _result(results, check_id)
        assert NOT_IMPLEMENTED not in str(r.get("message", "")), (
            f"{check_id} fell into the not-implemented fallback"
        )
        assert r.get("type") == "name"


def test_name_checks_resolve_their_own_ps_field():
    # The load-bearing C4 pin: with the merged production context, each
    # check must compare against ITS field — person checks against the
    # person, entity checks against the company, signatory against the
    # declared signatory.
    for doc_type, category, check_id, ps_field in NAME_CHECKS:
        results = run_rule_checks(doc_type, category,
                                  {"entity_name": MERGED_PS[ps_field]},
                                  MERGED_PS, "LOW")
        r = _result(results, check_id)
        assert r["result"] == CheckStatus.PASS, (
            f"{check_id}: expected PASS against its own field {ps_field!r}, "
            f"got {r['result']!r} ({r.get('message')})"
        )
        assert r.get("ps_field") == ps_field
        assert r.get("ps_value") == MERGED_PS[ps_field]


def test_person_name_match_prefers_person_over_company():
    # Regression pin for the production defect: person's document name must
    # match the PERSON even when the company name is present and different.
    results = run_rule_checks("passport", "person",
                              {"name": "John Alexander Smith"}, MERGED_PS, "LOW")
    r = _result(results, "DOC-51")
    assert r["result"] == CheckStatus.PASS
    assert r["ps_value"] == "John Alexander Smith"

    # And the company check still resolves the company on the same context.
    results = run_rule_checks("cert_inc", "entity",
                              {"entity_name": "Meridian Holdings Ltd"}, MERGED_PS, "LOW")
    r = _result(results, "DOC-05")
    assert r["result"] == CheckStatus.PASS
    assert r["ps_value"] == "Meridian Holdings Ltd"


def test_signatory_missing_declaration_warns_not_company_compare():
    ps = dict(MERGED_PS)
    ps.pop("authorised_signatory")
    results = run_rule_checks("board_res", "entity",
                              {"entity_name": "Someone Else"}, ps, "LOW")
    r = _result(results, "DOC-24")
    assert r["result"] == CheckStatus.WARN
    assert "No declared name" in r["message"]


def test_no_label_text_dispatch_remains():
    src = (ROOT / "arie-backend" / "document_verification.py").read_text(encoding="utf-8")
    region = src[src.index("def run_rule_checks"):src.index("def run_hybrid_deterministic_pass")]
    assert 'label in ("Entity Name Match"' not in region
    assert '"expiry" in label.lower()' not in region


# ── C5: one vocabulary, coherent round-trip ────────────────────────

def _seed_types():
    import json
    types = set()
    for _, _, _, cj in build_ai_checks_seed():
        for c in json.loads(cj):
            types.add(c["type"])
    return types


def test_seed_vocabulary_is_whitelisted_and_selectable():
    server_src = (ROOT / "arie-backend" / "server.py").read_text(encoding="utf-8")
    m = re.search(r"AI_CHECK_ALLOWED_TYPES = \{([^}]*)\}", server_src)
    allowed = set(re.findall(r'"([a-z_]+)"', m.group(1)))

    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    region_start = bo.index("check-type-sel")
    region_end = bo.index("</select>", region_start)
    options = set(re.findall(r"<option value=\\?\"([a-z_]+)\\?\"", bo[region_start:region_end]))

    seed = _seed_types()
    assert seed <= allowed, f"seed types rejected by whitelist: {seed - allowed}"
    assert seed <= options, f"seed types missing from admin select: {seed - options}"
    assert options <= allowed, f"select offers non-whitelisted types: {options - allowed}"


def test_every_seed_type_has_badge_css():
    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    missing = [t for t in sorted(_seed_types())
               if f".check-type-badge.{t}" not in bo]
    assert missing == [], f"seed types without badge CSS: {missing}"


def test_fallback_registers_use_seed_vocabulary_for_matrix_checks():
    # The static fallback registers must not reintroduce the legacy-only
    # vocabulary for matrix-backed checks (EDD supplementary rows keep
    # their own seeded content/quality/age values by design).
    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    start = bo.index("var ENTITY_DOC_CHECKS = [")
    end = bo.index("var EDD_DOC_CHECK_IDS")
    region = bo[start:end]
    offenders = re.findall(r"label:'([^']+)', rule:'[^']*', type:'(content|age|expiry|quality)'", region)
    assert offenders == [], f"fallback rows still on legacy vocabulary: {offenders}"


def test_admin_load_preserves_check_ids():
    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    fn = bo[bo.index("function normalizeVerificationCheckConfigItem"):]
    fn = fn[:fn.index("\nfunction ")]
    assert "id: ch.id || ''" in fn
    assert "ps_field: ch.ps_field || ''" in fn


def test_server_accepts_seed_shaped_ids():
    server_src = (ROOT / "arie-backend" / "server.py").read_text(encoding="utf-8")
    assert '[A-Za-z0-9][A-Za-z0-9_-]{1,80}' in server_src


def test_edd_register_has_no_duplicate_bankref_row():
    # Item 9: in fallback state the duplicate EDD bankref row PUT-clobbered
    # the entity row (same (doc_type, category) upsert key).
    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    start = bo.index("var EDD_DOC_CHECKS = [")
    end = bo.index("];", start)
    assert "docId:'bankref'" not in bo[start:end]


def test_shared_bankref_policy_family_is_neutral():
    # Item 8: one policy spans Company and Personal bank references — a
    # Company-titled family mislabelled unlinked person docs.
    from document_policy_registry import POLICY_DEFINITIONS
    entry = next(p for p in POLICY_DEFINITIONS if p.get("policy_id") == "DOC-EVIDENCE-BANK-REFERENCE-v1")
    assert entry["label"] == "Bank Reference Letter"
    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    assert "'DOC-EVIDENCE-BANK-REFERENCE-v1','Bank Reference Letter'" in bo

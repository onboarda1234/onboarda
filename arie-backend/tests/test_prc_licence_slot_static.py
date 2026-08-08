"""PR-C (C2 + C2b + C2d): conditional Regulatory Licence slot in Section A.

C2  — the back office declares an expected `licence` entity slot so an
      uploaded licence renders in Section A instead of falling through to
      Section D.
C2b — the slot exists ONLY when the client declared a licence at
      pre-screening; `backofficeLicenceApplicable` is a JS mirror of
      verification_matrix.is_licence_applicable and must match its truth
      table exactly.
C2d — the entity bank reference deliberately has NO Section A slot: the
      operative company-level requirement is the `company_bank_reference`
      enhanced requirement rendered in Section C.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _bo_html():
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _function_source(html, name):
    start = html.index("function " + name)
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


# ── C2b: predicate truth table (node-executed, mirrors Python) ─────

def test_backoffice_licence_predicate_matches_python_truth_table():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from verification_matrix import is_licence_applicable

    cases = [
        {"regulatory_licences": "FCA authorised PI, ref 12345"},
        {"regulatory_licences": "EMI licence Malta"},
        {"regulatory_licences": "None"},
        {"regulatory_licences": "none"},
        {"regulatory_licences": "n/a"},
        {"regulatory_licences": "N/A"},
        {"regulatory_licences": "no"},
        {"regulatory_licences": ""},
        {"regulatory_licences": None},
        {"regulatory_licences": "  "},
        {"is_licensed": True},
        {"is_licensed": False, "regulatory_licences": "FCA authorised PI"},
        {"is_licensed": True, "regulatory_licences": "none"},
        {"has_licence": True},
        {"has_licence": False, "regulatory_licences": "EMI licence"},
        {},
        # Non-string JSON shapes (review finding: str()/String() semantics
        # diverge between Python and JS — the predicate must special-case
        # non-strings to stay parity-correct on legacy/direct-DB rows).
        {"regulatory_licences": ["none"]},
        {"regulatory_licences": [""]},
        {"regulatory_licences": []},
        {"regulatory_licences": {}},
        {"regulatory_licences": {"licence": "EMI"}},
        {"regulatory_licences": 0},
        {"regulatory_licences": 5},
        {"regulatory_licences": [["none"]]},
        {"regulatory_licences": True},
        {"is_licensed": 1},
        {"is_licensed": "true"},
    ]
    expected = [is_licence_applicable(ps) for ps in cases]

    html = _bo_html()
    registry_fn = _function_source(html, "registryPrescreeningData")
    predicate_fn = _function_source(html, "backofficeLicenceApplicable")
    script = (
        registry_fn + "\n" + predicate_fn + "\n"
        + "var cases = " + json.dumps(cases) + ";\n"
        + "console.log(JSON.stringify(cases.map(function(ps){"
        + "return backofficeLicenceApplicable({prescreeningData: ps});})));\n"
    )
    out = subprocess.run(["node", "-"], input=script, capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip())
    assert got == expected, (
        f"JS/Python divergence: {[(c, e, g) for c, e, g in zip(cases, expected, got) if e != g]}"
    )


# ── C2: conditional slot in the expected-slot builder ──────────────

def test_expected_slots_gain_conditional_licence_entity_slot():
    html = _bo_html()
    builder = _function_source(html, "buildExpectedKycDocumentSlots")
    # The push is gated on the predicate, tagged for Section A, entity-level.
    guard_idx = builder.index("if (backofficeLicenceApplicable(app))")
    push_idx = builder.index("doc_type: 'licence'")
    assert guard_idx < push_idx, "licence slot must be inside the applicability branch"
    licence_push = builder[guard_idx:builder.index("}", push_idx)]
    assert "taxonomy_section: 'entity'" in licence_push
    assert "person_id: null" in licence_push

    # The 8 unconditional entity slots are unchanged and precede the branch.
    for doc_type in ("cert_inc", "memarts", "reg_sh", "reg_dir",
                     "fin_stmt", "poa", "board_res", "structure_chart"):
        assert builder.index("doc_type: '" + doc_type + "'") < guard_idx


def test_officer_upload_select_offers_licence():
    # Without a matching <option>, openBoDocUploadForExpectedSlot('licence')
    # silently resolves to an empty selection.
    html = _bo_html()
    start = html.index('id="bo-upload-doc-type"')
    end = html.index("</select>", start)
    assert '<option value="licence">' in html[start:end]


# ── C2d: entity bankref stays out of Section A ─────────────────────

def test_entity_bankref_has_no_section_a_slot():
    html = _bo_html()
    builder = _function_source(html, "buildExpectedKycDocumentSlots")
    # bankref appears only in the person loops (directors/UBOs, Section B) —
    # never as an entity slot. The company-level requirement lives in
    # Section C as the company_bank_reference enhanced requirement.
    first_person_loop = builder.index("(app.directors || [])")
    bankref_positions = []
    idx = builder.find("doc_type: 'bankref'")
    while idx != -1:
        bankref_positions.append(idx)
        idx = builder.find("doc_type: 'bankref'", idx + 1)
    assert bankref_positions, "person-level bankref slots must remain"
    assert all(p > first_person_loop for p in bankref_positions), (
        "entity-level bankref slot found before the person loops — C2d forbids "
        "a Section A bank reference slot (Section C enhanced requirement is "
        "the operative surface)"
    )


def test_matrix_bankref_entry_documents_the_c2d_reconciliation():
    src = (ROOT / "arie-backend" / "verification_matrix.py").read_text(encoding="utf-8")
    assert "NOT a Section A slot" in src
    assert "company_bank_reference" in src

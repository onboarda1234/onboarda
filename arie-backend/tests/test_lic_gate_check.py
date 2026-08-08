"""PR-B (C1 + C1b): LIC-GATE — Licence Applicability Gate.

C1 — engine: every applicable licence document used to receive
    "Rule check not implemented for id=LIC-GATE — manual review required"
from the run_rule_checks fallback. A warn makes _aggregate return
"flagged", so an applicable licence document could NEVER verify. LIC-GATE
now has a real handler: PASS when the client declared a licence
(reusing is_licence_applicable so the gate logic lives in one place),
SKIP when they did not (defensive parity with the
verify_document_layered short-circuit).

C1b — surfaces: the gate check is visible in the back-office
verification-check register (static fallback list + type select +
badge CSS) and in the portal's DOC_RULES reference register. The
matrix seed (server-side source for /api/config/verification-checks)
must keep carrying it.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from document_verification import run_rule_checks, verify_document_layered  # noqa: E402
from verification_matrix import (  # noqa: E402
    CheckStatus,
    build_ai_checks_seed,
    is_licence_applicable,
)

ROOT = Path(__file__).resolve().parents[2]

APPLICABLE_PS = {
    "regulatory_licences": "EMI licence Malta, ref 12345",
    "registered_entity_name": "Acme Corp Ltd",
}
NOT_APPLICABLE_PS = {
    "regulatory_licences": "none",
    "registered_entity_name": "Acme Corp Ltd",
}

NOT_IMPLEMENTED_MARKER = "Rule check not implemented"


def _lic_gate(results):
    matches = [r for r in results if r.get("id") == "LIC-GATE"]
    assert len(matches) == 1, f"expected exactly one LIC-GATE result, got {matches!r}"
    return matches[0]


# ── C1: run_rule_checks handler ────────────────────────────────────

class TestLicGateRuleCheck:
    def test_pass_when_licence_declared(self):
        results = run_rule_checks("licence", "entity", {}, APPLICABLE_PS, "LOW")
        gate = _lic_gate(results)
        assert gate["result"] == CheckStatus.PASS
        assert gate["ps_field"] == "regulatory_licences"
        assert "EMI licence Malta" in gate.get("ps_value", "")
        assert NOT_IMPLEMENTED_MARKER not in gate["message"]

    def test_pass_via_is_licensed_boolean(self):
        ps = {"is_licensed": True}
        assert is_licence_applicable(ps) is True
        gate = _lic_gate(run_rule_checks("licence", "entity", {}, ps, "LOW"))
        assert gate["result"] == CheckStatus.PASS

    def test_skip_when_not_applicable(self):
        # verify_document_layered short-circuits before rule checks in this
        # case, but run_rule_checks is a public entry point — the direct
        # call must mirror the gate's SKIP escalation, not warn.
        gate = _lic_gate(run_rule_checks("licence", "entity", {}, NOT_APPLICABLE_PS, "LOW"))
        assert gate["result"] == CheckStatus.SKIP
        assert gate["ps_field"] == "regulatory_licences"

    def test_skip_when_prescreening_empty(self):
        gate = _lic_gate(run_rule_checks("licence", "entity", {}, {}, "LOW"))
        assert gate["result"] == CheckStatus.SKIP

    def test_gate_never_warns(self):
        for ps in (APPLICABLE_PS, NOT_APPLICABLE_PS, {}, {"is_licensed": True},
                   {"is_licensed": False}, {"has_licence": True}):
            gate = _lic_gate(run_rule_checks("licence", "entity", {}, ps, "LOW"))
            assert gate["result"] in (CheckStatus.PASS, CheckStatus.SKIP), (
                f"LIC-GATE must resolve pass/skip, got {gate['result']!r} for ps={ps!r}"
            )

    def test_no_licence_rule_check_hits_fallback(self):
        # The defect: LIC-GATE fell through to the generic
        # "Rule check not implemented" warn. No licence rule check may do so.
        for ps in (APPLICABLE_PS, NOT_APPLICABLE_PS):
            results = run_rule_checks("licence", "entity", {}, ps, "LOW")
            offenders = [r for r in results if NOT_IMPLEMENTED_MARKER in str(r.get("message", ""))]
            assert offenders == [], f"licence checks fell into fallback: {offenders!r}"

    def test_pass_evidence_never_contradicts_verdict(self):
        # Review finding: on {is_licensed: True, regulatory_licences: "none"}
        # the verdict is PASS (boolean takes precedence in
        # is_licence_applicable) but naive evidence selection surfaced
        # ps_value "none" next to it. Evidence must cite the value that
        # drove the verdict.
        gate = _lic_gate(run_rule_checks(
            "licence", "entity", {},
            {"is_licensed": True, "regulatory_licences": "none"}, "LOW"))
        assert gate["result"] == CheckStatus.PASS
        assert gate.get("ps_value") == "True"

    def test_pass_result_matches_helper_verdict(self):
        # The handler must reuse is_licence_applicable — the same inputs
        # must produce the same verdict on both paths.
        cases = [
            {"regulatory_licences": "FCA authorised PI"},
            {"regulatory_licences": "None"},
            {"regulatory_licences": "n/a"},
            {"is_licensed": True},
            {"is_licensed": False, "regulatory_licences": "FCA authorised PI"},
            {"has_licence": True},
            {},
        ]
        for ps in cases:
            gate = _lic_gate(run_rule_checks("licence", "entity", {}, ps, "LOW"))
            expected = CheckStatus.PASS if is_licence_applicable(ps) else CheckStatus.SKIP
            assert gate["result"] == expected, f"divergence for ps={ps!r}"


# ── C1: layered engine end-to-end ──────────────────────────────────

class TestLicGateLayered:
    def _verify(self, tmp_path, ps):
        f = tmp_path / "licence.pdf"
        f.write_bytes(b"%PDF-1.4 test licence document")
        return verify_document_layered(
            "licence", "entity",
            file_path=str(f), file_size=f.stat().st_size,
            mime_type="application/pdf", prescreening_data=ps,
            risk_level="LOW", existing_hashes=[],
        )

    def test_applicable_licence_runs_gate_as_pass(self, tmp_path):
        result = self._verify(tmp_path, APPLICABLE_PS)
        gate = _lic_gate(result["checks"])
        assert gate["result"] == CheckStatus.PASS
        # The gate alone must not flag the document; any flags must come
        # from real evidence checks, never from the not-implemented fallback.
        assert all(NOT_IMPLEMENTED_MARKER not in str(c.get("message", ""))
                   for c in result["checks"])
        assert result["overall"] != "skipped"
        assert result.get("not_applicable") is None

    def test_not_applicable_licence_still_short_circuits(self, tmp_path):
        # C0 contract unchanged: declared no licence → all-skip →
        # overall "skipped" with the not_applicable marker.
        result = self._verify(tmp_path, NOT_APPLICABLE_PS)
        assert result["overall"] == "skipped"
        assert result.get("not_applicable") is True
        gate = _lic_gate(result["checks"])
        assert gate["result"] == CheckStatus.SKIP


# ── C1b: seed keeps the gate (server-side config source) ───────────

def test_seed_includes_lic_gate_for_licence():
    import json
    licence_rows = [(cat, dt, name, cj) for cat, dt, name, cj in build_ai_checks_seed()
                    if dt == "licence"]
    assert len(licence_rows) == 1
    checks = json.loads(licence_rows[0][3])
    gate = [c for c in checks if c["id"] == "LIC-GATE"]
    assert len(gate) == 1
    assert gate[0]["label"] == "Licence Applicability Gate"
    assert gate[0]["type"] == "presence"
    assert gate[0]["classification"] == "rule"
    assert checks[0]["id"] == "LIC-GATE", "gate must be listed first for the licence doc"


# ── C1b: back-office static register ───────────────────────────────

def test_backoffice_fallback_list_shows_lic_gate():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    start = html.index("{ docId:'licence', name:'Licence / Regulatory Approval'")
    end = html.index("]}", start)
    licence_block = html[start:end]
    assert "Licence Applicability Gate" in licence_block
    assert "type:'presence'" in licence_block
    # Gate row precedes the first evidence check, mirroring matrix order.
    assert licence_block.index("Licence Applicability Gate") < licence_block.index("Entity Name Match")


def test_backoffice_type_select_offers_presence():
    # Without a matching <option>, a fallback-state admin save would
    # silently rewrite the gate's type to the select's first option.
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    assert "'<option value=\"presence\"' + (ch.type==='presence'?' selected':'') + '>presence</option>'" in html


def test_backoffice_presence_badge_css_exists():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    assert ".check-type-badge.presence" in html


# ── C1b: admin save round-trip — select types must be server-accepted ──

def test_backoffice_select_types_are_accepted_by_server_validator():
    """Review finding (blocking): saveAIChecks PUTs every check with the
    type currently selected in the admin <select>. Any option value the
    server's AI_CHECK_ALLOWED_TYPES whitelist rejects turns the save into a
    hard 400 — and because the PUTs run sequentially in one try block, all
    payloads queued behind the failing one are dropped. Pin: every option
    offered by the back-office select is accepted by the server validator.
    """
    import re

    bo = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    server_src = (ROOT / "arie-backend" / "server.py").read_text(encoding="utf-8")

    # Scope to the admin check-type select built inside renderCheckTab —
    # the only select whose values saveAIChecks PUTs as check types.
    region_start = bo.index("check-type-sel")
    region_end = bo.index("</select>", region_start)
    options = re.findall(r"<option value=\\?\"([a-z_]+)\\?\"", bo[region_start:region_end])
    assert options, "could not locate check-type select options"
    assert "presence" in options

    m = re.search(r"AI_CHECK_ALLOWED_TYPES\s*=\s*(\{[^}]*\})", server_src)
    assert m, "could not locate AI_CHECK_ALLOWED_TYPES in server.py"
    allowed = eval(m.group(1))  # literal set of str
    assert "presence" in allowed

    not_allowed = [o for o in set(options) if o not in allowed]
    assert not_allowed == [], (
        f"admin select offers types the server rejects (save would 400): {not_allowed}"
    )


# ── PG parity: no inline single-% LIKE literals on the PG path ─────

def test_no_inline_percent_like_literals_in_pg_reachable_modules():
    """Same bug class as the C0 backfill failure (and its cleanup.py
    siblings found in review): the DBConnection PostgreSQL path always
    passes params to cursor.execute, so a literal '%' inside SQL text is
    parsed by psycopg2 as a format directive and the statement throws.
    LIKE patterns must be bound as parameters (or use the '%%' doubling
    idiom). Scans code lines only; the sqlite_master scan in db.py runs on
    a raw sqlite3 cursor and is allowlisted.
    """
    import re

    pat = re.compile(r"LIKE\s+'[^']*(?<!%)%(?!%)[^']*'", re.IGNORECASE)
    offenders = []
    for rel in ("arie-backend/fixtures/cleanup.py",
                "arie-backend/server.py",
                "arie-backend/db.py"):
        for lineno, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "sqlite_master" in code:
                continue  # native sqlite3 cursor — never executes on PG
            if pat.search(code):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert offenders == [], (
        "inline single-% LIKE literal(s) would throw under psycopg2 parameter "
        "interpolation on PostgreSQL — bind the pattern as a parameter:\n"
        + "\n".join(offenders)
    )


# ── C1b: portal reference register ─────────────────────────────────

def test_portal_doc_rules_show_lic_gate():
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")
    start = html.index("'doc-license-cert': {")
    end = html.index("]", html.index("checks: [", start))
    licence_block = html[start:end]
    assert "Licence Applicability Gate" in licence_block
    assert "'lic-gate'" in licence_block
    assert licence_block.index("lic-gate") < licence_block.index("name-match")

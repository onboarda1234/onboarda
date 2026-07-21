"""P12-7 (DCI-014) — flag-gated rules-first pass for HYBRID checks.

The matrix documents HYBRID as "rules first, AI fallback only on
INCONCLUSIVE" but no deterministic pass ever existed. These tests pin:
(1) the gate is OFF by default and the evaluator registry EMPTY (both are
founder-sign-off gates); (2) with the flag off — or on with no evaluators —
the AI check set is byte-identical to legacy behaviour; (3) a registered
evaluator removes its check from the AI set fail-safely (None/raise fall
through to AI); (4) a surviving INCONCLUSIVE flags the document.
"""

import os
from pathlib import Path

import pytest

import document_verification as dv
from document_verification import (
    HYBRID_DETERMINISTIC_EVALUATORS,
    _aggregate,
    run_hybrid_deterministic_pass,
    verify_document_layered,
)
from verification_matrix import CheckClassification, CheckStatus, get_ai_checks_for_doc_type

BACKEND = Path(__file__).resolve().parents[1]


# ── Sign-off gates ───────────────────────────────────────────────────


def test_flag_default_off():
    if os.environ.get("ENABLE_HYBRID_INCONCLUSIVE_GATE"):
        pytest.skip("env explicitly sets the flag")
    import config
    assert config.ENABLE_HYBRID_INCONCLUSIVE_GATE is False
    src = (BACKEND / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("ENABLE_HYBRID_INCONCLUSIVE_GATE", "false")' in src


def test_registry_empty_pending_signoff():
    """Registering an evaluator is a compliance-logic change: it needs the
    P12-7 decision memo signed. An empty registry keeps flag-on behaviour
    identical to legacy (everything INCONCLUSIVE -> AI)."""
    assert HYBRID_DETERMINISTIC_EVALUATORS == {}
    memo = BACKEND.parent / "docs" / "compliance" / "P12_7_VERIFICATION_MATRIX_DECISION_MEMO.md"
    assert memo.exists()


# ── Runner contract (unit) ───────────────────────────────────────────


def _chk(check_id):
    return {"id": check_id, "label": check_id, "classification": CheckClassification.HYBRID}


def test_runner_no_evaluator_everything_inconclusive():
    resolved, inconclusive = run_hybrid_deterministic_pass([_chk("H1"), _chk("H2")], {})
    assert resolved == []
    assert [c["id"] for c in inconclusive] == ["H1", "H2"]


def test_runner_resolving_none_and_raising_evaluators():
    calls = []

    def resolves(chk, ctx):
        calls.append("resolves")
        return {"id": chk["id"], "label": chk["label"], "result": CheckStatus.PASS,
                "message": "deterministic pass"}

    def declines(chk, ctx):
        calls.append("declines")
        return None

    def explodes(chk, ctx):
        calls.append("explodes")
        raise RuntimeError("boom")

    HYBRID_DETERMINISTIC_EVALUATORS.update({"H1": resolves, "H2": declines, "H3": explodes})
    try:
        resolved, inconclusive = run_hybrid_deterministic_pass(
            [_chk("H1"), _chk("H2"), _chk("H3"), _chk("H4")], {"doc_type": "x"}
        )
    finally:
        HYBRID_DETERMINISTIC_EVALUATORS.clear()

    assert calls == ["resolves", "declines", "explodes"]
    assert [r["id"] for r in resolved] == ["H1"]
    # Resolved results get fail-safe defaults for provenance.
    assert resolved[0]["source"] == "rule"
    assert resolved[0]["classification"] == CheckClassification.HYBRID
    # None, raise, and unregistered all stay on the AI path.
    assert [c["id"] for c in inconclusive] == ["H2", "H3", "H4"]


# ── Aggregation fail-safe ────────────────────────────────────────────


def test_aggregate_inconclusive_fail_safe_is_gate_scoped(monkeypatch):
    """Audit finding: the aggregation fail-safe must activate WITH the gate.
    Flag off = legacy byte-identical (silent non-pass); flag on = flagged."""
    import config

    results = [
        {"id": "A", "result": CheckStatus.PASS, "message": "ok"},
        {"id": "B", "result": CheckStatus.INCONCLUSIVE,
         "message": "deterministic resolution failed"},
    ]

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", False)
    legacy = _aggregate([dict(r) for r in results])
    assert legacy["overall"] == "verified"  # frozen legacy: silent non-pass
    assert not any("deterministic resolution failed" in w for w in legacy["warnings"])

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", True)
    gated = _aggregate([dict(r) for r in results])
    assert gated["overall"] == "flagged"
    assert any("deterministic resolution failed" in w for w in gated["warnings"])


def test_runner_non_dict_outcome_falls_through_to_ai():
    """Audit finding: a truthy non-dict evaluator return must degrade to
    INCONCLUSIVE, not crash the verification run."""
    HYBRID_DETERMINISTIC_EVALUATORS["H1"] = lambda chk, ctx: True
    try:
        resolved, inconclusive = run_hybrid_deterministic_pass([_chk("H1")], {})
    finally:
        HYBRID_DETERMINISTIC_EVALUATORS.clear()
    assert resolved == []
    assert [c["id"] for c in inconclusive] == ["H1"]


# ── End-to-end: AI set unchanged unless an evaluator resolves ────────


class _StubClaude:
    def __init__(self):
        self.captured_overrides = []
        self.last_provider_failure = None
        self.last_field_extraction_timing_ms = {}

    def extract_document_fields(self, **kwargs):
        return {}

    def verify_document(self, **kwargs):
        overrides = kwargs.get("check_overrides") or []
        self.captured_overrides.append(overrides)
        return {
            "_validated": True,
            "checks": [
                {"id": c.get("id"), "label": c.get("label", ""), "result": "pass",
                 "message": "stub"} for c in overrides
            ],
        }


def _run_layered(tmp_path, stub):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 4096)
    return verify_document_layered(
        doc_type="cert_inc",
        category="entity",
        file_path=str(pdf),
        file_size=pdf.stat().st_size,
        mime_type="application/pdf",
        prescreening_data={"registered_entity_name": "Acme Corp"},
        risk_level="MEDIUM",
        existing_hashes=[],
        claude_client=stub,
        entity_name="Acme Corp",
        file_name="doc.pdf",
    )


def _captured_ids(stub):
    """ORDERED ids — the numbered check list in the Claude prompt is built
    from list order, so order changes are behaviour changes (audit finding)."""
    assert stub.captured_overrides, "stub Claude was never called"
    return [c.get("id") for c in stub.captured_overrides[-1]]


def test_flag_off_and_flag_on_empty_registry_send_identical_ai_sets(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", False)
    stub_off = _StubClaude()
    _run_layered(tmp_path, stub_off)

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", True)
    assert HYBRID_DETERMINISTIC_EVALUATORS == {}
    stub_on = _StubClaude()
    _run_layered(tmp_path, stub_on)

    assert _captured_ids(stub_off) == _captured_ids(stub_on)
    # Sanity: hybrids are genuinely in that set (the finding this guards).
    hybrid_ids = {c.get("id") for c in get_ai_checks_for_doc_type("cert_inc", "entity")
                  if c.get("classification") == CheckClassification.HYBRID}
    assert hybrid_ids & set(_captured_ids(stub_on))


def test_flag_on_registered_evaluator_removes_check_from_ai_set(tmp_path, monkeypatch):
    import config

    hybrids = [c for c in get_ai_checks_for_doc_type("cert_inc", "entity")
               if c.get("classification") == CheckClassification.HYBRID]
    assert hybrids, "cert_inc/entity must have at least one HYBRID check"
    target_id = hybrids[0]["id"]

    def evaluator(chk, ctx):
        assert ctx["doc_type"] == "cert_inc"
        return {"id": chk["id"], "label": chk.get("label", ""),
                "result": CheckStatus.PASS, "message": "resolved deterministically"}

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", True)
    HYBRID_DETERMINISTIC_EVALUATORS[target_id] = evaluator
    try:
        stub = _StubClaude()
        result = _run_layered(tmp_path, stub)
    finally:
        HYBRID_DETERMINISTIC_EVALUATORS.clear()

    assert target_id not in _captured_ids(stub)
    resolved = [c for c in result["checks"]
                if c.get("id") == target_id and c.get("source") == "rule"]
    assert len(resolved) == 1
    assert resolved[0]["result"] == CheckStatus.PASS


def test_db_override_branch_participates_in_gate(tmp_path, monkeypatch):
    """Audit-suggested: the DB check_overrides path (the priority path on
    live deployments) must run the deterministic pass too."""
    import config

    overrides = [
        {"id": "AI-X", "label": "AI-X", "classification": CheckClassification.AI},
        {"id": "HY-1", "label": "HY-1", "classification": CheckClassification.HYBRID},
        {"id": "HY-2", "label": "HY-2", "classification": CheckClassification.HYBRID},
    ]

    monkeypatch.setattr(config, "ENABLE_HYBRID_INCONCLUSIVE_GATE", True)
    HYBRID_DETERMINISTIC_EVALUATORS["HY-1"] = lambda chk, ctx: {
        "id": chk["id"], "label": chk["label"],
        "result": CheckStatus.PASS, "message": "resolved deterministically",
    }
    try:
        stub = _StubClaude()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 4096)
        result = verify_document_layered(
            doc_type="cert_inc", category="entity", file_path=str(pdf),
            file_size=pdf.stat().st_size, mime_type="application/pdf",
            prescreening_data={"registered_entity_name": "Acme Corp"},
            risk_level="MEDIUM", existing_hashes=[], claude_client=stub,
            entity_name="Acme Corp", check_overrides=overrides, file_name="doc.pdf",
        )
    finally:
        HYBRID_DETERMINISTIC_EVALUATORS.clear()

    # Order preserved, only the resolved check removed.
    assert _captured_ids(stub) == ["AI-X", "HY-2"]
    assert any(c.get("id") == "HY-1" and c.get("source") == "rule"
               for c in result["checks"])


def test_flag_wiring_reads_config_at_call_time():
    """The gate must consult config dynamically (monkeypatchable), the legacy
    selection line must survive untouched, and removal must be id-filtering
    (order-preserving), not list repartition (audit finding)."""
    src = (BACKEND / "document_verification.py").read_text(encoding="utf-8")
    assert "_hybrid_gate_enabled()" in src
    assert "ai_hybrid_checks = get_ai_checks_for_doc_type(doc_type, category)" in src
    assert "resolved_ids = {r.get(\"id\") for r in resolved}" in src
    assert "pure_ai + still_inconclusive" not in src

"""PR-E #7 item 1 — per-row evidence-cap honesty flag (frozen Screening Queue).

The monitoring-evidence batch caps each application's evidence at
_SCREENING_QUEUE_EVIDENCE_PER_APP_CAP and carries the true pre-cap count in
application_evidence_total, but the only truncation signal was a page-wide
metrics boolean the UI never read — so a truncated (capped) scan could read as
the complete evidence set. `_enrich_screening_queue_evidence` now sets a per-row
`evidence_capped` flag (+ true total + cap) and the readiness panel surfaces it.

Founder-approved frozen-surface change; additive only (new keys), so the frozen
queue guard suites stay green.
"""
import subprocess
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _row():
    return {
        "application_id": "APP1",
        "subject_type": "entity",
        "subject_name": "Acme",
        "provider_evidence": [],
    }


# --- backend -----------------------------------------------------------------

def test_evidence_capped_true_when_total_exceeds_cap():
    over = server._SCREENING_QUEUE_EVIDENCE_PER_APP_CAP + 50
    out = server._enrich_screening_queue_evidence(
        _row(), [{"_link_candidate": {}, "application_evidence_total": over}]
    )
    assert out["evidence_capped"] is True
    s = out["evidence_summary"]
    assert s["evidence_capped"] is True
    assert s["application_evidence_total"] == over
    assert s["evidence_cap"] == server._SCREENING_QUEUE_EVIDENCE_PER_APP_CAP


def test_evidence_not_capped_when_total_within_cap():
    out = server._enrich_screening_queue_evidence(
        _row(), [{"_link_candidate": {}, "application_evidence_total": 3}]
    )
    assert out["evidence_capped"] is False
    s = out["evidence_summary"]
    assert s["evidence_capped"] is False
    # true total / cap only surfaced when actually capped (no noise otherwise)
    assert s["application_evidence_total"] is None
    assert s["evidence_cap"] is None


def test_no_evidence_is_not_capped():
    out = server._enrich_screening_queue_evidence(_row(), [])
    assert out["evidence_capped"] is False


# --- frontend ----------------------------------------------------------------

def test_readiness_panel_surfaces_truncation():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    start = html.index("function screeningQueueEvidenceReadinessPanel")
    fn = html[start:html.index("\nfunction ", start)]
    assert "summary.evidence_capped" in fn
    assert "Evidence truncated" in fn
    assert "not the complete evidence set" in fn
    assert "summary.application_evidence_total" in fn

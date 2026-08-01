"""Server-side generation of the canonical Compliance Memo PDF.

The document is deliberately decision-first. Detailed calculations, raw
screening evidence, document-level checks and workflow history remain in their
authoritative modules or evidence pack.
"""
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Optional

from branding import BRAND

logger = logging.getLogger("arie")

_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
COMPLIANCE_MEMO_TITLE = BRAND["pdf_header"]

# Lazy-load WeasyPrint (heavy import)
_weasyprint = None


def _get_weasyprint():
    """Lazy-load WeasyPrint to avoid import cost on every request."""
    global _weasyprint
    if _weasyprint is None:
        import weasyprint
        _weasyprint = weasyprint
    return _weasyprint


# ══════════════════════════════════════════════════════════
# PDF STYLE SHEET — regulator-grade formatting
# ══════════════════════════════════════════════════════════

PDF_CSS = """
@page {
    size: A4;
    margin: 2.2cm 2cm 2.2cm 2cm;
    @top-right {
        content: "CONFIDENTIAL - """ + BRAND["pdf_header"] + """";
        font-size: 8pt;
        color: #888;
    }
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 8pt;
        color: #888;
    }
    @bottom-right {
        content: \"""" + BRAND["pdf_footer"] + """";
        font-size: 7pt;
        color: #aaa;
    }
}
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 9.9pt;
    line-height: 1.58;
    color: #1f2937;
}
h1 {
    font-size: 17pt;
    line-height: 1.25;
    color: #1a3a5c;
    border-bottom: 3px solid #1a3a5c;
    letter-spacing: -0.15pt;
    padding-bottom: 9px;
    margin-top: 0;
    margin-bottom: 17px;
}
h2 {
    font-size: 12.25pt;
    color: #1a3a5c;
    border-bottom: 1px solid #cbd5e1;
    letter-spacing: -0.05pt;
    padding-bottom: 5px;
    margin-top: 17px;
    page-break-after: avoid;
}
h3 {
    font-size: 10.5pt;
    color: #2c5f8a;
    margin-top: 13px;
    page-break-after: avoid;
}
p {
    margin: 7px 0;
    text-align: left;
}
.header-block {
    background: #f5f8fc;
    border: 1px solid #d0d8e4;
    border-radius: 4px;
    padding: 11px 14px;
    margin-bottom: 15px;
}
.header-block table {
    width: 100%;
    border-collapse: collapse;
}
.header-block td {
    padding: 4px 8px;
    font-size: 9.5pt;
    vertical-align: top;
}
.header-block .label {
    font-weight: bold;
    color: #555;
    width: 180px;
}
.risk-badge {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 3px;
    font-weight: bold;
    font-size: 10pt;
    color: white;
}
.risk-low { background: #27ae60; }
.risk-medium { background: #f39c12; }
.risk-high { background: #e74c3c; }
.risk-very-high { background: #8e0000; }
.risk-unrated { background: #6b7280; }
.decision-badge {
    display: inline-block;
    padding: 4px 16px;
    border-radius: 3px;
    font-weight: bold;
    font-size: 11pt;
    color: white;
    margin: 8px 0;
}
.decision-approve { background: #27ae60; }
.decision-approve-conditions { background: #f39c12; }
.decision-edd { background: #e67e22; }
.decision-reject { background: #e74c3c; }
.decision-review { background: #3498db; }
.section-content {
    margin-left: 8px;
    margin-bottom: 12px;
}
.red-flag {
    color: #c0392b;
    font-weight: bold;
}
.mitigant {
    color: #27ae60;
}
.validation-box {
    background: #fafafa;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 12px;
    margin-top: 16px;
    page-break-inside: avoid;
}
.validation-box h3 {
    margin-top: 0;
}
table.risk-table {
    width: 100%;
    border-collapse: collapse;
    margin: 8px 0;
    font-size: 9.5pt;
}
table.risk-table th, table.risk-table td {
    border: 1px solid #ccc;
    padding: 6px 10px;
    text-align: left;
}
table.risk-table th {
    background: #f0f4f8;
    color: #1a3a5c;
}
.factor-list {
    margin: 6px 0 6px 16px;
    padding: 0;
}
.factor-list li {
    margin: 3px 0;
    font-size: 9.5pt;
}
.watermark {
    text-align: center;
    color: #bbb;
    font-size: 8pt;
    margin-top: 18px;
    border-top: 1px solid #ddd;
    padding-top: 6px;
}
.watermark p { margin: 3px 0; }
.immutable-hash {
    font-family: 'Courier New', monospace;
    font-size: 7pt;
    color: #aaa;
}
.draft-watermark {
    position: fixed;
    top: 42%;
    left: 7%;
    width: 86%;
    text-align: center;
    transform: rotate(-28deg);
    color: rgba(153, 27, 27, 0.10);
    font-size: 44pt;
    font-weight: 800;
    letter-spacing: 2pt;
    z-index: -1;
}
.document-state {
    border-radius: 4px;
    color: white;
    font-size: 11pt;
    font-weight: 800;
    letter-spacing: 0.5pt;
    margin-bottom: 14px;
    padding: 8px 12px;
    text-align: center;
}
.document-state.draft { background: #991b1b; }
.document-state.final { background: #166534; }
.memo-section {
    break-inside: avoid;
    margin-top: 14px;
}
.memo-section h2 { margin-bottom: 8px; }
.memo-basis {
    background: #ffffff;
    border-bottom: 1px solid #dbe3ec;
    border-top: 1px solid #dbe3ec;
    padding: 7px 12px 8px;
}
.memo-basis p { margin: 3px 0 5px; }
.basis-list {
    columns: 2;
    column-gap: 28px;
    margin: 5px 0 7px 18px;
    padding: 0;
}
.basis-list li {
    break-inside: avoid;
    margin: 2px 0;
}
.opinion-box {
    background: #f5f8fc;
    border: 1px solid #cbd5e1;
    border-left: 5px solid #1a3a5c;
    border-radius: 4px;
    padding: 10px 13px;
}
.opinion-box p {
    font-size: 9.6pt;
    line-height: 1.55;
    margin: 5px 0;
}
.recommendation {
    border-bottom: 1px solid #d5deea;
    margin: 1px 0 11px;
    padding: 1px 0 10px;
}
.recommendation-label {
    color: #64748b;
    display: block;
    font-size: 7.8pt;
    font-weight: 700;
    letter-spacing: 0.75pt;
    margin-bottom: 3px;
    text-transform: uppercase;
}
.recommendation-value {
    color: #173a5e;
    display: block;
    font-size: 13.5pt;
    font-weight: 650;
    letter-spacing: -0.08pt;
    line-height: 1.25;
}
.two-column {
    display: table;
    table-layout: fixed;
    width: 100%;
}
.two-column > div {
    display: table-cell;
    padding-right: 10px;
    vertical-align: top;
    width: 50%;
}
.two-column > div:last-child {
    padding-left: 10px;
    padding-right: 0;
}
.content-box {
    background: #fafafa;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 10px 12px;
}
.content-box h3 { margin: 0 0 6px; }
.content-box ul { margin: 5px 0 0 18px; padding: 0; }
.content-box li { margin: 3px 0; overflow-wrap: anywhere; }
.conditions-grid .content-box { min-height: 76px; }
.condition-list {
    margin: 6px 0 0 21px;
    padding: 0;
}
.condition-list li {
    margin: 4px 0;
    padding-left: 3px;
}
.signature-grid {
    border-collapse: collapse;
    table-layout: fixed;
    width: 100%;
}
.signature-grid td {
    border: 1px solid #cbd5e1;
    overflow-wrap: anywhere;
    padding: 7px 9px;
    vertical-align: top;
}
.signature-grid .label {
    background: #f1f5f9;
    color: #475569;
    font-weight: 700;
    width: 24%;
}
.officer-rationale-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-left: 5px solid #1a3a5c;
    border-radius: 4px;
    margin: 16px 0 19px;
    min-height: 88px;
    padding: 18px 20px;
}
.officer-rationale-box.final {
    background: #f4faf6;
    border-color: #b9d8c2;
    border-left-color: #166534;
}
.officer-rationale-label {
    color: #475569;
    font-size: 8.2pt;
    font-weight: 800;
    letter-spacing: 0.8pt;
    margin-bottom: 9px;
    text-transform: uppercase;
}
.officer-rationale-box p {
    color: #172033;
    font-size: 10.6pt;
    line-height: 1.68;
    margin: 0;
}
.governance-table {
    border-collapse: collapse;
    table-layout: fixed;
    width: 100%;
}
.governance-table th, .governance-table td {
    border: 1px solid #cbd5e1;
    overflow-wrap: anywhere;
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
}
.governance-table th {
    background: #f1f5f9;
    color: #334155;
    width: 32%;
}
.document-control {
    border-collapse: collapse;
    table-layout: fixed;
    width: 100%;
}
.document-control td {
    border-bottom: 1px solid #dbe3ec;
    overflow-wrap: anywhere;
    padding: 4px 7px;
    vertical-align: top;
    width: 25%;
}
.document-control .label {
    color: #475569;
    font-size: 8.5pt;
    font-weight: 700;
    width: 18%;
}
.risk-summary-cell {
    padding: 5px 7px !important;
}
.risk-summary {
    display: table;
    table-layout: fixed;
    width: 100%;
}
.risk-metric {
    display: table-cell;
    padding-right: 10px;
    vertical-align: top;
    width: 50%;
}
.risk-metric:last-child {
    border-left: 1px solid #dbe3ec;
    padding-left: 12px;
    padding-right: 0;
}
.risk-metric-label {
    color: #64748b;
    display: block;
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 0.35pt;
    line-height: 1.25;
    margin-bottom: 3px;
    text-transform: uppercase;
}
.risk-metric-value {
    color: #1e3f63;
    display: block;
    font-size: 11pt;
    font-weight: 700;
    line-height: 1.25;
}
.timestamp {
    font-size: 8.5pt;
    white-space: nowrap;
}
"""


# ══════════════════════════════════════════════════════════
# HTML RENDERING FUNCTIONS
# ══════════════════════════════════════════════════════════

def _esc(val: Any) -> str:
    """Escape any value for safe HTML rendering."""
    if val is None:
        return "N/A"
    return escape(str(val))


def _display_timestamp(value: Any) -> str:
    """Format stored timestamps in UTC, including legacy naive UTC values."""
    if value in (None, ""):
        return "Not recorded"
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return text
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _risk_badge(level: str) -> str:
    """Render a coloured risk badge."""
    level_upper = (level or "MEDIUM").upper()
    css_class = {
        "LOW": "risk-low", "MEDIUM": "risk-medium",
        "HIGH": "risk-high", "VERY_HIGH": "risk-very-high",
        "NOT_RATED": "risk-unrated", "UNRATED": "risk-unrated",
        "NOT YET RATED": "risk-unrated",
    }.get(level_upper, "risk-unrated")
    if level_upper in ("NOT_RATED", "UNRATED", "NOT YET RATED"):
        level_upper = "NOT YET RATED"
    return f'<span class="risk-badge {css_class}">{_esc(level_upper)}</span>'


def _decision_badge(decision: str) -> str:
    """Render a coloured decision badge."""
    d = (decision or "REVIEW").upper().replace("_", " ")
    css_map = {
        "APPROVE": "decision-approve",
        "APPROVE WITH CONDITIONS": "decision-approve-conditions",
        "APPROVE_WITH_CONDITIONS": "decision-approve-conditions",
        "EDD": "decision-edd",
        "REJECT": "decision-reject",
        "REVIEW": "decision-review",
        "ESCALATE": "decision-review",
    }
    css_class = css_map.get(decision.upper() if decision else "", "decision-review")
    return f'<span class="decision-badge {css_class}">{_esc(d)}</span>'


def _decision_label(decision: Any) -> str:
    """Return a restrained, human-readable recommendation label."""
    normalised = str(decision or "REVIEW").strip().upper().replace(" ", "_")
    return {
        "APPROVE": "Approve",
        "APPROVE_WITH_CONDITIONS": "Approve with Conditions",
        "EDD": "Enhanced Due Diligence",
        "REJECT": "Reject",
        "REVIEW": "Review",
        "ESCALATE": "Escalate",
    }.get(normalised, normalised.replace("_", " "))


def _risk_classification_label(level: Any) -> str:
    """Return the authoritative risk class in professional title case."""
    normalised = str(level or "NOT_RATED").strip().upper().replace(" ", "_").replace("-", "_")
    return {
        "LOW": "Low",
        "MEDIUM": "Medium",
        "HIGH": "High",
        "VERY_HIGH": "Very High",
        "NOT_RATED": "Not Yet Rated",
        "UNRATED": "Not Yet Rated",
    }.get(normalised, normalised.replace("_", " ").title())


def _css_content(value: Any) -> str:
    """Escape dynamic text for use inside a quoted CSS generated-content value."""
    text = " ".join(str(value or "Not recorded").split())
    text = text.replace("<", "").replace(">", "")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _memo_page_css(memo_version: Any, evidence_pack_ref: Any, content_hash: str) -> str:
    """Build memo-only running header and footer styles without changing other PDFs."""
    return f"""
@page {{
    @top-right {{
        content: "CONFIDENTIAL - {_css_content(COMPLIANCE_MEMO_TITLE)}";
        font-size: 8pt;
        color: #64748b;
    }}
    @bottom-left {{
        content: "Generated by {_css_content(BRAND['platform_name'])} Compliance OS\\A Memo Version: {_css_content(memo_version)}";
        white-space: pre;
        font-size: 6.4pt;
        line-height: 1.35;
        color: #64748b;
    }}
    @bottom-center {{
        content: "Evidence Pack Reference\\A {_css_content(evidence_pack_ref)}";
        white-space: pre;
        font-size: 6.4pt;
        line-height: 1.35;
        color: #64748b;
    }}
    @bottom-right {{
        content: "Classification: CONFIDENTIAL\\A Content Hash: {_css_content(content_hash)}  |  Page " counter(page) " of " counter(pages);
        white-space: pre;
        font-size: 6.4pt;
        line-height: 1.35;
        color: #64748b;
    }}
}}
"""


def _normalise_pdf_risk_level(value: Any) -> Optional[str]:
    level = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return level if level in _VALID_RISK_LEVELS else None


def _normalise_pdf_risk_score(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > 100:
        return None
    return score


def _score_text(score: float) -> str:
    return str(int(score)) if score.is_integer() else str(round(score, 2))


def _application_pdf_risk_display(application: Optional[Dict]) -> tuple[Optional[str], Optional[float], Optional[str]]:
    app = application or {}
    level = _normalise_pdf_risk_level(app.get("final_risk_level")) or _normalise_pdf_risk_level(app.get("risk_level"))
    score = _normalise_pdf_risk_score(
        app.get("final_risk_score") if app.get("final_risk_score") not in (None, "") else app.get("risk_score")
    )
    if level and level != "LOW" and score == 0:
        score = None
    calculated_at = app.get("risk_computed_at") or app.get("updated_at") or app.get("submitted_at")
    return level, score, calculated_at


def _pdf_risk_display(metadata: Dict, application: Optional[Dict] = None) -> tuple[str, str, str]:
    """Return authoritative risk badge, score text, and timestamp."""
    app_level, app_score, app_calculated_at = _application_pdf_risk_display(application)
    if app_level and app_score is not None:
        return app_level, f"{_score_text(app_score)}/100", str(
            app_calculated_at
            or metadata.get("risk_calculated_at")
            or "Not recorded"
        )

    canonical_risk = metadata.get("canonical_risk") if isinstance(metadata.get("canonical_risk"), dict) else None
    if not canonical_risk or canonical_risk.get("available") is not True:
        return "NOT_RATED", "Not yet scored", "Not recorded"

    level = (
        metadata.get("display_risk_rating")
        or canonical_risk.get("level")
        or metadata.get("risk_rating")
        or metadata.get("aggregated_risk")
    )
    level = _normalise_pdf_risk_level(level)
    score = metadata.get("display_risk_score")
    if score in (None, ""):
        score = canonical_risk.get("score")

    numeric_score = _normalise_pdf_risk_score(score)
    if not level or numeric_score is None:
        return "NOT_RATED", "Not yet scored", "Not recorded"
    calculated_at = (
        metadata.get("risk_calculated_at")
        or canonical_risk.get("calculated_at")
        or canonical_risk.get("risk_computed_at")
        or "Not recorded"
    )
    return level, f"{_score_text(numeric_score)}/100", str(calculated_at)


def _render_section_content(content: Any) -> str:
    """Render section content — handles string or dict with 'content' key."""
    if isinstance(content, str):
        return f'<div class="section-content"><p>{_esc(content)}</p></div>'
    if isinstance(content, dict):
        parts = []
        main = content.get("content", "")
        if main:
            parts.append(f'<p>{_esc(main)}</p>')
        return f'<div class="section-content">{"".join(parts)}</div>'
    return '<div class="section-content"><p>Information not provided</p></div>'


def _render_risk_assessment(section: Dict) -> str:
    """Render the structured risk assessment section with sub-sections."""
    html = '<div class="section-content">'

    main_content = section.get("content", "")
    if main_content:
        html += f'<p>{_esc(main_content)}</p>'

    sub_sections = section.get("sub_sections", {})
    if sub_sections:
        html += '<table class="risk-table"><tr><th>Risk Dimension</th><th>Rating</th><th>Assessment</th></tr>'
        dimension_labels = {
            "jurisdiction_risk": "Jurisdiction Risk",
            "business_risk": "Business Risk",
            "transaction_risk": "Transaction Risk",
            "ownership_risk": "Ownership Risk",
            "financial_crime_risk": "Financial Crime Risk",
        }
        for key, label in dimension_labels.items():
            sub = sub_sections.get(key, {})
            rating = sub.get("rating", "N/A")
            sub_content = sub.get("content", "Not assessed")
            html += f'<tr><td><strong>{_esc(label)}</strong></td><td>{_risk_badge(rating)}</td><td>{_esc(sub_content)}</td></tr>'
        html += '</table>'

    html += '</div>'
    return html


def _render_red_flags(section: Dict) -> str:
    """Render red flags and mitigants section."""
    html = '<div class="section-content">'

    red_flags = section.get("red_flags", [])
    mitigants = section.get("mitigants", [])

    if red_flags:
        html += '<h3>Red Flags Identified</h3><ul class="factor-list">'
        for flag in red_flags:
            html += f'<li class="red-flag">{_esc(flag)}</li>'
        html += '</ul>'
    else:
        html += '<p>No red flags identified.</p>'

    if mitigants:
        html += '<h3>Mitigating Factors</h3><ul class="factor-list">'
        for m in mitigants:
            html += f'<li class="mitigant">{_esc(m)}</li>'
        html += '</ul>'

    html += '</div>'
    return html


def _render_ai_explainability(section: Dict) -> str:
    """Render AI explainability layer with risk factors."""
    html = '<div class="section-content">'
    main = section.get("content", "")
    if main:
        html += f'<p>{_esc(main)}</p>'

    increasing = section.get("risk_increasing_factors", [])
    decreasing = section.get("risk_decreasing_factors", [])

    if increasing:
        html += '<h3>Risk-Increasing Factors</h3><ul class="factor-list">'
        for f in increasing:
            html += f'<li class="red-flag">{_esc(f)}</li>'
        html += '</ul>'

    if decreasing:
        html += '<h3>Risk-Decreasing Factors</h3><ul class="factor-list">'
        for f in decreasing:
            html += f'<li class="mitigant">{_esc(f)}</li>'
        html += '</ul>'

    html += '</div>'
    return html


def _render_ownership(section: Dict) -> str:
    """Render ownership & control section with structure complexity."""
    html = '<div class="section-content">'
    main = section.get("content", "")
    if main:
        html += f'<p>{_esc(main)}</p>'
    complexity = section.get("structure_complexity", "")
    if complexity:
        html += f'<p><strong>Structure Complexity:</strong> {_esc(complexity)}</p>'
    control = section.get("control_statement", "")
    if control:
        html += f'<p><strong>Control Assessment:</strong> {_esc(control)}</p>'
    html += '</div>'
    return html


def _render_appendix_index(memo_data: Dict) -> str:
    """Render a concise index for preserved appendix evidence."""
    appendix = memo_data.get("appendix_sections")
    if not isinstance(appendix, dict) or not appendix:
        return ""
    profile = (memo_data.get("metadata") or {}).get("memo_output_profile") or {}
    html = '<h2>Appendix Evidence Index</h2><div class="section-content">'
    html += (
        "<p>Full pre-cleanup section detail is retained in the memo export payload as "
        "<strong>appendix_sections</strong>. The default PDF remains decision-first and lists the retained evidence below.</p>"
    )
    html += '<table class="risk-table"><tr><th>Retained Evidence Section</th><th>Status</th></tr>'
    for key, section in appendix.items():
        title = key.replace("_", " ").title()
        if isinstance(section, dict) and section.get("title"):
            title = section.get("title")
        html += f'<tr><td>{_esc(title)}</td><td>Retained in appendix_sections</td></tr>'
    html += "</table>"
    if profile.get("original_sections_word_count"):
        html += (
            "<p><strong>Original detail word count:</strong> "
            + _esc(profile.get("original_sections_word_count"))
            + " words. <strong>Default memo word count:</strong> "
            + _esc(profile.get("default_sections_word_count", "not recorded"))
            + " words.</p>"
        )
    html += "</div>"
    return html


def _section_text(sections: Dict, key: str, default: str = "Not recorded") -> str:
    section = sections.get(key) or {}
    if isinstance(section, dict):
        value = section.get("content")
    else:
        value = section
    text = " ".join(str(value or "").split())
    return text or default


def _text_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = " ".join(str(item or "").split())
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _render_compact_list(items: list[str], empty_text: str) -> str:
    if not items:
        return f"<p>{_esc(empty_text)}</p>"
    return "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in items) + "</ul>"


def _render_numbered_conditions(items: list[str], empty_text: str) -> str:
    if not items:
        return f"<p>{_esc(empty_text)}</p>"
    return (
        '<ol class="condition-list">'
        + "".join(f"<li>{_esc(item)}</li>" for item in items)
        + "</ol>"
    )


def _condition_groups(metadata: Dict, concerns_section: Dict) -> tuple[list[str], list[str]]:
    """Return explicit condition groups without changing the approval record."""
    mandatory = _text_list(metadata.get("mandatory_conditions"), limit=6)
    operational = _text_list(metadata.get("operational_conditions"), limit=6)
    if mandatory or operational:
        return mandatory, operational

    conditions = _text_list(metadata.get("conditions"), limit=8)
    if not conditions:
        conditions = _text_list(concerns_section.get("conditions"), limit=8)
    if not conditions:
        conditions = _text_list(concerns_section.get("approval_blockers"), limit=8)
    return conditions, []


def _screening_snapshot_text(metadata: Dict) -> str:
    snapshot = metadata.get("canonical_screening_current_summary")
    if not isinstance(snapshot, dict) or not snapshot:
        snapshot = metadata.get("screening_state_summary")
    if not isinstance(snapshot, dict) or not snapshot:
        return "Screening snapshot not recorded"
    state = (
        snapshot.get("canonical_state")
        or snapshot.get("status")
        or snapshot.get("state")
        or "not recorded"
    )
    mode = (
        snapshot.get("provider_mode")
        or snapshot.get("screening_mode")
        or snapshot.get("mode")
        or "not recorded"
    )
    captured_at = (
        snapshot.get("snapshot_at")
        or snapshot.get("screened_at")
        or snapshot.get("generated_at")
    )
    text = f"{str(state).replace('_', ' ')}; provider mode: {str(mode).replace('_', ' ')}"
    if captured_at:
        text += f"; captured: {_display_timestamp(captured_at)}"
    return text


# ══════════════════════════════════════════════════════════
# MAIN PDF GENERATION
# ══════════════════════════════════════════════════════════

def generate_memo_pdf(
    memo_data: Dict,
    application: Dict,
    validation_result: Optional[Dict] = None,
    supervisor_result: Optional[Dict] = None,
    approved_by: Optional[str] = None,
    approved_at: Optional[str] = None,
    approval_reason: Optional[str] = None,
) -> bytes:
    """Generate the canonical decision-first memo document."""
    weasyprint = _get_weasyprint()

    memo_data = memo_data if isinstance(memo_data, dict) else {}
    application = application if isinstance(application, dict) else {}
    sections = memo_data.get("sections") if isinstance(memo_data.get("sections"), dict) else {}
    metadata = memo_data.get("metadata") if isinstance(memo_data.get("metadata"), dict) else {}
    risk_level, risk_score_display, risk_calculated_at = _pdf_risk_display(metadata, application)
    decision_section = sections.get("compliance_decision") if isinstance(sections.get("compliance_decision"), dict) else {}
    decision = decision_section.get("decision") or metadata.get("approval_recommendation", "REVIEW")
    memo_version = metadata.get("memo_version", "1.0")
    memo_generated_at = metadata.get("memo_generated_at") or metadata.get("generated_at")

    app_ref = application.get("ref", "N/A")
    company_name = application.get("company_name", "Unknown Entity")
    application_snapshot = (
        metadata.get("application_snapshot_timestamp")
        or application.get("inputs_updated_at")
        or application.get("updated_at")
        or "Not recorded"
    )
    risk_model_version = (
        application.get("risk_config_version")
        or metadata.get("risk_config_version")
        or metadata.get("model_version")
        or "Not recorded"
    )
    memo_generated_display = memo_generated_at or memo_data.get("memo_generated") or "Not recorded"
    screening_snapshot = _screening_snapshot_text(metadata)
    is_final = bool(approved_by)
    document_state = "FINAL - APPROVED / LOCKED" if is_final else "DRAFT - NOT APPROVED"
    state_class = "final" if is_final else "draft"

    # Content hash for immutability verification
    content_hash = hashlib.sha256(json.dumps(memo_data, sort_keys=True).encode()).hexdigest()[:16]
    evidence_pack_ref = metadata.get("evidence_pack_reference") or app_ref

    validation_result = validation_result if isinstance(validation_result, dict) else {}
    validation_status = validation_result.get("validation_status") or metadata.get("validation_status") or "pending"
    quality_score = validation_result.get("quality_score")
    if quality_score in (None, ""):
        quality_score = metadata.get("quality_score", "Not recorded")
    quality_score_display = (
        f"{quality_score}/10"
        if isinstance(quality_score, (int, float)) and not isinstance(quality_score, bool)
        else str(quality_score)
    )
    rule_engine = metadata.get("rule_engine") if isinstance(metadata.get("rule_engine"), dict) else {}
    rule_status = rule_engine.get("engine_status") or "Not recorded"
    memo_input_hash = metadata.get("memo_input_hash") or "Not recorded"
    build = metadata.get("build") if isinstance(metadata.get("build"), dict) else {}
    renderer_build = build.get("git_sha_short") or build.get("git_sha") or "Not recorded"

    opinion = _section_text(sections, "compliance_decision", "Compliance opinion pending")
    decision_rationale = _section_text(sections, "executive_summary", "Decision rationale not recorded")
    concerns_section = sections.get("red_flags_and_mitigants") if isinstance(sections.get("red_flags_and_mitigants"), dict) else {}
    concerns = _text_list(concerns_section.get("red_flags"), limit=6)
    mitigants = _text_list(concerns_section.get("mitigants"), limit=6)
    mandatory_conditions, operational_conditions = _condition_groups(metadata, concerns_section)
    residual_risk = _section_text(sections, "risk_assessment", "Residual risk position not recorded")
    monitoring_position = _section_text(sections, "ongoing_monitoring", "Monitoring position not recorded")
    officer_rationale = " ".join(str(approval_reason or metadata.get("approval_reason") or "").split())

    # ── Build HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><style>{PDF_CSS}{_memo_page_css(memo_version, evidence_pack_ref, content_hash)}</style></head>
<body>
{'' if is_final else '<div class="draft-watermark">DRAFT - NOT APPROVED</div>'}
<h1>{_esc(COMPLIANCE_MEMO_TITLE)} - {_esc(company_name)}</h1>
<div class="document-state {state_class}">{_esc(document_state)}</div>

<h2>1. Document Control</h2>
<div class="header-block">
<table class="document-control">
<tr><td class="label">Entity name</td><td>{_esc(company_name)}</td>
    <td class="label">Application reference</td><td>{_esc(app_ref)}</td></tr>
<tr><td class="label">Memo version</td><td>{_esc(memo_version)}</td>
    <td class="label">Document status</td><td><strong>{_esc(document_state)}</strong></td></tr>
<tr><td class="label">Generated timestamp</td><td class="timestamp">{_esc(_display_timestamp(memo_generated_display))}</td>
    <td class="label">Application snapshot timestamp</td><td class="timestamp">{_esc(_display_timestamp(application_snapshot))}</td></tr>
<tr><td class="label">Risk model version</td><td>{_esc(risk_model_version)}</td>
    <td class="risk-summary-cell" colspan="2"><div class="risk-summary">
        <div class="risk-metric"><span class="risk-metric-label">Risk Classification</span><span class="risk-metric-value">{_esc(_risk_classification_label(risk_level))}</span></div>
        <div class="risk-metric"><span class="risk-metric-label">Authoritative Risk Score</span><span class="risk-metric-value">{_esc(risk_score_display)}</span></div>
    </div></td></tr>
<tr><td class="label">Screening snapshot</td><td colspan="3">{_esc(screening_snapshot)}</td></tr>
</table>
</div>

<div class="memo-section">
<h2>2. Memo Basis</h2>
<div class="memo-basis">
<p>This compliance opinion has been prepared using:</p>
<ul class="basis-list">
<li>Current Application Profile</li>
<li>Current Risk Assessment</li>
<li>Current Screening Snapshot</li>
<li>Current Document Verification Status</li>
<li>Current Enhanced Review Status</li>
</ul>
<p>This memo reflects the application state at the generation timestamp. Subsequent material changes require a new memo version.</p>
</div>
</div>

<div class="memo-section">
<h2>3. Compliance Opinion</h2>
<div class="opinion-box">
<div class="recommendation">
<span class="recommendation-label">Recommendation</span>
<span class="recommendation-value">{_esc(_decision_label(decision))}</span>
</div>
<p>{_esc(opinion)}</p>
</div>
</div>

<div class="memo-section">
<h2>4. Decision Rationale</h2>
<div class="section-content"><p>{_esc(decision_rationale)}</p></div>
</div>

<div class="memo-section">
<h2>5. Material Concerns and Mitigating Factors</h2>
<div class="two-column">
<div><div class="content-box"><h3>Material concerns</h3>{_render_compact_list(concerns, 'No material concerns recorded.')}</div></div>
<div><div class="content-box"><h3>Mitigating factors</h3>{_render_compact_list(mitigants, 'No mitigating factors recorded.')}</div></div>
</div>
</div>

<div class="memo-section">
<h2>6. Conditions Before Approval</h2>
<div class="two-column conditions-grid">
<div><div class="content-box"><h3>Mandatory Conditions</h3>{_render_numbered_conditions(mandatory_conditions, 'No mandatory conditions recorded.')}</div></div>
<div><div class="content-box"><h3>Operational Conditions</h3>{_render_numbered_conditions(operational_conditions, 'No operational conditions beyond the recorded monitoring position.')}</div></div>
</div>
</div>

<div class="memo-section">
<h2>7. Residual Risk and Monitoring Position</h2>
<div class="section-content">
<p><strong>Residual risk:</strong> {_esc(residual_risk)}</p>
<p><strong>Monitoring position:</strong> {_esc(monitoring_position)}</p>
<p><strong>Risk calculated at:</strong> {_esc(_display_timestamp(risk_calculated_at))}</p>
</div>
</div>

<div class="memo-section">
<h2>8. Officer Decision and Sign-Off</h2>
<table class="signature-grid">
<tr><td class="label">Decision status</td><td>{_esc(document_state)}</td></tr>
<tr><td class="label">Officer</td><td>{_esc(approved_by or 'Pending officer approval')}</td></tr>
<tr><td class="label">Decision timestamp</td><td>{_esc(_display_timestamp(approved_at) if approved_at else 'Not approved')}</td></tr>
<tr><td class="label">Lock status</td><td>{_esc('Locked - new evidence requires a new memo version' if is_final else 'Unlocked draft')}</td></tr>
</table>
<div class="officer-rationale-box {state_class}">
<div class="officer-rationale-label">Officer Rationale - Professional Judgement</div>
<p>{_esc(officer_rationale or 'Officer rationale pending')}</p>
</div>
</div>

<div class="memo-section">
<h2>9. Audit and Governance Metadata</h2>
<table class="governance-table">
<tr><th>Memo content hash</th><td>{_esc(content_hash)}</td></tr>
<tr><th>Memo input hash</th><td>{_esc(memo_input_hash)}</td></tr>
<tr><th>Validation result</th><td>{_esc(str(validation_status).upper())} (quality score: {_esc(quality_score_display)})</td></tr>
<tr><th>Deterministic rule status</th><td>{_esc(rule_status)}</td></tr>
<tr><th>Renderer build</th><td>{_esc(renderer_build)}</td></tr>
<tr><th>Classification</th><td>CONFIDENTIAL - regulated compliance record</td></tr>
<tr><th>Retention</th><td>Retain under the applicable compliance-record retention policy.</td></tr>
</table>
</div>
</body>
</html>"""

    # ── Render PDF ──
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    logger.info("PDF generated for %s (%s) — %d bytes, hash=%s", app_ref, company_name, len(pdf_bytes), content_hash)
    return pdf_bytes


_UUID_RE = None


def _looks_like_uuid(value: Any) -> bool:
    """True when a value is a bare UUID (a provider profile id posing as a name)."""
    global _UUID_RE
    if _UUID_RE is None:
        import re as _re
        _UUID_RE = _re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            _re.IGNORECASE,
        )
    return bool(_UUID_RE.match(str(value or "").strip()))


def _screening_report_hits(screening_report: Dict) -> list:
    """Flatten a stored screening_report into display-ready hit rows.

    Reads only already-stored provider results — no provider calls, no
    mutation. Mirrors the back-office collection order (entity, then
    directors/UBOs/intermediaries) so the PDF matches the panel.
    """
    hits = []

    def _first(d, *keys):
        for k in keys:
            v = d.get(k)
            if v:
                return v
        return ""

    def add(subject_type, subject_name, results):
        for result in (results or []):
            if not isinstance(result, dict):
                continue
            raw_name = _first(
                result, "matched_name", "name", "full_name", "entity_name",
                "caption", "title",
            ) or subject_name
            categories = result.get("categories")
            if isinstance(categories, list) and categories:
                category = str(categories[0])
            else:
                category = _first(result, "category", "match_category") or "watchlist"
            # Prefer the provider's numeric match strength (0–100); fall back to
            # the strict/relaxed confidence signal, then to "not scored".
            score = result.get("match_score")
            if not isinstance(score, (int, float)):
                score = result.get("score")
            if isinstance(score, (int, float)):
                confidence = f"{score}%"
            else:
                surfaced = str(result.get("surfaced_by_pass") or "").lower()
                if surfaced == "both":
                    confidence = "High (strict + relaxed match)"
                elif surfaced == "strict":
                    confidence = "High (strict match)"
                elif surfaced == "relaxed":
                    confidence = "Lower (relaxed match only)"
                else:
                    confidence = "Not scored by provider"
            evidence_url = ""
            evidence_snippet = ""
            for indicator in (result.get("indicators") or []):
                value = indicator.get("value") if isinstance(indicator, dict) else None
                if not isinstance(value, dict):
                    continue
                canonical = value.get("canonical_url")
                evidence_url = (
                    (canonical.get("url") if isinstance(canonical, dict) else "")
                    or value.get("url") or value.get("raw_url") or ""
                )
                snippets = value.get("snippets")
                if isinstance(snippets, list) and snippets and isinstance(snippets[0], dict):
                    evidence_snippet = snippets[0].get("text") or ""
                if evidence_url or evidence_snippet:
                    break
            evidence_url = evidence_url or _first(result, "media_url", "source_url", "url")
            hits.append({
                "subject_type": subject_type,
                "subject_name": subject_name or "Unknown subject",
                "matched_name": "Unnamed provider match" if _looks_like_uuid(raw_name) else raw_name,
                "category": category,
                "list_name": _first(
                    result, "sanctions_list", "list", "list_name", "dataset",
                    "source_list", "watchlist", "source_name",
                ),
                "reference": _first(
                    result, "id", "profile_identifier", "match_id", "reference",
                    "entity_id",
                ),
                "confidence": confidence,
                "evidence_url": evidence_url,
                "evidence_snippet": evidence_snippet,
            })

    if not isinstance(screening_report, dict):
        return hits
    company = screening_report.get("company_screening")
    if isinstance(company, dict):
        add("Entity", company.get("company_name") or "Entity", company.get("results"))
    for key, label in (
        ("director_screenings", "Director"),
        ("ubo_screenings", "UBO"),
        ("intermediary_screenings", "Intermediary"),
    ):
        for subject in (screening_report.get(key) or []):
            if not isinstance(subject, dict):
                continue
            name = _first(subject, "name", "full_name", "entity_name", "subject_name", "person_name") or "Unknown subject"
            screening = subject.get("screening") or subject.get("screening_result") or subject.get("provider_result") or subject
            results = screening.get("results") if isinstance(screening, dict) else None
            add(label, name, results)
    return hits


def _summarize_overall_flags(overall_flags, max_distinct: int = 12) -> str:
    """Collapse the per-match overall_flags list into a deduped, counted rollup.

    overall_flags carries one summary string PER provider match, so a 200-hit
    screen repeats the same handful of strings ~200 times. Joining the raw list
    spilled ~13 pages of duplicate noise into the header "Overall Flags" cell.
    Dedup in first-seen order, count each distinct flag, and cap the distinct
    count so the header stays a one-glance rollup. Content-preserving: distinct
    flag text is shown verbatim (only trailing separators/whitespace trimmed for
    display); nothing is invented, reordered by weight, or dropped silently — an
    overflow beyond the cap is reported as a residual count.
    """
    if not overall_flags:
        return "None recorded"
    counts: Dict[str, int] = {}
    order: list = []
    for flag in overall_flags:
        text = str(flag).strip().rstrip(",").strip()
        if not text:
            continue
        if text not in counts:
            counts[text] = 0
            order.append(text)
        counts[text] += 1
    if not order:
        return "None recorded"
    parts = []
    for text in order[:max_distinct]:
        n = counts[text]
        parts.append(f"{text} (×{n})" if n > 1 else text)
    display = "; ".join(parts)
    remaining = len(order) - max_distinct
    if remaining > 0:
        display += f"; …and {remaining} more distinct flag(s)"
    return display


def build_screening_report_html(
    application: Dict,
    screening_report: Dict,
    disposition_reviews: Optional[list] = None,
) -> str:
    """Build the screening-report HTML (separated from PDF render for testability)."""
    screening_report = screening_report if isinstance(screening_report, dict) else {}
    app_ref = application.get("ref", "N/A")
    company_name = application.get("company_name", "Unknown Entity")
    country = application.get("country", "N/A")

    provider = screening_report.get("screening_provider") or screening_report.get("provider") or "Unknown"
    mode = screening_report.get("screening_mode") or "unknown"
    screened_at = (str(screening_report.get("screened_at") or "Not recorded")).replace("T", " ")[:19]
    total_hits = screening_report.get("total_hits", 0)
    overall_flags = screening_report.get("overall_flags") or []
    flags_display = _summarize_overall_flags(overall_flags)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content_hash = hashlib.sha256(
        json.dumps(screening_report, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    hits = _screening_report_hits(screening_report)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><style>{PDF_CSS}</style></head>
<body>

<h1>{_esc(BRAND['pdf_header'])} — Screening Report — {_esc(company_name)}</h1>

<div class="header-block">
<table>
<tr><td class="label">Application Reference</td><td>{_esc(app_ref)}</td>
    <td class="label">Entity Name</td><td>{_esc(company_name)}</td></tr>
<tr><td class="label">Country</td><td>{_esc(country)}</td>
    <td class="label">Screening Provider</td><td>{_esc(provider)}</td></tr>
<tr><td class="label">Screening Mode</td><td>{_esc(mode)}</td>
    <td class="label">Screened At</td><td>{_esc(screened_at)}</td></tr>
<tr><td class="label">Total Provider Matches</td><td>{_esc(total_hits)}</td>
    <td class="label">Overall Flags</td><td>{_esc(flags_display)}</td></tr>
<tr><td class="label">Report Generated</td><td colspan="3">{_esc(now)}</td></tr>
</table>
</div>

<p class="advisory-note">This screening report reproduces stored provider results for officer
review. It is advisory: a screening match is not a determination. Officer disposition and, where
required, second-reviewer sign-off remain mandatory.</p>

<h2>Provider Matches ({len(hits)})</h2>
"""

    if not hits:
        html += '<p>No provider matches are recorded in the stored screening result for this application.</p>'
    else:
        html += (
            '<table class="data-table"><thead><tr>'
            '<th>#</th><th>Subject</th><th>Matched entity</th><th>Category</th>'
            '<th>List / source</th><th>Confidence</th>'
            '</tr></thead><tbody>'
        )
        for index, hit in enumerate(hits, start=1):
            html += (
                f'<tr><td>{index}</td>'
                f'<td>{_esc(hit["subject_name"])}<br><span class="muted">{_esc(hit["subject_type"])}</span></td>'
                f'<td>{_esc(hit["matched_name"])}'
                + (f'<br><span class="muted">ref {_esc(hit["reference"])}</span>' if hit["reference"] else '')
                + f'</td>'
                f'<td>{_esc(hit["category"])}</td>'
                f'<td>{_esc(hit["list_name"] or "—")}</td>'
                f'<td>{_esc(hit["confidence"])}</td></tr>'
            )
            if hit["evidence_url"] or hit["evidence_snippet"]:
                snippet = _esc(hit["evidence_snippet"]) if hit["evidence_snippet"] else ""
                link = (
                    f'<a href="{_esc(hit["evidence_url"])}">{_esc(hit["evidence_url"])}</a>'
                    if hit["evidence_url"] else "Source link not provided by provider payload."
                )
                html += (
                    f'<tr class="evidence-row"><td></td><td colspan="5">'
                    f'<span class="muted">Evidence:</span> {snippet} {link}'
                    f'</td></tr>'
                )
        html += '</tbody></table>'

    reviews = disposition_reviews or []
    if reviews:
        html += '<h2>Disposition History</h2><table class="data-table"><thead><tr>'
        html += '<th>Reviewer</th><th>Decision</th><th>Rationale</th><th>At</th></tr></thead><tbody>'
        for review in reviews:
            if not isinstance(review, dict):
                continue
            html += (
                f'<tr><td>{_esc(review.get("reviewer") or review.get("reviewed_by") or "N/A")}</td>'
                f'<td>{_esc(review.get("decision") or review.get("review_disposition") or "N/A")}</td>'
                f'<td>{_esc(review.get("rationale") or review.get("reason") or "")}</td>'
                f'<td>{_esc((str(review.get("reviewed_at") or "")).replace("T", " ")[:19])}</td></tr>'
            )
        html += '</tbody></table>'

    html += f"""
<div class="footer">
<p class="immutable-hash">Screening Content Hash: {content_hash} | Generated: {_esc(now)}</p>
</div>

</body>
</html>"""
    return html


def generate_screening_report_pdf(
    application: Dict,
    screening_report: Dict,
    disposition_reviews: Optional[list] = None,
) -> bytes:
    """Generate a regulator-grade screening report PDF from a stored screening_report."""
    weasyprint = _get_weasyprint()
    html = build_screening_report_html(application, screening_report, disposition_reviews)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    logger.info(
        "Screening report PDF generated for %s — %d bytes",
        application.get("ref", "N/A"), len(pdf_bytes),
    )
    return pdf_bytes


def generate_memo_pdf_to_file(
    memo_data: Dict,
    application: Dict,
    output_path: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Generate PDF and write to file. Returns the file path.
    If output_path is None, writes to a temp file.
    """
    pdf_bytes = generate_memo_pdf(memo_data, application, **kwargs)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".pdf", prefix="arie_memo_")
        os.close(fd)

    with open(output_path, "wb") as f:
        f.write(pdf_bytes)

    return output_path

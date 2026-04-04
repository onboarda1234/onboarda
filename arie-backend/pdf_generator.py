"""
Onboarda — Server-Side PDF Generation Engine
Sprint 3: Regulator-grade compliance memo PDF export via WeasyPrint.
Sprint 4: Branded as "Onboarda Compliance Report" / "Powered by RegMind".

Produces immutable, branded PDF snapshots of compliance memos with:
    - Onboarda/RegMind branding (config-driven)
    - Full 11-section structured memo
    - Risk rating badges, decision highlighting
    - Validation status, supervisor verdict
    - Approval metadata, generation timestamp
    - Proper page breaks, no truncation
    - Safe from HTML/script injection (all content escaped)
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
    margin: 2.5cm 2cm 2.5cm 2cm;
    @top-right {
        content: "CONFIDENTIAL \u2014 """ + BRAND["pdf_header"] + """";
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
    font-size: 10pt;
    line-height: 1.5;
    color: #222;
}
h1 {
    font-size: 18pt;
    color: #1a3a5c;
    border-bottom: 3px solid #1a3a5c;
    padding-bottom: 8px;
    margin-top: 0;
}
h2 {
    font-size: 13pt;
    color: #1a3a5c;
    border-bottom: 1px solid #ccc;
    padding-bottom: 4px;
    margin-top: 20px;
    page-break-after: avoid;
}
h3 {
    font-size: 11pt;
    color: #2c5f8a;
    margin-top: 12px;
    page-break-after: avoid;
}
p {
    margin: 6px 0;
    text-align: justify;
}
.header-block {
    background: #f5f8fc;
    border: 1px solid #d0d8e4;
    border-radius: 4px;
    padding: 16px;
    margin-bottom: 20px;
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
    margin-top: 30px;
    border-top: 1px solid #ddd;
    padding-top: 8px;
}
.immutable-hash {
    font-family: 'Courier New', monospace;
    font-size: 7pt;
    color: #aaa;
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


def _risk_badge(level: str) -> str:
    """Render a coloured risk badge."""
    level_upper = (level or "MEDIUM").upper()
    css_class = {
        "LOW": "risk-low", "MEDIUM": "risk-medium",
        "HIGH": "risk-high", "VERY_HIGH": "risk-very-high"
    }.get(level_upper, "risk-medium")
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
) -> bytes:
    """
    Generate a regulator-grade PDF from a compliance memo.

    Args:
        memo_data: The full memo dict (sections + metadata)
        application: The application row (ref, company_name, country, sector, etc.)
        validation_result: Optional validation engine output
        supervisor_result: Optional supervisor engine output
        approved_by: Name of approving officer (if approved)
        approved_at: Approval timestamp (if approved)

    Returns:
        PDF file content as bytes
    """
    weasyprint = _get_weasyprint()

    sections = memo_data.get("sections", {})
    metadata = memo_data.get("metadata", {})
    risk_level = metadata.get("risk_rating", metadata.get("aggregated_risk", "MEDIUM"))
    risk_score = metadata.get("risk_score", 0)
    decision = metadata.get("approval_recommendation", "REVIEW")
    confidence = metadata.get("confidence_level", 0)
    memo_version = metadata.get("memo_version", "1.0")

    app_ref = application.get("ref", "N/A")
    company_name = application.get("company_name", "Unknown Entity")
    country = application.get("country", "N/A")
    sector = application.get("sector", "N/A")
    entity_type = application.get("entity_type", "N/A")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Content hash for immutability verification
    content_hash = hashlib.sha256(json.dumps(memo_data, sort_keys=True).encode()).hexdigest()[:16]

    # ── Build HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><style>{PDF_CSS}</style></head>
<body>

<h1>{_esc(BRAND['pdf_header'])} — {_esc(company_name)}</h1>

<div class="header-block">
<table>
<tr><td class="label">Application Reference</td><td>{_esc(app_ref)}</td>
    <td class="label">Entity Name</td><td>{_esc(company_name)}</td></tr>
<tr><td class="label">Country of Incorporation</td><td>{_esc(country)}</td>
    <td class="label">Sector</td><td>{_esc(sector)}</td></tr>
<tr><td class="label">Entity Type</td><td>{_esc(entity_type)}</td>
    <td class="label">Risk Rating</td><td>{_risk_badge(risk_level)}</td></tr>
<tr><td class="label">Risk Score</td><td>{_esc(risk_score)}/100</td>
    <td class="label">Confidence</td><td>{_esc(round(confidence * 100, 1) if isinstance(confidence, (int, float)) else confidence)}%</td></tr>
<tr><td class="label">Decision</td><td colspan="3">{_decision_badge(decision)}</td></tr>
<tr><td class="label">Memo Version</td><td>{_esc(memo_version)}</td>
    <td class="label">Generated</td><td>{_esc(now)}</td></tr>
"""

    if approved_by:
        html += f'<tr><td class="label">Approved By</td><td>{_esc(approved_by)}</td>'
        html += f'<td class="label">Approved At</td><td>{_esc(approved_at or "N/A")}</td></tr>'

    html += """</table></div>"""

    # ── Section 1: Executive Summary ──
    html += '<h2>1. Executive Summary</h2>'
    html += _render_section_content(sections.get("executive_summary", {}))

    # ── Section 2: Client Overview ──
    html += '<h2>2. Client Overview</h2>'
    html += _render_section_content(sections.get("client_overview", {}))

    # ── Section 3: Ownership & Control ──
    html += '<h2>3. Ownership &amp; Control</h2>'
    ownership = sections.get("ownership_and_control", {})
    if isinstance(ownership, dict):
        html += _render_ownership(ownership)
    else:
        html += _render_section_content(ownership)

    # ── Section 4: Risk Assessment ──
    html += '<h2>4. Risk Assessment</h2>'
    risk_section = sections.get("risk_assessment", {})
    if isinstance(risk_section, dict) and risk_section.get("sub_sections"):
        html += _render_risk_assessment(risk_section)
    else:
        html += _render_section_content(risk_section)

    # ── Section 5: Screening Results ──
    html += '<h2>5. Screening Results</h2>'
    html += _render_section_content(sections.get("screening_results", {}))

    # ── Section 6: Document Verification ──
    html += '<h2>6. Document Verification</h2>'
    html += _render_section_content(sections.get("document_verification", {}))

    # ── Section 7: AI Explainability Layer ──
    html += '<h2>7. AI Explainability Layer</h2>'
    ai_section = sections.get("ai_explainability", {})
    if isinstance(ai_section, dict) and (ai_section.get("risk_increasing_factors") or ai_section.get("risk_decreasing_factors")):
        html += _render_ai_explainability(ai_section)
    else:
        html += _render_section_content(ai_section)

    # ── Section 8: Red Flags & Mitigants ──
    html += '<h2>8. Red Flags &amp; Mitigants</h2>'
    rf_section = sections.get("red_flags_and_mitigants", {})
    if isinstance(rf_section, dict) and (rf_section.get("red_flags") or rf_section.get("mitigants")):
        html += _render_red_flags(rf_section)
    else:
        html += _render_section_content(rf_section)

    # ── Section 9: Compliance Decision ──
    html += '<h2>9. Compliance Decision</h2>'
    decision_section = sections.get("compliance_decision", {})
    html += '<div class="section-content">'
    if isinstance(decision_section, dict):
        d = decision_section.get("decision", decision)
        html += f'<p><strong>Recommendation:</strong> {_decision_badge(d)}</p>'
        content = decision_section.get("content", "")
        if content:
            html += f'<p>{_esc(content)}</p>'
    else:
        html += _render_section_content(decision_section)
    html += '</div>'

    # ── Section 10: Ongoing Monitoring ──
    html += '<h2>10. Ongoing Monitoring &amp; Review</h2>'
    html += _render_section_content(sections.get("ongoing_monitoring", {}))

    # ── Section 11: Audit & Governance ──
    html += '<h2>11. Audit &amp; Governance</h2>'
    html += _render_section_content(sections.get("audit_and_governance", {}))

    # ── Validation & Supervisor Summary Box ──
    val_status = "N/A"
    quality_score = "N/A"
    if validation_result:
        val_status = validation_result.get("validation_status", "pending")
        quality_score = validation_result.get("quality_score", 0)

    supervisor_verdict = "N/A"
    if supervisor_result:
        supervisor_verdict = supervisor_result.get("verdict", "N/A")

    # Also check metadata for embedded results
    meta_rule = metadata.get("rule_engine", {})
    rule_status = meta_rule.get("engine_status", "N/A")

    html += f"""
    <div class="validation-box">
        <h3>Quality Assurance Summary</h3>
        <table class="risk-table">
        <tr><th>Check</th><th>Status</th></tr>
        <tr><td>Validation Engine</td><td><strong>{_esc(val_status).upper()}</strong> (Score: {_esc(quality_score)}/10)</td></tr>
        <tr><td>Supervisor Engine</td><td><strong>{_esc(supervisor_verdict)}</strong></td></tr>
        <tr><td>Rule Engine</td><td><strong>{_esc(rule_status)}</strong></td></tr>
        </table>
    </div>
    """

    # ── Immutability Footer ──
    html += f"""
    <div class="watermark">
        <p>This document is a system-generated compliance memo. It constitutes an immutable snapshot at the time of generation.</p>
        <p>Any amendments require a new memo version with full audit trail.</p>
        <p class="immutable-hash">Content Hash: {content_hash} | Generated: {_esc(now)}</p>
    </div>

</body>
</html>"""

    # ── Render PDF ──
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    logger.info("PDF generated for %s (%s) — %d bytes, hash=%s", app_ref, company_name, len(pdf_bytes), content_hash)
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

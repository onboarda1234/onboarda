"""Backend evidence pack ZIP generation for application-level exports."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import UPLOAD_DIR

try:
    import weasyprint
except Exception:  # pragma: no cover - exercised by deployment health, not unit tests
    weasyprint = None

try:
    from s3_client import get_s3_client
    HAS_S3 = True
except Exception:  # pragma: no cover - optional dependency path
    HAS_S3 = False
    get_s3_client = None


EXPORT_TYPES = {"regulator", "auditor", "internal_case", "bank_partner"}
REDACTION_LEVELS = {"full_internal", "external_redacted"}
SECTIONS = {
    "client_submission",
    "documents",
    "risk_assessment",
    "screening_summary",
    "compliance_memo",
    "officer_corrections",
    "audit_trail",
}
DEFAULT_SECTIONS = tuple(sorted(SECTIONS))
GENERATED_BY_NOTE = "Raw provider JSON is not included in this MVP export."
UNAVAILABLE_VALUE = "Value unavailable / securely stored"
ACTIVE_DOCUMENT_SQL = "COALESCE(is_current, TRUE) = TRUE"


class ExportValidationError(ValueError):
    pass


class ExportGenerationError(RuntimeError):
    pass


def validate_export_request(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    export_type = str(payload.get("export_type") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    redaction_level = str(payload.get("redaction_level") or "").strip()

    if not reason:
        raise ExportValidationError("reason is required")
    if export_type not in EXPORT_TYPES:
        raise ExportValidationError("invalid export_type")
    if redaction_level not in REDACTION_LEVELS:
        raise ExportValidationError("invalid redaction_level")

    include_sections = payload.get("include_sections", DEFAULT_SECTIONS)
    if not isinstance(include_sections, list):
        raise ExportValidationError("include_sections must be an array")
    if not include_sections:
        raise ExportValidationError("include_sections must include at least one section")
    normalized_sections = []
    for section in include_sections:
        value = str(section or "").strip()
        if value not in SECTIONS:
            raise ExportValidationError(f"unknown include_section: {value or '<empty>'}")
        if value not in normalized_sections:
            normalized_sections.append(value)

    return {
        "export_type": export_type,
        "reason": reason,
        "redaction_level": redaction_level,
        "include_sections": normalized_sections,
    }


def safe_zip_filename(*parts: Any, default: str = "file") -> str:
    clean_parts = [os.path.basename(str(part or "").strip()) for part in parts if str(part or "").strip()]
    text = "_".join(part for part in clean_parts if part)
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    if not text:
        text = default
    return text[:180]


def export_download_filename(application_ref: str, exported_at: datetime | None = None) -> str:
    exported_at = exported_at or datetime.now(timezone.utc)
    safe_ref = safe_zip_filename(application_ref, default="application")
    return f"RegMind_Evidence_Pack_{safe_ref}_{exported_at.strftime('%Y%m%d')}.zip"


def _json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _rows(db, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, params).fetchall()]


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _display(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, (dict, list)):
        return _summarize(value)
    text = str(value)
    if _looks_sensitive_raw(text):
        return UNAVAILABLE_VALUE
    return text


def _looks_sensitive_raw(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("gAAAAA") and len(text) > 60:
        return True
    if len(text) > 80 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", text):
        return True
    if len(text) > 120 and text[:1] in ("{", "["):
        return True
    lowered = text.lower()
    return "fernet" in lowered or "ciphertext" in lowered or lowered.startswith("encrypted:")


def _summarize(value: Any, max_len: int = 260) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if _looks_sensitive_raw(value):
            return UNAVAILABLE_VALUE
        text = value
    else:
        text = json.dumps(value, ensure_ascii=True, default=str, sort_keys=True)
    text = " ".join(str(text).split())
    if _looks_sensitive_raw(text):
        return UNAVAILABLE_VALUE
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _esc(value: Any) -> str:
    return html.escape(_display(value), quote=True)


def _table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>" for label, value in rows)
    return f"<table>{body}</table>"


def _section(title: str, body: str) -> str:
    return f"<h2>{_esc(title)}</h2>{body}"


def _html_doc(title: str, body: str) -> bytes:
    document = f"""
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          @page {{ size: A4; margin: 18mm 16mm; }}
          body {{ font-family: Arial, sans-serif; color: #1f2937; font-size: 10pt; line-height: 1.42; }}
          h1 {{ color: #12395d; font-size: 18pt; margin: 0 0 14px; border-bottom: 2px solid #12395d; padding-bottom: 8px; }}
          h2 {{ color: #12395d; font-size: 13pt; margin: 18px 0 8px; border-bottom: 1px solid #d1d5db; padding-bottom: 4px; }}
          h3 {{ color: #374151; font-size: 11pt; margin: 12px 0 6px; }}
          table {{ width: 100%; border-collapse: collapse; margin: 6px 0 12px; }}
          th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: top; text-align: left; }}
          th {{ width: 34%; background: #f3f4f6; }}
          ul {{ margin-top: 6px; }}
          li {{ margin-bottom: 3px; }}
          .note {{ background: #f8fafc; border: 1px solid #d1d5db; padding: 10px; }}
          .footer {{ margin-top: 24px; color: #6b7280; font-size: 8pt; border-top: 1px solid #d1d5db; padding-top: 8px; }}
        </style>
      </head>
      <body>
        <h1>{_esc(title)}</h1>
        {body}
        <div class="footer">Generated by RegMind Evidence Pack Backend MVP. {GENERATED_BY_NOTE}</div>
      </body>
    </html>
    """
    if weasyprint is None:
        return _simple_pdf_from_text(title, _html_to_text(document))
    return weasyprint.HTML(string=document).write_pdf()


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?i)</(tr|p|h[1-3]|li|table|ul)>", "\n", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?i)<t[dh][^>]*>", "  ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _pdf_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_text(text: str, width: int = 96) -> list[str]:
    wrapped: list[str] = []
    for raw_line in str(text or "").splitlines():
        words = raw_line.split()
        if not words:
            wrapped.append("")
            continue
        line = ""
        for word in words:
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                wrapped.append(line)
                line = word
        if line:
            wrapped.append(line)
    return wrapped


def _simple_pdf_from_text(title: str, text: str) -> bytes:
    """Generate a valid, uncompressed text PDF without external native libraries."""
    all_lines = [str(title or "RegMind Evidence")] + _wrap_text(text)
    lines_per_page = 48
    pages = [all_lines[i:i + lines_per_page] for i in range(0, len(all_lines), lines_per_page)] or [[]]
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    content_ids = []
    for page_lines in pages:
        commands = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
        for index, line in enumerate(page_lines):
            if index:
                commands.append("T*")
            commands.append(f"({_pdf_escape(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        content_id = add_object(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        content_ids.append(content_id)
        page_ids.append(None)

    pages_id = len(objects) + len(pages) + 1
    for index, content_id in enumerate(content_ids):
        page_payload = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids[index] = add_object(page_payload)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"))
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj_id, payload in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{obj_id} 0 obj\n".encode("ascii"))
        out.write(payload)
        out.write(b"\nendobj\n")
    xref_offset = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return out.getvalue()


def _note_pdf(section_name: str) -> bytes:
    return _html_doc(section_name, f"<p class='note'>No {html.escape(section_name)} available at export time.</p>")


def _party_name(row: dict[str, Any], intermediary: bool = False) -> str:
    if intermediary:
        return _first(row.get("entity_name"), row.get("full_name"))
    return _first(row.get("full_name"), " ".join(p for p in [row.get("first_name"), row.get("last_name")] if p))


def _load_case(db, app: dict[str, Any]) -> dict[str, Any]:
    app_id = app["id"]
    prescreening = _json_loads(app.get("prescreening_data"), {})
    risk_dimensions = _json_loads(app.get("risk_dimensions"), {})
    directors = _rows(db, "SELECT * FROM directors WHERE application_id = ? ORDER BY created_at ASC, id ASC", (app_id,))
    ubos = _rows(db, "SELECT * FROM ubos WHERE application_id = ? ORDER BY created_at ASC, id ASC", (app_id,))
    intermediaries = _rows(db, "SELECT * FROM intermediaries WHERE application_id = ? ORDER BY created_at ASC, id ASC", (app_id,))
    documents = _rows(
        db,
        f"SELECT * FROM documents WHERE application_id = ? AND {ACTIVE_DOCUMENT_SQL} ORDER BY uploaded_at DESC, id DESC",
        (app_id,),
    )
    corrections = _rows(
        db,
        """
        SELECT * FROM application_corrections
        WHERE application_id = ?
        ORDER BY corrected_at DESC, id DESC
        """,
        (app_id,),
    )
    screening_reviews = _rows(
        db,
        "SELECT * FROM screening_reviews WHERE application_id = ? ORDER BY updated_at DESC, created_at DESC, id DESC",
        (app_id,),
    )
    memo = _row_dict(db.execute(
        "SELECT * FROM compliance_memos WHERE application_id = ? ORDER BY version DESC, id DESC LIMIT 1",
        (app_id,),
    ).fetchone())
    audit = _rows(
        db,
        """
        SELECT * FROM audit_log
        WHERE target IN (?, ?)
        ORDER BY timestamp ASC, id ASC
        LIMIT 5000
        """,
        (app["ref"], f"application:{app['ref']}"),
    )
    return {
        "application": app,
        "prescreening": prescreening if isinstance(prescreening, dict) else {},
        "risk_dimensions": risk_dimensions if isinstance(risk_dimensions, dict) else {},
        "directors": directors,
        "ubos": ubos,
        "intermediaries": intermediaries,
        "documents": documents,
        "corrections": corrections,
        "screening_reviews": screening_reviews,
        "memo": memo,
        "audit": audit,
    }


def _evidence_inventory(case: dict[str, Any]) -> str:
    items = [
        ("Directors", len(case["directors"])),
        ("UBOs", len(case["ubos"])),
        ("Intermediaries", len(case["intermediaries"])),
        ("Uploaded documents", len(case["documents"])),
        ("Officer corrections", len(case["corrections"])),
        ("Screening review records", len(case["screening_reviews"])),
        ("Audit events", len(case["audit"])),
    ]
    return "<ul>" + "".join(f"<li>{_esc(label)}: {_esc(count)}</li>" for label, count in items) + "</ul>"


def render_case_summary(case: dict[str, Any]) -> bytes:
    app = case["application"]
    body = _table([
        ("Application reference", app.get("ref")),
        ("Company name", app.get("company_name")),
        ("Status/stage", app.get("status")),
        ("Assigned officer", app.get("assigned_to")),
        ("Entity type", app.get("entity_type")),
        ("Incorporation country", _first(app.get("country"), case["prescreening"].get("country_of_incorporation"))),
        ("Sector/industry", _first(app.get("sector"), case["prescreening"].get("sector"))),
        ("Ownership structure", app.get("ownership_structure")),
        ("Risk score", app.get("risk_score")),
        ("Risk level", app.get("final_risk_level") or app.get("risk_level")),
        ("Latest decision status", _first(app.get("pre_approval_decision"), app.get("decision_notes"))),
        ("Created", app.get("created_at")),
        ("Submitted", app.get("submitted_at")),
    ])
    body += _section("Evidence Inventory", _evidence_inventory(case))
    return _html_doc("Case Summary", body)


def render_client_submission(case: dict[str, Any]) -> bytes:
    app = case["application"]
    ps = case["prescreening"]
    corrections = _latest_correction_values(case["corrections"])
    fields = [
        ("registered_entity_name", "Registered entity name", _first(ps.get("registered_entity_name"), app.get("company_name"))),
        ("trading_name", "Trading name", ps.get("trading_name")),
        ("entity_type", "Entity type", _first(ps.get("entity_type"), app.get("entity_type"))),
        ("country_of_incorporation", "Incorporation country", _first(ps.get("country_of_incorporation"), app.get("country"))),
        ("sector", "Sector/industry", _first(ps.get("sector"), app.get("sector"))),
        ("ownership_structure", "Ownership structure", _first(ps.get("ownership_structure"), app.get("ownership_structure"))),
        ("introduction_method", "Introduction/referrer", _first(ps.get("introduction_method"), ps.get("referrer"))),
        ("expected_activity", "Expected activity/transaction profile", _first(ps.get("expected_activity"), ps.get("transaction_profile"), ps.get("monthly_volume"))),
    ]
    rows = []
    for key, label, original in fields:
        corrected = corrections.get(key)
        if corrected is not None and corrected != original:
            rows.append((label, f"Original submitted value: {_display(original)}\nOfficer-corrected value: {_display(corrected)}"))
        else:
            rows.append((label, original))
    body = _table(rows)
    for title, key, intermediary in (
        ("Directors", "directors", False),
        ("UBOs", "ubos", False),
        ("Intermediaries", "intermediaries", True),
    ):
        body += f"<h2>{_esc(title)}</h2>"
        if not case[key]:
            body += f"<p class='note'>No {html.escape(title)} recorded at export time.</p>"
            continue
        for row in case[key]:
            body += _table([
                ("Name", _party_name(row, intermediary=intermediary)),
                ("Nationality/jurisdiction", _first(row.get("nationality"), row.get("jurisdiction"))),
                ("Ownership %", row.get("ownership_pct")),
                ("Client-declared PEP", row.get("is_pep")),
            ])
    return _html_doc("Client Submission", body)


def _latest_correction_values(corrections: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in reversed(corrections):
        after = _json_loads(row.get("after_state"), {})
        if isinstance(after, dict):
            for key, value in after.items():
                if key not in {"risk_after", "memo_after", "source_surface", "portal_visible"}:
                    values[key] = value
    return values


def render_risk_assessment(case: dict[str, Any]) -> bytes:
    app = case["application"]
    body = _table([
        ("Risk score", app.get("risk_score")),
        ("Risk level", app.get("final_risk_level") or app.get("risk_level")),
        ("Base risk level", app.get("base_risk_level")),
        ("Onboarding lane", app.get("onboarding_lane")),
        ("Risk recomputation timestamp", app.get("risk_computed_at") or app.get("updated_at")),
        ("Risk dimensions", case["risk_dimensions"] or "N/A"),
        ("Risk factors", _first(case["prescreening"].get("risk_factors"), case["prescreening"].get("risk_flags"))),
        ("Floor/elevation rules", _first(app.get("elevation_reason_text"), case["prescreening"].get("elevation_rules"))),
    ])
    risk_changes = []
    for row in case["corrections"]:
        before = _json_loads(row.get("before_state"), {})
        after = _json_loads(row.get("after_state"), {})
        downstream = _json_loads(row.get("downstream_state"), {})
        if isinstance(before, dict) and isinstance(after, dict) and ("risk_before" in before or "risk_after" in after):
            risk_changes.append((row.get("corrected_at"), downstream.get("risk_impact") or after.get("risk_after") or "Risk changed"))
    if risk_changes:
        body += _section("Risk Before/After From Corrections", _table([(ts, summary) for ts, summary in risk_changes[:20]]))
    return _html_doc("Risk Assessment", body)


def render_screening_summary(case: dict[str, Any], redaction_level: str) -> bytes:
    ps = case["prescreening"]
    truth = ps.get("screening_truth_summary") if isinstance(ps.get("screening_truth_summary"), dict) else {}
    report = ps.get("screening_report") if isinstance(ps.get("screening_report"), dict) else {}
    body = _table([
        ("Screening status summary", _first(truth.get("state"), report.get("status"), ps.get("screening_status"))),
        ("Sanctions summary", _first(truth.get("sanctions"), report.get("sanctions_summary"))),
        ("PEP summary", _first(truth.get("pep"), report.get("pep_summary"))),
        ("Adverse media summary", _first(truth.get("adverse_media"), report.get("adverse_media_summary"))),
        ("Screening freshness", _first(ps.get("screening_freshness"), ps.get("screening_last_run_at"))),
        ("Provider references", _first(report.get("provider_reference"), report.get("case_id"))),
    ])
    if case["screening_reviews"]:
        review_rows = []
        for review in case["screening_reviews"]:
            summary = [
                f"Disposition: {_display(review.get('disposition'))}",
                f"Code: {_display(review.get('disposition_code'))}",
            ]
            if redaction_level == "full_internal":
                summary.append(f"Rationale: {_display(review.get('rationale') or review.get('notes'))}")
            summary.append(f"Second review: {_display(review.get('second_disposition_code'))}")
            review_rows.append((review.get("subject_name"), "; ".join(summary)))
        body += _section("Screening Review Dispositions", _table(review_rows))
    return _html_doc("Screening Summary", body)


def render_officer_corrections(case: dict[str, Any]) -> bytes:
    if not case["corrections"]:
        return _html_doc("Officer Corrections", "<p class='note'>No officer corrections recorded at export time.</p>")
    rows = []
    for row in case["corrections"]:
        before = _json_loads(row.get("before_state"), {})
        after = _json_loads(row.get("after_state"), {})
        downstream = _json_loads(row.get("downstream_state"), {})
        field_scope = str(row.get("field_scope") or "")
        old_values, new_values = _correction_value_summaries(before, after, field_scope)
        rows.append((
            row.get("corrected_at"),
            "<br>".join([
                f"Officer/actor: {_esc(row.get('corrected_by_name') or row.get('corrected_by'))} ({_esc(row.get('corrected_by_role'))})",
                f"Entity type: {_esc(row.get('target_type'))}",
                f"Field changed: {_esc(field_scope)}",
                f"Old value: {_esc(old_values)}",
                f"New value: {_esc(new_values)}",
                f"Reason: {_esc(row.get('correction_reason'))}",
                f"Risk impact: {_esc((downstream or {}).get('risk_impact'))}",
                f"Memo impact: {_esc((downstream or {}).get('memo_impact'))}",
            ]),
        ))
    body = "<table><tr><th>Correction date/time</th><th>Details</th></tr>"
    body += "".join(f"<tr><td>{_esc(ts)}</td><td>{detail}</td></tr>" for ts, detail in rows)
    body += "</table>"
    return _html_doc("Officer Corrections", body)


def _correction_value_summaries(before: Any, after: Any, field_scope: str) -> tuple[str, str]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    fields = [f.strip() for f in str(field_scope or "").split(",") if f.strip()]
    if not fields:
        fields = sorted({*before.keys(), *after.keys()} - {"risk_before", "risk_after", "memo_before", "memo_after"})
    old_items = []
    new_items = []
    for field in fields[:12]:
        old_items.append(f"{field}: {_display(before.get(field))}")
        new_items.append(f"{field}: {_display(after.get(field))}")
    return "; ".join(old_items) or "N/A", "; ".join(new_items) or "N/A"


def render_compliance_memo(case: dict[str, Any]) -> bytes:
    memo = case.get("memo") or {}
    if not memo:
        return _html_doc("Compliance Memo", "<p class='note'>Compliance memo not generated at export time.</p>")
    memo_data = _json_loads(memo.get("memo_data"), {})
    body = _table([
        ("Memo version", _first(memo.get("memo_version"), memo.get("version"))),
        ("Review status", memo.get("review_status")),
        ("Validation status", memo.get("validation_status")),
        ("Quality score", memo.get("quality_score")),
        ("Approved by", memo.get("approved_by")),
        ("Approved at", memo.get("approved_at")),
        ("Created", memo.get("created_at")),
        ("Stale", memo.get("is_stale")),
        ("Stale reason", memo.get("stale_reason")),
    ])
    if isinstance(memo_data, dict):
        sections = memo_data.get("sections") if isinstance(memo_data.get("sections"), dict) else memo_data
        for key, value in list(sections.items())[:20]:
            if key in {"raw_provider_json", "provider_payload", "screening_payload"}:
                continue
            body += _section(str(key).replace("_", " ").title(), f"<p>{_esc(_summarize(value, max_len=1800))}</p>")
    return _html_doc("Compliance Memo", body)


def render_audit_trail_csv(case: dict[str, Any], redaction_level: str) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "actor", "role", "action", "target", "summary", "before_state_summary", "after_state_summary", "source_surface"])
    for row in case["audit"]:
        detail = _json_loads(row.get("detail"), {})
        if not isinstance(detail, dict):
            detail_summary = _summarize(row.get("detail"), 500)
            source_surface = ""
        else:
            detail_summary = _summarize({k: v for k, v in detail.items() if k not in {"before_state", "after_state"}}, 500)
            source_surface = detail.get("source_surface") or detail.get("path") or ""
        before = row.get("before_state", "")
        after = row.get("after_state", "")
        if redaction_level == "external_redacted":
            before = ""
            after = ""
        writer.writerow([
            row.get("timestamp"),
            row.get("user_name") or row.get("user_id"),
            row.get("user_role"),
            row.get("action"),
            row.get("target"),
            detail_summary,
            _summarize(before, 300),
            _summarize(after, 300),
            source_surface,
        ])
    return output.getvalue().encode("utf-8")


def _resolve_upload_document_path(stored_path: str | None) -> str | None:
    if not stored_path:
        return None
    raw_path = str(stored_path).strip()
    if not raw_path:
        return None
    upload_root = Path(UPLOAD_DIR).resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = upload_root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved != upload_root and upload_root not in resolved.parents:
        return None
    return str(resolved)


def _document_bytes(doc: dict[str, Any]) -> tuple[bytes | None, str | None]:
    s3_key = doc.get("s3_key")
    if s3_key and HAS_S3 and get_s3_client is not None:
        try:
            ok, data = get_s3_client().download_document(s3_key)
            if ok and isinstance(data, bytes):
                return data, None
            return None, str(data)
        except Exception as exc:
            return None, str(exc)
    file_path = _resolve_upload_document_path(doc.get("file_path"))
    if not file_path or not os.path.isfile(file_path):
        return None, "Document file not found on server"
    try:
        with open(file_path, "rb") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, str(exc)


def _uploaded_document_entries(case: dict[str, Any]) -> tuple[list[tuple[str, bytes]], list[str]]:
    entries = []
    failures = []
    seen_names: set[str] = set()
    if not case["documents"]:
        entries.append(("08_uploaded_documents/README.txt", b"No uploaded documents available at export time.\n"))
        return entries, failures
    for index, doc in enumerate(case["documents"], start=1):
        data, error = _document_bytes(doc)
        safe_name = safe_zip_filename(doc.get("doc_type"), doc.get("doc_name"), default=f"document_{index}")
        if safe_name in seen_names:
            stem, ext = os.path.splitext(safe_name)
            safe_name = f"{stem}_{index}{ext}"
        seen_names.add(safe_name)
        if data is None:
            failures.append(f"{doc.get('doc_name') or doc.get('id')}: {error}")
            continue
        entries.append((f"08_uploaded_documents/{safe_name}", data))
    if not entries:
        entries.append(("08_uploaded_documents/README.txt", b"No uploaded documents could be retrieved at export time.\n"))
    return entries, failures


def _manifest_pdf(
    case: dict[str, Any],
    request: dict[str, Any],
    actor: dict[str, Any],
    exported_at: datetime,
    zip_files: list[dict[str, Any]],
    retrieval_failures: list[str],
) -> bytes:
    app = case["application"]
    files_html = "<ul>" + "".join(
        f"<li>{_esc(item['path'])} - SHA256 {_esc(item['sha256'])}</li>" for item in zip_files
    ) + "</ul>"
    failures_html = ""
    if retrieval_failures:
        failures_html = _section("Document Retrieval Failures", "<ul>" + "".join(f"<li>{_esc(f)}</li>" for f in retrieval_failures) + "</ul>")
    body = _table([
        ("Application reference", app.get("ref")),
        ("Company/client name", app.get("company_name")),
        ("Export type", request["export_type"]),
        ("Redaction level", request["redaction_level"]),
        ("Export reason", request["reason"]),
        ("Exported by", f"{actor.get('name') or actor.get('sub')} / {actor.get('email', '')} / {actor.get('role')}"),
        ("Export timestamp", exported_at.isoformat()),
        ("Application status", app.get("status")),
        ("Risk score", app.get("risk_score") if request["redaction_level"] == "full_internal" else "Redacted"),
        (
            "Risk level",
            (app.get("final_risk_level") or app.get("risk_level"))
            if request["redaction_level"] == "full_internal"
            else "Redacted",
        ),
        ("Included sections", ", ".join(request["include_sections"])),
    ])
    body += _section("Files Included In ZIP", files_html)
    body += _section("MVP Notes", f"<p>{_esc(GENERATED_BY_NOTE)} Redaction is conservative and does not replace a formal redaction review.</p>")
    body += failures_html
    return _html_doc("RegMind Evidence Pack", body)


def build_evidence_pack_zip(
    db,
    app: dict[str, Any],
    request: dict[str, Any],
    actor: dict[str, Any],
    exported_at: datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    exported_at = exported_at or datetime.now(timezone.utc)
    case = _load_case(db, app)
    root = f"RegMind_Evidence_Pack_{safe_zip_filename(app.get('ref'), default='application')}"
    files: list[tuple[str, bytes]] = []

    def add(path: str, data: bytes) -> None:
        files.append((f"{root}/{path}", data))

    add("01_case_summary.pdf", render_case_summary(case))
    sections = set(request["include_sections"])
    if "client_submission" in sections:
        add("02_client_submission.pdf", render_client_submission(case))
    if "risk_assessment" in sections:
        add("03_risk_assessment.pdf", render_risk_assessment(case))
    if "screening_summary" in sections:
        add("04_screening_summary.pdf", render_screening_summary(case, request["redaction_level"]))
    if "officer_corrections" in sections:
        add("05_officer_corrections.pdf", render_officer_corrections(case))
    if "compliance_memo" in sections:
        add("06_compliance_memo.pdf", render_compliance_memo(case))
    if "audit_trail" in sections:
        add("07_audit_trail.csv", render_audit_trail_csv(case, request["redaction_level"]))

    retrieval_failures: list[str] = []
    if "documents" in sections:
        document_entries, retrieval_failures = _uploaded_document_entries(case)
        for path, data in document_entries:
            add(path, data)

    file_manifest = [
        {"path": path.replace(f"{root}/", "", 1), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for path, data in files
    ]
    manifest = _manifest_pdf(case, request, actor, exported_at, file_manifest, retrieval_failures)
    files.insert(0, (f"{root}/00_manifest.pdf", manifest))
    file_manifest.insert(0, {
        "path": "00_manifest.pdf",
        "sha256": hashlib.sha256(manifest).hexdigest(),
        "bytes": len(manifest),
    })

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in files:
            zf.writestr(path, data)
    zip_bytes = out.getvalue()
    metadata = {
        "file_count": len(files),
        "zip_sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "files": file_manifest,
        "document_retrieval_failures": retrieval_failures,
        "exported_at": exported_at.isoformat(),
    }
    return zip_bytes, metadata

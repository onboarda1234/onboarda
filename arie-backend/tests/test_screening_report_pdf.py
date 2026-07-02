"""RPT-1 — Screening report PDF.

Unit tests for the screening-report HTML builder and hit flattener. These
exercise the pure builders (no weasyprint, no server, no provider calls) so the
report renders correctly from a stored screening_report only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdf_generator


def _app():
    return {"ref": "ARF-2026-RPT01", "company_name": "Report Co Ltd", "country": "MU"}


def _report():
    return {
        "screening_provider": "complyadvantage",
        "screening_mode": "live",
        "screened_at": "2026-06-30T09:58:18+00:00",
        "total_hits": 3,
        "overall_flags": ["sanctions", "adverse_media"],
        "company_screening": {
            "company_name": "Report Co Ltd",
            "matched": True,
            "results": [
                {
                    "name": "Report Holdings",
                    "category": "sanctions",
                    "sanctions_list": "OFAC SDN",
                    "id": "prov-ref-1",
                    "surfaced_by_pass": "strict",
                },
                {
                    # UUID posing as a name -> readability fallback
                    "matched_name": "019f185a-2a5d-7bfb-a85b-c1cfad8e5c5d",
                    "category": "adverse_media",
                    "surfaced_by_pass": "relaxed",
                    "indicators": [
                        {"value": {"canonical_url": {"url": "https://news.example/probe"},
                                   "snippets": [{"text": "named in a laundering probe"}]}}
                    ],
                },
            ],
        },
        "director_screenings": [
            {"name": "Jane Director",
             "screening": {"results": [{"name": "Jane Director", "category": "pep", "match_score": 88}]}},
        ],
    }


def test_flatten_hits_shape_and_uuid_fallback():
    hits = pdf_generator._screening_report_hits(_report())
    assert len(hits) == 3
    by_entity = {h["matched_name"]: h for h in hits}
    # sanctions entity carried through with list + strict confidence
    assert by_entity["Report Holdings"]["category"] == "sanctions"
    assert by_entity["Report Holdings"]["list_name"] == "OFAC SDN"
    assert "strict" in by_entity["Report Holdings"]["confidence"].lower()
    # UUID matched_name replaced with readable fallback
    assert "Unnamed provider match" in by_entity
    fp = by_entity["Unnamed provider match"]
    assert fp["evidence_url"] == "https://news.example/probe"
    assert "laundering probe" in fp["evidence_snippet"]
    assert "relaxed" in fp["confidence"].lower()
    # director hit flattened with numeric score fallback
    jane = by_entity["Jane Director"]
    assert jane["subject_type"] == "Director"
    assert jane["confidence"] == "88%"


def test_build_html_contains_key_sections():
    html = pdf_generator.build_screening_report_html(_app(), _report())
    assert "Screening Report" in html
    assert "Report Co Ltd" in html
    assert "complyadvantage" in html
    assert "Provider Matches (3)" in html
    assert "Report Holdings" in html
    assert "Unnamed provider match" in html
    # advisory framing must be present (officer disposition required)
    assert "advisory" in html.lower()
    assert "disposition" in html.lower()
    # evidence link surfaced
    assert "https://news.example/probe" in html


def test_build_html_empty_report():
    html = pdf_generator.build_screening_report_html(_app(), {})
    assert "Provider Matches (0)" in html
    assert "No provider matches" in html


def test_flatten_ignores_non_dict_and_missing():
    assert pdf_generator._screening_report_hits({}) == []
    assert pdf_generator._screening_report_hits(None) == []
    weird = {"company_screening": {"company_name": "X", "results": ["not-a-dict", None]}}
    assert pdf_generator._screening_report_hits(weird) == []


def test_uuid_detector():
    assert pdf_generator._looks_like_uuid("019f185a-2a5d-7bfb-a85b-c1cfad8e5c5d")
    assert not pdf_generator._looks_like_uuid("Report Holdings")
    assert not pdf_generator._looks_like_uuid("")


def test_generate_screening_report_pdf_uses_weasyprint(monkeypatch):
    # The render entrypoint must build the HTML and hand it to WeasyPrint's
    # HTML(...).write_pdf(), returning the produced bytes. Mock WeasyPrint so
    # the test does not require the native library.
    captured = {}

    class _FakeHTML:
        def __init__(self, string=None):
            captured["string"] = string

        def write_pdf(self):
            return b"%PDF-fake"

    monkeypatch.setattr(
        pdf_generator, "_get_weasyprint",
        lambda: type("W", (), {"HTML": _FakeHTML})(),
    )
    pdf_bytes = pdf_generator.generate_screening_report_pdf(_app(), _report())
    assert pdf_bytes == b"%PDF-fake"
    # confirms the HTML builder ran and its output was passed through
    assert "Provider Matches (3)" in captured["string"]

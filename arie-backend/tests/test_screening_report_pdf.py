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


def test_summarize_overall_flags_dedup_and_count():
    # A 200-hit screen repeats the same handful of flag strings ~200 times.
    # The rollup must dedup (first-seen order), count, and trim trailing commas
    # — NOT spill 200 near-identical lines into the header (the 13-page bug).
    flags = (
        ["ComplyAdvantage adverse media hit: wirecard,"] * 180
        + ["ComplyAdvantage adverse media hit: jan marsalek,"] * 20
    )
    out = pdf_generator._summarize_overall_flags(flags)
    assert out == (
        "ComplyAdvantage adverse media hit: wirecard (×180); "
        "ComplyAdvantage adverse media hit: jan marsalek (×20)"
    )
    # deduped to two distinct lines, not 200
    assert out.count("(×") == 2
    assert "wirecard,;" not in out  # trailing comma trimmed for display


def test_summarize_overall_flags_singletons_unchanged():
    # Distinct one-off flags render verbatim, no count suffix.
    out = pdf_generator._summarize_overall_flags(["sanctions", "adverse_media"])
    assert out == "sanctions; adverse_media"
    assert "(×" not in out


def test_summarize_overall_flags_empty_and_blank():
    assert pdf_generator._summarize_overall_flags([]) == "None recorded"
    assert pdf_generator._summarize_overall_flags(None) == "None recorded"
    assert pdf_generator._summarize_overall_flags(["", "  ", ","]) == "None recorded"


def test_summarize_overall_flags_trailing_comma_variants_merge():
    # Trailing-comma provider-format artifacts are noise, not distinct meaning:
    # "A," and "A" collapse to one counted entry (intent lock for the rstrip).
    out = pdf_generator._summarize_overall_flags(["sanctions,", "sanctions"])
    assert out == "sanctions (×2)"


def test_summarize_overall_flags_coerces_non_str_members():
    # Non-string members must not raise (matches the prior str() contract).
    out = pdf_generator._summarize_overall_flags([None, 7, None])
    assert "None" in out and "7" in out


def test_summarize_overall_flags_caps_distinct_with_residual():
    # Many DISTINCT flags are capped; the overflow is reported, never dropped silently.
    flags = [f"flag {i}" for i in range(20)]
    out = pdf_generator._summarize_overall_flags(flags, max_distinct=12)
    assert "flag 0" in out and "flag 11" in out
    assert "flag 12" not in out
    assert "…and 8 more distinct flag(s)" in out


def test_build_html_header_not_flooded_by_repeated_flags():
    # End-to-end: a 200-repeat report must NOT put 200 flag lines in the HTML.
    report = _report()
    report["overall_flags"] = ["ComplyAdvantage adverse media hit: wirecard,"] * 200
    html = pdf_generator.build_screening_report_html(_app(), report)
    assert "(×200)" in html
    assert html.count("adverse media hit: wirecard") == 1


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

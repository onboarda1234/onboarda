import argparse
import json

import pytest

from scripts.qa import day5_closing_smoke as smoke


def test_reconciliation_requires_all_lifecycle_buckets():
    classified = smoke._check_reconciliation({
        "total": 4,
        "pending": 2,
        "edd_required": 1,
        "approved": 1,
        "rejected": 0,
        "withdrawn": 0,
    })

    assert classified == 4


def test_reconciliation_fails_when_buckets_do_not_sum_to_total():
    with pytest.raises(smoke.SmokeFailure, match="classified total"):
        smoke._check_reconciliation({
            "total": 5,
            "pending": 2,
            "edd_required": 1,
            "approved": 1,
            "rejected": 0,
            "withdrawn": 0,
        })


def test_csv_export_record_count_uses_header(monkeypatch):
    body = "\ufeffref,status\nARF-1,draft\nARF-2,edd_required\n".encode("utf-8")

    def fake_request(api_base, path, token=None, accept=None):
        assert path.startswith("/reports/generate?format=csv&")
        assert "fields=ref%2Ccompany_name" in path
        assert accept == "text/csv"
        return smoke.HttpResponse(
            200,
            {
                "X-Report-Record-Count": "2",
                "X-Report-Canonical-View": "applications_report_v1",
                "X-Report-Field-List": smoke.DEFAULT_FIELDS,
            },
            body,
        )

    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke._check_csv_export("https://example.test/api", "token", False) == {
        "rows": 2,
        "canonical_view": "applications_report_v1",
        "field_list": smoke.DEFAULT_FIELDS,
    }


def test_extract_applications_accepts_known_response_shapes():
    rows = [{"status": "draft"}]

    assert smoke._extract_applications({"applications": rows}) == rows
    assert smoke._extract_applications({"data": rows}) == rows
    assert smoke._extract_applications({"items": rows}) == rows
    assert smoke._extract_applications({"results": rows}) == rows


def test_kpi_data_check_uses_pending_and_edd_contracts(monkeypatch):
    payload = {
        "applications": [
            {"status": "draft"},
            {"status": "pricing_review"},
            {"status": "edd_required"},
            {"status": "approved"},
        ]
    }

    def fake_request(api_base, path, token=None, accept=None):
        assert path.startswith("/applications?")
        return smoke.HttpResponse(200, {}, json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(smoke, "_request", fake_request)

    assert smoke._check_kpi_data(
        "https://example.test/api",
        "token",
        ["draft", "pricing_review"],
        ["edd_required"],
        expected_pending=2,
        expected_edd=1,
        show_fixtures=False,
    ) == {"pending": 2, "edd": 1}


def test_run_smoke_checks_core_day5_gates(monkeypatch):
    responses = {
        "/version": {"git_sha": "abc123456"},
        "/reports/analytics": {
            "summary": {
                "total": 4,
                "pending": 2,
                "edd_required": 1,
                "approved": 1,
                "rejected": 0,
                "withdrawn": 0,
            },
            "report": {
                "pending_statuses": ["draft", "pricing_review"],
                "edd_routed_statuses": ["edd_required"],
                "canonical_view": "applications_report_v1",
            },
        },
        "/dashboard": {
            "early_stage_applications": 2,
            "in_progress_applications": 2,
            "edd": 1,
            "pending_statuses": ["draft", "pricing_review"],
            "canonical_view": "applications_report_v1",
        },
        "/applications": {
            "applications": [
                {"status": "draft"},
                {"status": "pricing_review"},
                {"status": "edd_required"},
                {"status": "approved"},
            ]
        },
    }
    csv_body = "\ufeffref,status\nARF-1,draft\nARF-2,pricing_review\nARF-3,edd_required\nARF-4,approved\n".encode("utf-8")

    def fake_request(api_base, path, token=None, accept=None):
        key = path.split("?", 1)[0]
        if key == "/reports/generate":
            return smoke.HttpResponse(
                200,
                {
                    "X-Report-Record-Count": "4",
                    "X-Report-Canonical-View": "applications_report_v1",
                    "X-Report-Field-List": smoke.DEFAULT_FIELDS,
                },
                csv_body,
            )
        return smoke.HttpResponse(200, {}, json.dumps(responses[key]).encode("utf-8"))

    monkeypatch.setattr(smoke, "_request", fake_request)
    args = argparse.Namespace(
        api_base="https://example.test/api",
        token="token",
        token_env="BACKOFFICE_TOKEN",
        expected_sha="abc123",
        expected_total=4,
        expected_pending=2,
        expected_edd=1,
        show_fixtures=False,
        skip_applications=False,
    )

    result = smoke.run_smoke(args)

    assert result["git_sha"] == "abc123456"
    assert result["classified_total"] == 4
    assert result["dashboard"]["in_progress_applications"] == 2
    assert result["csv"]["rows"] == 4
    assert result["applications_kpi_data"] == {"pending": 2, "edd": 1}

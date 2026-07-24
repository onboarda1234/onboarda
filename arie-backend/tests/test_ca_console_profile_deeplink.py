"""Remaining-list item #2 — operator-configured ComplyAdvantage console deep-link.

Sourceless watchlist hits (CA returns only opaque profile/alert/risk IDs, no list
name or article URL) left the officer with UUIDs to copy-paste. The front end
already renders an "Open in ComplyAdvantage ↗" button whenever a hit carries a
non-empty provider_case_url — the backend just never populated it (evidence.py
hardcoded "").

`_console_profile_url` now fills provider_case_url from an OPERATOR-CONFIGURED URL
template (COMPLYADVANTAGE_CONSOLE_PROFILE_URL_TEMPLATE). Honesty contract: no URL is
ever guessed or hardcoded — unset template, unresolved token, tokenless template, or
a non-http(s) result all yield "" so the button stays hidden (prior behavior).
"""
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage import evidence
from screening_complyadvantage.evidence import (
    _console_profile_url,
    CONSOLE_PROFILE_URL_TEMPLATE_ENV,
    extract_monitoring_evidence,
)

ENV = CONSOLE_PROFILE_URL_TEMPLATE_ENV
TEMPLATE = "https://app.complyadvantage.com/#/profiles/{profile_id}"


# --- helper unit tests --------------------------------------------------------

def test_empty_when_template_unset(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert _console_profile_url({"profile_id": "profile-1"}) == ""


def test_builds_url_from_profile_id(monkeypatch):
    monkeypatch.setenv(ENV, TEMPLATE)
    assert _console_profile_url({"profile_id": "profile-1"}) == (
        "https://app.complyadvantage.com/#/profiles/profile-1"
    )


def test_missing_required_id_yields_empty(monkeypatch):
    # Template references {profile_id} but the match carries none → no partial link.
    monkeypatch.setenv(ENV, TEMPLATE)
    assert _console_profile_url({"profile_id": None}) == ""
    assert _console_profile_url({"profile_id": ""}) == ""
    assert _console_profile_url({"alert_id": "alert-1"}) == ""


def test_tokenless_template_refused(monkeypatch):
    # A static URL with no token would link every hit to the same page — refuse it.
    monkeypatch.setenv(ENV, "https://app.complyadvantage.com/dashboard")
    assert _console_profile_url({"profile_id": "profile-1"}) == ""


def test_non_https_scheme_rejected(monkeypatch):
    # Provider consoles are TLS-only: ftp/javascript AND plain http are all refused.
    for bad in ("ftp://evil/{profile_id}", "javascript:alert(1)/{profile_id}",
                "http://ca.example/{profile_id}", "//ca.example/{profile_id}"):
        monkeypatch.setenv(ENV, bad)
        assert _console_profile_url({"profile_id": "profile-1"}) == "", bad


def test_surviving_unrecognized_token_refused(monkeypatch):
    # A misspelled/unsupported token the loop never resolves must NOT ship as a
    # literal placeholder in the URL (broken deep-link handed to the officer).
    monkeypatch.setenv(ENV, "https://x/{profile_id}/{entity_id}")
    assert _console_profile_url({"profile_id": "p1"}) == ""
    monkeypatch.setenv(ENV, "https://x/{profileid}")  # typo of profile_id
    assert _console_profile_url({"profile_id": "p1"}) == ""


def test_identifier_is_url_encoded(monkeypatch):
    monkeypatch.setenv(ENV, "https://ca.example/p/{profile_id}")
    out = _console_profile_url({"profile_id": "a b/c?d"})
    assert out == "https://ca.example/p/a%20b%2Fc%3Fd"


def test_identifier_cannot_alter_host(monkeypatch):
    # A hostile identifier cannot inject userinfo/host/scheme — it is percent-encoded.
    monkeypatch.setenv(ENV, "https://ca.example/p/{profile_id}")
    out = _console_profile_url({"profile_id": "evil.com/@h"})
    assert out == "https://ca.example/p/evil.com%2F%40h"
    assert out.startswith("https://ca.example/")


def test_whitespace_only_identifier_yields_empty(monkeypatch):
    monkeypatch.setenv(ENV, TEMPLATE)
    assert _console_profile_url({"profile_id": "   "}) == ""


def test_multi_token_template_all_resolved(monkeypatch):
    monkeypatch.setenv(ENV, "https://ca.example/{profile_id}/alerts/{alert_id}")
    out = _console_profile_url({"profile_id": "p1", "alert_id": "a1"})
    assert out == "https://ca.example/p1/alerts/a1"


def test_multi_token_template_one_missing_yields_empty(monkeypatch):
    monkeypatch.setenv(ENV, "https://ca.example/{profile_id}/alerts/{alert_id}")
    assert _console_profile_url({"profile_id": "p1", "alert_id": None}) == ""


# --- end-to-end through extract_monitoring_evidence ---------------------------

def _report():
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "matches": [{
                    "risk_id": "risk-1",
                    "profile_identifier": "profile-1",
                    "profile": {"person": {"names": {"values": [{"name": "Matched Person"}]}}},
                    "indicators": [{
                        "type": "CASanctionIndicator",
                        "taxonomy_key": "sanctions",
                        "taxonomy_label": "Sanctions",
                        "value": {"list_name": "OFAC SDN"},
                    }],
                }],
            }
        },
    }


def test_extract_populates_case_url_when_configured(monkeypatch):
    monkeypatch.setenv(ENV, TEMPLATE)
    rows = extract_monitoring_evidence(_report(), case_identifier="case-1", alert_identifier="alert-1")
    assert rows and rows[0]["provider_case_url"] == (
        "https://app.complyadvantage.com/#/profiles/profile-1"
    )


def test_extract_case_url_empty_by_default(monkeypatch):
    # Prior behavior guard: with no template configured, provider_case_url stays "".
    monkeypatch.delenv(ENV, raising=False)
    rows = extract_monitoring_evidence(_report(), case_identifier="case-1", alert_identifier="alert-1")
    assert rows and rows[0]["provider_case_url"] == ""

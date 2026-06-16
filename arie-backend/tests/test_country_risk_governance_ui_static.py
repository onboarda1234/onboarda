"""
PR-CR1R static UI coverage for manual country-risk source of truth.
"""
from pathlib import Path


def test_backoffice_country_risk_settings_show_manual_source():
    html = Path(__file__).resolve().parents[1].parent.joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    assert "Manual Risk Scoring Model settings are the active source of truth" in html
    assert "Imported PR-CR1 FATF snapshot data is reference only / not active for pilot" in html
    assert "id=\"btn-edit-countries\" onclick=\"toggleCountryEdit()\"" in html
    assert "MEDIUM_RISK" in html
    assert "Mauritius" in html
    assert "countryRiskListsToScoreMap" in html
    assert "countryScoreMapToCountryRiskLists" in html
    assert "renderCountryManualSourceNotice" in html
    assert "requestCountryRiskGovernanceLoad();" not in html
    assert "Governed Source" not in html
    assert "Loading governed country-risk source" not in html


def test_country_risk_manual_ui_dedupes_active_display():
    html = Path(__file__).resolve().parents[1].parent.joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    assert "var displayed = {}" in html
    assert "if (displayed[normalized]) return false;" in html
    assert "score > scoreMap[normalized]" in html

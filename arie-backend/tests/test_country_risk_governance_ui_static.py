"""
PR-CR1 static UI coverage for governed country-risk source visibility.
"""
from pathlib import Path


def test_backoffice_country_risk_settings_show_governed_source():
    html = Path(__file__).resolve().parents[1].parent.joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    assert "country-risk-source-container" in html
    assert "Governed Source" in html
    assert "/config/country-risk" in html
    assert "renderCountryRiskGovernance" in html
    assert "snapshot_version" in html
    assert "snapshot.version" in html
    assert "snapshot.id" in html
    assert "country_name" in html
    assert "entry.checksum" in html
    assert "stale_warning" in html

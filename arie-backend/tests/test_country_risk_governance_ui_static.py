"""Static coverage for the runtime-owned, read-only country-risk view."""
from pathlib import Path


def test_backoffice_country_risk_settings_use_runtime_projection_only():
    html = Path(__file__).resolve().parents[1].parent.joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    assert "This screen reflects the currently active runtime scoring model." in html
    assert "var model = payload && payload.runtime_model;" in html
    assert "RUNTIME_RISK_MODEL = deepFreezeRiskProjection(model);" in html
    assert "runtimeRiskCatalogLabels('country')" in html
    assert "COUNTRY_RISK_LISTS" not in html
    assert "countryScoreMapToCountryRiskLists" not in html
    assert "btn-edit-countries" not in html
    assert "toggleCountryEdit" not in html
    assert "boApiCall('PUT', '/config/risk-model'" not in html
    assert "No fallback model is displayed." in html


def test_country_risk_ui_does_not_embed_manual_country_rows():
    html = Path(__file__).resolve().parents[1].parent.joinpath("arie-backoffice.html").read_text(encoding="utf-8")

    risk_start = html.index("// RISK SCORING MODEL — READ-ONLY RUNTIME PROJECTION")
    risk_end = html.index("// AI VERIFICATION CHECKS CONFIGURATION", risk_start)
    risk_model = html[risk_start:risk_end]
    assert "Mauritius" not in risk_model
    assert "United Kingdom" not in risk_model
    assert "FATF_GREY" not in risk_model
    assert "countryScoreMapToCountryRiskLists" not in risk_model

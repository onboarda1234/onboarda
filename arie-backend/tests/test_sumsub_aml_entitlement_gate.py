import importlib


def test_sumsub_aml_without_explicit_entitlement_is_not_configured(monkeypatch):
    monkeypatch.delenv("SUMSUB_AML_ENTITLEMENT_PROVEN", raising=False)
    monkeypatch.setenv("SUMSUB_APP_TOKEN", "test-token")
    monkeypatch.setenv("SUMSUB_SECRET_KEY", "test-secret")

    import environment
    import screening

    importlib.reload(environment)
    importlib.reload(screening)

    result = screening.screen_sumsub_aml("Jane Director", entity_type="Person")

    assert result["matched"] is False
    assert result["results"] == []
    assert result["api_status"] == "not_configured"
    assert result["source"] == "sumsub_idv_only"
    assert result["provider_scope"] == "identity_verification_only"
    assert "IDV/KYC only" in result["reason"]


def test_sumsub_aml_entitlement_flag_is_explicit(monkeypatch):
    monkeypatch.delenv("SUMSUB_AML_ENTITLEMENT_PROVEN", raising=False)

    import environment

    importlib.reload(environment)
    assert environment.is_sumsub_aml_entitlement_proven() is False

    monkeypatch.setenv("SUMSUB_AML_ENTITLEMENT_PROVEN", "true")
    importlib.reload(environment)
    assert environment.is_sumsub_aml_entitlement_proven() is True


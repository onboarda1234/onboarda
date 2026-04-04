"""
Tests for branding.py — Brand configuration and convenience accessors.
"""
import os
import pytest


class TestBrandDictionary:
    """Test BRAND configuration dictionary structure and defaults."""

    def test_brand_dict_exists(self):
        from branding import BRAND
        assert isinstance(BRAND, dict)

    def test_required_keys_present(self):
        from branding import BRAND
        required_keys = [
            "portal_name", "portal_tagline", "portal_description",
            "backoffice_name", "backoffice_tagline",
            "platform_name", "powered_by", "company_legal", "copyright",
            "support_email", "website",
            "pdf_header", "pdf_footer", "pdf_classification",
            "system_id", "logger_name", "cookie_prefix", "metric_prefix",
            "email_from_name", "email_from_address",
        ]
        for key in required_keys:
            assert key in BRAND, f"Missing key '{key}' in BRAND dict"

    def test_portal_name_default(self):
        from branding import BRAND
        assert BRAND["portal_name"] == "Onboarda"

    def test_backoffice_name_default(self):
        from branding import BRAND
        assert BRAND["backoffice_name"] == "RegMind"

    def test_platform_name(self):
        from branding import BRAND
        assert BRAND["platform_name"] == "Onboarda"

    def test_pdf_header_default(self):
        from branding import BRAND
        assert BRAND["pdf_header"] == "Onboarda Compliance Report"

    def test_pdf_footer_default(self):
        from branding import BRAND
        assert BRAND["pdf_footer"] == "Powered by RegMind"

    def test_pdf_classification(self):
        from branding import BRAND
        assert BRAND["pdf_classification"] == "CONFIDENTIAL"

    def test_system_id(self):
        from branding import BRAND
        assert BRAND["system_id"] == "regmind"

    def test_copyright_year(self):
        from branding import BRAND
        assert "Onboarda" in BRAND["copyright"]

    def test_company_legal(self):
        from branding import BRAND
        assert BRAND["company_legal"] == "Onboarda Ltd"

    def test_support_email(self):
        from branding import BRAND
        assert "@" in BRAND["support_email"]

    def test_website_is_url(self):
        from branding import BRAND
        assert BRAND["website"].startswith("https://")


class TestBrandingEnvironmentOverrides:
    """Test that branding values can be overridden via env vars."""

    def test_portal_name_env_override(self, monkeypatch):
        monkeypatch.setenv("BRAND_PORTAL_NAME", "CustomPortal")
        import importlib
        import branding
        importlib.reload(branding)
        assert branding.BRAND["portal_name"] == "CustomPortal"
        assert branding.portal_name() == "CustomPortal"
        # Restore default so other tests aren't affected
        monkeypatch.delenv("BRAND_PORTAL_NAME", raising=False)
        importlib.reload(branding)

    def test_backoffice_name_env_override(self, monkeypatch):
        monkeypatch.setenv("BRAND_BACKOFFICE_NAME", "CustomBackoffice")
        import importlib
        import branding
        importlib.reload(branding)
        assert branding.BRAND["backoffice_name"] == "CustomBackoffice"
        assert branding.backoffice_name() == "CustomBackoffice"
        # Restore default so other tests aren't affected
        monkeypatch.delenv("BRAND_BACKOFFICE_NAME", raising=False)
        importlib.reload(branding)


class TestConvenienceAccessors:
    """Test convenience accessor functions."""

    def test_portal_name(self):
        from branding import portal_name
        assert portal_name() == "Onboarda"

    def test_backoffice_name(self):
        from branding import backoffice_name
        assert backoffice_name() == "RegMind"

    def test_powered_by(self):
        from branding import powered_by
        assert powered_by() == "Powered by RegMind"

    def test_pdf_header(self):
        from branding import pdf_header
        assert pdf_header() == "Onboarda Compliance Report"

    def test_pdf_footer(self):
        from branding import pdf_footer
        assert pdf_footer() == "Powered by RegMind"

    def test_system_id(self):
        from branding import system_id
        assert system_id() == "regmind"


class TestBrandingConsistency:
    """Test consistency between BRAND dict and accessor functions."""

    def test_portal_name_matches_dict(self):
        from branding import BRAND, portal_name
        assert portal_name() == BRAND["portal_name"]

    def test_backoffice_name_matches_dict(self):
        from branding import BRAND, backoffice_name
        assert backoffice_name() == BRAND["backoffice_name"]

    def test_pdf_header_matches_dict(self):
        from branding import BRAND, pdf_header
        assert pdf_header() == BRAND["pdf_header"]

    def test_pdf_footer_matches_dict(self):
        from branding import BRAND, pdf_footer
        assert pdf_footer() == BRAND["pdf_footer"]

    def test_powered_by_matches_dict(self):
        from branding import BRAND, powered_by
        assert powered_by() == BRAND["powered_by"]

    def test_system_id_matches_dict(self):
        from branding import BRAND, system_id
        assert system_id() == BRAND["system_id"]


class TestNoBrandHardcoding:
    """Verify no old brand name leaks."""

    def test_portal_not_arie(self):
        from branding import BRAND
        # Portal should be Onboarda, not ARIE
        assert BRAND["portal_name"] != "ARIE"
        assert BRAND["portal_name"] != "Arie Finance"

    def test_email_uses_onboarda_domain(self):
        from branding import BRAND
        assert "onboarda.com" in BRAND["support_email"]

    def test_website_uses_onboarda_domain(self):
        from branding import BRAND
        assert "onboarda.com" in BRAND["website"]


class TestStatusLabels:
    """Test STATUS_LABELS mapping and get_status_label() accessor."""

    # All application statuses defined in the DB schema CHECK constraint
    ALL_DB_STATUSES = [
        "draft", "submitted", "prescreening_submitted", "pricing_review",
        "pricing_accepted", "pre_approval_review", "pre_approved",
        "kyc_documents", "kyc_submitted", "compliance_review", "in_review",
        "edd_required", "approved", "rejected", "rmi_sent", "withdrawn",
    ]

    def test_status_labels_dict_exists(self):
        from branding import STATUS_LABELS
        assert isinstance(STATUS_LABELS, dict)

    def test_all_db_statuses_have_labels(self):
        """Every status in the DB CHECK constraint must have a human-readable label."""
        from branding import STATUS_LABELS
        for status in self.ALL_DB_STATUSES:
            assert status in STATUS_LABELS, f"Missing label for status '{status}'"

    def test_labels_are_nonempty_strings(self):
        from branding import STATUS_LABELS
        for status, label in STATUS_LABELS.items():
            assert isinstance(label, str) and len(label) > 0, f"Empty label for '{status}'"

    def test_labels_are_human_readable(self):
        """Labels should not contain underscores (internal format)."""
        from branding import STATUS_LABELS
        for status, label in STATUS_LABELS.items():
            assert "_" not in label, f"Label for '{status}' contains underscore: '{label}'"

    def test_get_status_label_known_status(self):
        from branding import get_status_label
        assert get_status_label("approved") == "Approved \u2013 Ready for Activation"

    def test_get_status_label_draft(self):
        from branding import get_status_label
        assert get_status_label("draft") == "Application Started"

    def test_get_status_label_unknown_fallback(self):
        """Unknown statuses should be title-cased with underscores replaced."""
        from branding import get_status_label
        assert get_status_label("some_custom_status") == "Some Custom Status"

    def test_get_status_label_none(self):
        from branding import get_status_label
        assert get_status_label(None) == "Unknown"

    def test_get_status_label_empty_string(self):
        from branding import get_status_label
        assert get_status_label("") == "Unknown"

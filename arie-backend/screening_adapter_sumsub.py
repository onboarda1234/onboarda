"""
Sumsub Screening Adapter — SCR-008
====================================
Thin wrapper around existing ``screening.run_full_screening()``,
``screening.screen_sumsub_aml()``, and ``screening.lookup_opencorporates()``.

This adapter is **never** called unless ``ENABLE_SCREENING_ABSTRACTION``
is set to ``true``.

Rules:
- No code moves out of ``screening.py``
- No modifications to ``sumsub_client.py``
- Adapter is a thin wrapper, not a refactor
"""

import logging
from screening_provider import ScreeningProvider
from screening_normalizer import normalize_screening_report

logger = logging.getLogger("arie")


class SumsubScreeningAdapter(ScreeningProvider):
    """
    Wraps the existing Sumsub screening pipeline behind the
    ``ScreeningProvider`` interface.
    """

    provider_name = "sumsub"

    def run_full_screening(self, application_data: dict, directors: list,
                           ubos: list, client_ip: str = None) -> dict:
        """
        Run the full screening pipeline by delegating to the existing
        ``screening.run_full_screening()`` and normalising the result.
        """
        from screening import run_full_screening as _run_full_screening
        raw = _run_full_screening(application_data, directors, ubos, client_ip=client_ip)
        return normalize_screening_report(raw, provider="sumsub")

    def screen_person(self, name: str, birth_date: str = None,
                      nationality: str = None, entity_type: str = "Person") -> dict:
        """
        Screen a single person against Sumsub AML/PEP/sanctions.
        Returns a minimally-normalized result.
        """
        from screening import screen_sumsub_aml
        raw = screen_sumsub_aml(name, birth_date=birth_date,
                                nationality=nationality, entity_type=entity_type)
        # Minimal normalization: add provider tag
        result = dict(raw)
        result["provider"] = "sumsub"
        return result

    def screen_company(self, company_name: str, jurisdiction: str = None) -> dict:
        """
        Screen a company via OpenCorporates lookup.
        Returns a minimally-normalized result.
        """
        from screening import lookup_opencorporates
        raw = lookup_opencorporates(company_name, jurisdiction=jurisdiction)
        result = dict(raw)
        result["provider"] = "opencorporates"
        return result

    def is_configured(self) -> bool:
        """Delegate to the existing Sumsub client configuration state."""
        try:
            from sumsub_client import get_sumsub_client
            client = get_sumsub_client()
            return client.is_configured
        except Exception:
            return False

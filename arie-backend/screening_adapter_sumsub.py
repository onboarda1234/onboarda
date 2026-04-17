"""
Sumsub Screening Adapter — ScreeningProvider wrapper
=====================================================
Thin adapter wrapping existing screening.py functions
behind the ScreeningProvider interface.

SAFETY: Does not move logic out of screening.py.
SAFETY: Does not modify sumsub_client.py.
SAFETY: No side effects on import.
SAFETY: Not called by runtime code unless abstraction is enabled.
"""

import logging
import os

from screening_provider import ScreeningProvider
from screening_normalizer import normalize_screening_report
from screening_models import create_normalized_person_screening, create_normalized_company_screening

logger = logging.getLogger("arie.screening_adapter_sumsub")


class SumsubScreeningAdapter(ScreeningProvider):
    """
    Adapter wrapping existing Sumsub screening functions
    behind the ScreeningProvider interface.

    All calls delegate to the existing screening module.
    Results are normalized before returning.
    """

    provider_name = "sumsub"

    def run_full_screening(self, application_data, directors, ubos, client_ip=None):
        """
        Run full screening via existing screening.run_full_screening().
        Returns normalized result.
        """
        from screening import run_full_screening as _run_full_screening
        raw_report = _run_full_screening(application_data, directors, ubos, client_ip=client_ip)
        return normalize_screening_report(raw_report)

    def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person"):
        """
        Screen an individual via existing screening.screen_sumsub_aml().
        Returns minimally normalized person record.
        """
        from screening import screen_sumsub_aml as _screen_sumsub_aml
        raw_result = _screen_sumsub_aml(
            name, birth_date=birth_date, nationality=nationality, entity_type=entity_type
        )

        # Compute hit summaries
        matched = raw_result.get("matched", False)
        results = raw_result.get("results", [])
        has_pep_hit = None
        has_sanctions_hit = None

        if matched and results:
            has_pep_hit = any(r.get("is_pep") for r in results)
            has_sanctions_hit = any(r.get("is_sanctioned") for r in results)
        elif isinstance(matched, bool):
            has_pep_hit = False
            has_sanctions_hit = False

        return create_normalized_person_screening(
            person_name=name,
            person_type="person",
            nationality=nationality or "",
            has_pep_hit=has_pep_hit,
            has_sanctions_hit=has_sanctions_hit,
            has_adverse_media_hit=None,
            adverse_media_coverage="none",
            screening=raw_result,
        )

    def screen_company(self, company_name, jurisdiction=None):
        """
        Screen a company. Sumsub provides company sanctions screening
        via screen_sumsub_aml with entity_type="Company".
        Returns minimally normalized company record.
        """
        from screening import screen_sumsub_aml as _screen_sumsub_aml
        raw_result = _screen_sumsub_aml(company_name, entity_type="Company")

        matched = raw_result.get("matched", False)
        has_hit = None
        if isinstance(matched, bool):
            has_hit = matched

        return create_normalized_company_screening(
            company_screening_coverage="full",
            has_company_screening_hit=has_hit,
            company_screening=raw_result,
        )

    def is_configured(self) -> bool:
        """
        Check if Sumsub is configured by verifying required env vars.
        """
        token = os.environ.get("SUMSUB_APP_TOKEN")
        secret = os.environ.get("SUMSUB_SECRET_KEY")
        return bool(token and secret)

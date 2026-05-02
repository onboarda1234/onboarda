"""Runtime routing for full screening calls.

This module is intentionally thin.  It preserves the legacy Sumsub report shape
when Sumsub is the active provider, and dispatches non-Sumsub providers through
the provider registry.
"""

from __future__ import annotations

import inspect
import logging
from typing import Callable

from screening_config import get_active_provider_name
from screening_provider import (
    COMPLYADVANTAGE_PROVIDER_NAME,
    SUMSUB_PROVIDER_NAME,
    ProviderNotRegistered,
    get_provider,
)

logger = logging.getLogger("arie.screening_routing")


def run_screening_for_active_provider(
    application_data,
    directors,
    ubos,
    *,
    client_ip=None,
    db=None,
    legacy_runner: Callable | None = None,
):
    """Run full screening using the configured active provider.

    Sumsub is kept on the legacy code path to avoid changing the production KYC
    and existing AML report contract.  ComplyAdvantage and future providers are
    resolved through the provider registry.
    """
    provider_name = get_active_provider_name()

    if provider_name == SUMSUB_PROVIDER_NAME:
        runner = legacy_runner or _default_legacy_runner()
        return runner(application_data, directors, ubos, client_ip=client_ip)

    provider = _build_provider(provider_name, db=db)
    logger.info("screening_routing active_provider=%s", provider_name)
    return provider.run_full_screening(application_data, directors, ubos, client_ip=client_ip)


def _build_provider(provider_name: str, *, db=None):
    try:
        factory = get_provider(provider_name)
    except ProviderNotRegistered:
        factory = _fallback_factory(provider_name)

    if not callable(factory):
        return factory

    kwargs = {}
    try:
        signature = inspect.signature(factory)
        parameters = signature.parameters.values()
        accepts_db = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "db"
            for parameter in parameters
        )
        if accepts_db:
            kwargs["db"] = db
    except (TypeError, ValueError):
        pass

    return factory(**kwargs)


def _fallback_factory(provider_name: str):
    if provider_name == COMPLYADVANTAGE_PROVIDER_NAME:
        from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter

        return ComplyAdvantageScreeningAdapter
    raise RuntimeError(f"Screening provider '{provider_name}' is not registered")


def _default_legacy_runner():
    from screening import run_full_screening

    return run_full_screening

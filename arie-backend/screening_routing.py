"""Runtime routing for full screening calls.

This module is intentionally thin.  It preserves the legacy Sumsub report shape
when Sumsub is the active provider, and dispatches non-Sumsub providers through
the provider registry.
"""

from __future__ import annotations

import inspect
import logging
from typing import Callable

from screening_config import get_active_provider_name, is_abstraction_enabled
from screening_provider import (
    COMPLYADVANTAGE_PROVIDER_NAME,
    SUMSUB_PROVIDER_NAME,
    ProviderNotRegistered,
    get_provider,
)
from screening_shadow import maybe_schedule_shadow_screening

logger = logging.getLogger("arie.screening_routing")


def run_screening_for_active_provider(
    application_data,
    directors,
    ubos,
    intermediaries=None,
    *,
    client_ip=None,
    db=None,
    legacy_runner: Callable | None = None,
    provider_options: dict | None = None,
):
    """Run full screening using the configured active provider.

    Sumsub is kept on the legacy code path to avoid changing the production KYC
    and existing AML report contract.  ComplyAdvantage and future providers are
    resolved through the provider registry.
    """
    provider_name = _effective_provider_name()

    if provider_name == SUMSUB_PROVIDER_NAME:
        runner = legacy_runner or _default_legacy_runner()
        result = runner(application_data, directors, ubos, client_ip=client_ip)
        try:
            maybe_schedule_shadow_screening(
                application_data,
                directors,
                ubos,
                result,
                client_ip=client_ip,
            )
        except Exception:
            logger.error(
                "screening_shadow_schedule_failed active_provider=%s shadow_provider=%s",
                provider_name,
                COMPLYADVANTAGE_PROVIDER_NAME,
                exc_info=True,
            )
        return result

    provider = _build_provider(provider_name, db=db, provider_options=provider_options)
    logger.info("screening_routing active_provider=%s", provider_name)
    return provider.run_full_screening(
        application_data,
        directors,
        ubos,
        intermediaries or [],
        client_ip=client_ip,
    )


def _build_provider(provider_name: str, *, db=None, provider_options: dict | None = None):
    try:
        factory = get_provider(provider_name)
    except ProviderNotRegistered:
        factory = _fallback_factory(provider_name)

    if not callable(factory):
        return factory

    kwargs = {}
    provider_options = dict(provider_options or {})
    try:
        signature = inspect.signature(factory)
        parameters = list(signature.parameters.values())
        parameter_names = {parameter.name for parameter in parameters}
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
        accepts_db = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "db"
            for parameter in parameters
        )
        if accepts_db:
            kwargs["db"] = db
        for name, value in provider_options.items():
            if name == "db":
                continue
            if accepts_kwargs or name in parameter_names:
                kwargs[name] = value
    except (TypeError, ValueError):
        pass

    return factory(**kwargs)


def _effective_provider_name() -> str:
    """Return the provider that is allowed to handle live screening calls.

    ComplyAdvantage cutover requires both the provider selection and the
    abstraction gate.  This keeps a stray SCREENING_PROVIDER change from moving
    operational screening off the legacy Sumsub path.
    """
    requested = get_active_provider_name()
    if requested == COMPLYADVANTAGE_PROVIDER_NAME and not is_abstraction_enabled():
        logger.warning(
            "screening_provider_override_ignored provider=%s abstraction_enabled=false",
            requested,
        )
        return SUMSUB_PROVIDER_NAME
    return requested


def _fallback_factory(provider_name: str):
    if provider_name == COMPLYADVANTAGE_PROVIDER_NAME:
        from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter

        return ComplyAdvantageScreeningAdapter
    raise RuntimeError(f"Screening provider '{provider_name}' is not registered")


def _default_legacy_runner():
    from screening import run_full_screening

    return run_full_screening

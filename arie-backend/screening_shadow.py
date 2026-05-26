"""D2 Sumsub-primary / ComplyAdvantage-shadow screening runner."""

from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from screening_config import get_active_provider_name, get_shadow_provider_name
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME, SUMSUB_PROVIDER_NAME

logger = logging.getLogger("arie.screening_shadow")

_SHADOW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="d2-ca-shadow")
_COMPARISON_KIND = "screening_shadow"


def maybe_schedule_shadow_screening(
    application_data: dict,
    directors: list[dict],
    ubos: list[dict],
    primary_report: dict,
    *,
    client_ip=None,
    scheduler=None,
):
    """Schedule a best-effort CA shadow run after a successful Sumsub primary run."""
    active_provider = get_active_provider_name()
    shadow_provider = get_shadow_provider_name()
    if active_provider != SUMSUB_PROVIDER_NAME or shadow_provider != COMPLYADVANTAGE_PROVIDER_NAME:
        return None

    app_id = _application_id(application_data)
    client_id = _client_id(application_data)
    if not app_id or not client_id:
        logger.warning(
            "d2_shadow_skipped_missing_identity application_id_present=%s client_id_present=%s",
            bool(app_id),
            bool(client_id),
        )
        return None

    payload = (
        copy.deepcopy(application_data or {}),
        copy.deepcopy(directors or []),
        copy.deepcopy(ubos or []),
        copy.deepcopy(primary_report or {}),
        client_ip,
    )
    submitter = scheduler or _SHADOW_EXECUTOR.submit
    return submitter(_safe_run_shadow_screening, *payload)


def _safe_run_shadow_screening(application_data, directors, ubos, primary_report, client_ip=None):
    try:
        return run_shadow_screening_now(application_data, directors, ubos, primary_report, client_ip=client_ip)
    except Exception:
        logger.error(
            "d2_shadow_run_failed application_id=%s shadow_provider=%s",
            _application_id(application_data),
            COMPLYADVANTAGE_PROVIDER_NAME,
            exc_info=True,
        )
        try:
            from screening_complyadvantage.observability import emit_metric

            emit_metric(
                "shadow_run_failed",
                metric_name="ShadowRunFailed",
                component="shadow_runner",
                outcome="failure",
                active_provider=get_active_provider_name(),
                step="shadow_run",
            )
        except Exception:
            pass
        return None


def run_shadow_screening_now(
    application_data,
    directors,
    ubos,
    primary_report,
    *,
    client_ip=None,
    db_factory=None,
    adapter_factory=None,
):
    """Run CA shadow synchronously. Intended for the worker and tests."""
    if db_factory is None:
        from db import get_db as db_factory
    if adapter_factory is None:
        from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter

        adapter_factory = lambda: ComplyAdvantageScreeningAdapter(  # noqa: E731 - tiny factory
            db=None,
            monitoring_enabled=False,
        )

    from screening_complyadvantage.observability import emit_audit, emit_metric
    from screening_normalizer import normalize_screening_report
    from screening_storage import (
        compute_report_hash,
        ensure_normalized_table,
        ensure_provider_comparisons_table,
        persist_normalized_report,
        persist_provider_comparison,
    )

    app_id = _application_id(application_data)
    client_id = _client_id(application_data)
    if not app_id or not client_id:
        raise ValueError("D2 shadow screening requires application_id and client_id")

    emit_metric(
        "shadow_run_started",
        metric_name="ShadowRunStarted",
        component="shadow_runner",
        outcome="started",
        active_provider=get_active_provider_name(),
        step="shadow_run",
    )

    shadow_report = adapter_factory().run_full_screening(application_data, directors, ubos, client_ip=client_ip)

    db = db_factory()
    try:
        ensure_normalized_table(db)
        ensure_provider_comparisons_table(db)

        if isinstance(primary_report, dict) and primary_report.get("normalized_version"):
            primary_normalized = dict(primary_report)
        else:
            primary_normalized = normalize_screening_report(primary_report)
        primary_hash = compute_report_hash(primary_report)
        primary_row_id = persist_normalized_report(
            db,
            client_id,
            app_id,
            primary_normalized,
            primary_hash,
            provider=SUMSUB_PROVIDER_NAME,
            normalized_version=primary_normalized.get("normalized_version", "1.0"),
            source="d2_primary",
        )

        shadow_hash = shadow_report.get("source_screening_report_hash") or compute_report_hash(shadow_report)
        shadow_row_id = persist_normalized_report(
            db,
            client_id,
            app_id,
            shadow_report,
            shadow_hash,
            provider=COMPLYADVANTAGE_PROVIDER_NAME,
            normalized_version=shadow_report.get("normalized_version", "2.0"),
            source="d2_shadow",
        )

        comparison = build_provider_comparison(primary_normalized, shadow_report)
        comparison_id = persist_provider_comparison(
            db,
            application_id=app_id,
            client_id=client_id,
            primary_provider=SUMSUB_PROVIDER_NAME,
            shadow_provider=COMPLYADVANTAGE_PROVIDER_NAME,
            comparison_kind=_COMPARISON_KIND,
            primary_normalized_record_id=primary_row_id,
            shadow_normalized_record_id=shadow_row_id,
            mismatch_class=comparison["mismatch_class"],
            comparison=comparison,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    _emit_comparison_metrics(comparison)
    emit_metric(
        "shadow_agent7_skipped",
        metric_name="ShadowAgent7Skipped",
        component="shadow_runner",
        outcome="skipped",
        active_provider=get_active_provider_name(),
        step="agent7_push",
    )
    emit_metric(
        "shadow_run_completed",
        metric_name="ShadowRunCompleted",
        component="shadow_runner",
        outcome="success",
        active_provider=get_active_provider_name(),
        step="shadow_run",
    )
    emit_audit(
        "ca_shadow_comparison_generated",
        component="shadow_runner",
        outcome="success",
        application_id=app_id,
        client_id=client_id,
        primary_provider=SUMSUB_PROVIDER_NAME,
        shadow_provider=COMPLYADVANTAGE_PROVIDER_NAME,
        comparison_kind=_COMPARISON_KIND,
        mismatch_class=comparison["mismatch_class"],
        primary_normalized_record_id=primary_row_id,
        shadow_normalized_record_id=shadow_row_id,
        comparison_id=comparison_id,
        decision_context="shadow_mode",
    )
    return {
        "application_id": app_id,
        "client_id": client_id,
        "primary_normalized_record_id": primary_row_id,
        "shadow_normalized_record_id": shadow_row_id,
        "comparison_id": comparison_id,
        "mismatch_class": comparison["mismatch_class"],
    }


def build_provider_comparison(primary_report: dict, shadow_report: dict) -> dict:
    primary = _summarize_report(primary_report or {})
    shadow = _summarize_report(shadow_report or {})
    category_delta = {
        key: {"primary": primary["categories"][key], "shadow": shadow["categories"][key]}
        for key in sorted(primary["categories"])
        if primary["categories"][key] != shadow["categories"][key]
    }
    comparison = {
        "primary_provider": SUMSUB_PROVIDER_NAME,
        "shadow_provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "primary": primary,
        "shadow": shadow,
        "deltas": {
            "hit": primary["has_hit"] != shadow["has_hit"],
            "total_hits": shadow["total_hits"] - primary["total_hits"],
            "categories": category_delta,
            "company": primary["company"] != shadow["company"],
            "directors": shadow["directors"]["hit_count"] - primary["directors"]["hit_count"],
            "ubos": shadow["ubos"]["hit_count"] - primary["ubos"]["hit_count"],
        },
    }
    comparison["mismatch_class"] = _mismatch_class(primary, shadow, comparison["deltas"])
    return comparison


def _summarize_report(report: dict) -> dict:
    directors = list(report.get("director_screenings") or [])
    ubos = list(report.get("ubo_screenings") or [])
    company = report.get("company_screening") or {}
    categories = {
        "pep": bool(report.get("any_pep_hits") or _party_category_hit(directors + ubos, "pep")),
        "sanctions": bool(report.get("any_sanctions_hits") or _company_sanctions_hit(company) or _party_category_hit(directors + ubos, "sanctions")),
        "media": bool(report.get("has_adverse_media_hit") or _company_media_hit(company) or _party_category_hit(directors + ubos, "media")),
        "watchlist": bool(_party_category_hit(directors + ubos, "watchlist")),
    }
    total_hits = _safe_int(report.get("total_hits"))
    if total_hits == 0:
        total_hits = sum(1 for value in categories.values() if value)
    return {
        "has_hit": any(categories.values()) or total_hits > 0,
        "total_hits": total_hits,
        "categories": categories,
        "company": {
            "has_hit": bool(report.get("has_company_screening_hit") or _company_sanctions_hit(company) or _company_media_hit(company)),
            "sanctions": _company_sanctions_hit(company),
            "media": _company_media_hit(company),
        },
        "directors": _party_summary(directors),
        "ubos": _party_summary(ubos),
    }


def _party_summary(parties: list[dict]) -> dict:
    return {
        "count": len(parties),
        "hit_count": sum(1 for party in parties if _party_has_hit(party)),
        "pep_count": sum(1 for party in parties if _party_category_hit([party], "pep")),
        "sanctions_count": sum(1 for party in parties if _party_category_hit([party], "sanctions")),
        "media_count": sum(1 for party in parties if _party_category_hit([party], "media")),
    }


def _party_has_hit(party: dict) -> bool:
    return any((
        party.get("has_pep_hit"),
        party.get("provider_detected_pep"),
        party.get("has_sanctions_hit"),
        party.get("has_adverse_media_hit"),
        ((party.get("screening") or {}).get("matched")),
    ))


def _party_category_hit(parties: list[dict], category: str) -> bool:
    for party in parties:
        screening = party.get("screening") or {}
        results = screening.get("results") or []
        if category == "pep" and (party.get("has_pep_hit") or party.get("provider_detected_pep") or any(r.get("is_pep") for r in results)):
            return True
        if category == "sanctions" and (party.get("has_sanctions_hit") or any(r.get("is_sanctioned") or r.get("sanctions") for r in results)):
            return True
        if category == "media" and (party.get("has_adverse_media_hit") or any(r.get("is_adverse_media") or r.get("media") for r in results)):
            return True
        if category == "watchlist" and any(r.get("is_watchlist") or r.get("watchlist") for r in results):
            return True
    return False


def _company_sanctions_hit(company: dict) -> bool:
    return bool((company.get("sanctions") or {}).get("matched"))


def _company_media_hit(company: dict) -> bool:
    return bool((company.get("adverse_media") or {}).get("matched"))


def _mismatch_class(primary: dict, shadow: dict, deltas: dict) -> str:
    if primary == shadow:
        return "exact_match"
    if shadow["has_hit"] and not primary["has_hit"]:
        return "ca_only"
    if primary["has_hit"] and not shadow["has_hit"]:
        return "sumsub_only"
    if deltas["categories"]:
        return "category_delta"
    if deltas["total_hits"]:
        return "count_delta"
    return "mixed_divergence"


def _emit_comparison_metrics(comparison: dict) -> None:
    from screening_complyadvantage.observability import emit_metric

    mismatch = comparison["mismatch_class"]
    emit_metric(
        "shadow_comparison_generated",
        metric_name="ShadowComparisonGenerated",
        component="shadow_runner",
        outcome="success",
        mismatch_class=mismatch,
        step="provider_pair_compare",
    )
    emit_metric(
        "shadow_decision_match" if mismatch == "exact_match" else "shadow_decision_mismatch",
        metric_name="ShadowDecisionMatch" if mismatch == "exact_match" else "ShadowDecisionMismatch",
        component="shadow_runner",
        outcome="success",
        mismatch_class=mismatch,
        step="provider_pair_compare",
    )
    if mismatch == "ca_only":
        emit_metric("shadow_ca_only", metric_name="ShadowCaOnly", component="shadow_runner", outcome="success", step="provider_pair_compare")
    elif mismatch == "sumsub_only":
        emit_metric("shadow_sumsub_only", metric_name="ShadowSumsubOnly", component="shadow_runner", outcome="success", step="provider_pair_compare")


def _application_id(application_data: dict) -> str | None:
    return application_data.get("application_id") or application_data.get("id")


def _client_id(application_data: dict) -> str | None:
    return application_data.get("client_id")


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

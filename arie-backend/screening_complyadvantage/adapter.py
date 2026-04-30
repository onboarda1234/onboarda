"""ScreeningProvider adapter for ComplyAdvantage."""

from hashlib import sha256
import os

from screening_models import create_normalized_screening_report
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME, ScreeningProvider
from screening_config import get_active_provider_name

from .client import ComplyAdvantageClient
from .config import CAConfig
from .exceptions import CAConfigurationError
from .normalizer import ScreeningApplicationContext
from .observability import emit_metric
from .orchestrator import ComplyAdvantageScreeningOrchestrator
from .payloads import build_customer_company, build_customer_person


class ComplyAdvantageScreeningAdapter(ScreeningProvider):
    """Thin ScreeningProvider wrapper around the CA workflow orchestrator."""

    provider_name = COMPLYADVANTAGE_PROVIDER_NAME

    def __init__(self, client=None, config=None, orchestrator=None, poll_timeout_seconds=300, db=None):
        self._client = client
        self._config = config
        self._orchestrator = orchestrator
        self._poll_timeout_seconds = poll_timeout_seconds
        self._db = db

    def is_configured(self) -> bool:
        try:
            CAConfig.from_env()
            return True
        except CAConfigurationError:
            return False

    def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person"):
        person = {
            "full_name": name,
            "date_of_birth": birth_date,
            "nationality": nationality,
        }
        return self._screen_subject(
            strict_customer=build_customer_person(person, strict=True),
            relaxed_customer=build_customer_person(person, strict=False),
            context=ScreeningApplicationContext(
                application_id=_stable_id("standalone-person", name),
                client_id="standalone",
                screening_subject_kind="director",
                screening_subject_name=name,
            ),
        )

    def screen_company(self, company_name, jurisdiction=None):
        company = {"company_name": company_name, "country": jurisdiction}
        return self._screen_subject(
            strict_customer=build_customer_company(company, strict=True),
            relaxed_customer=build_customer_company(company, strict=False),
            context=ScreeningApplicationContext(
                application_id=_stable_id("standalone-company", company_name),
                client_id="standalone",
                screening_subject_kind="entity",
                screening_subject_name=company_name,
            ),
        )

    def run_full_screening(self, application_data, directors, ubos, client_ip=None):
        company_name = _first(application_data, "company_name", "name", "legal_name")
        application_id = _application_id(application_data, company_name)
        client_id = str(_first(application_data, "client_id") or "unknown")
        reports = []

        if company_name:
            reports.append(self._screen_subject(
                strict_customer=build_customer_company(application_data, strict=True),
                relaxed_customer=build_customer_company(application_data, strict=False),
                context=ScreeningApplicationContext(
                    application_id=application_id,
                    client_id=client_id,
                    screening_subject_kind="entity",
                    screening_subject_name=company_name,
                ),
                external_identifier=application_id,
            ))
        for director in directors or []:
            reports.append(self._screen_party(director, "director", application_id, client_id))
        for ubo in ubos or []:
            reports.append(self._screen_party(ubo, "ubo", application_id, client_id))
        return _combine_reports(reports)

    def _screen_party(self, party, kind, application_id, client_id):
        name = _first(party, "full_name", "name") or "Unknown"
        return self._screen_subject(
            strict_customer=build_customer_person(party, strict=True),
            relaxed_customer=build_customer_person(party, strict=False),
            context=ScreeningApplicationContext(
                application_id=application_id,
                client_id=client_id,
                screening_subject_kind=kind,
                screening_subject_name=name,
                screening_subject_person_key=_first(party, "person_key", "id"),
                declared_pep=bool(_first(party, "is_pep", "declared_pep")),
            ),
            external_identifier=application_id,
        )

    def _screen_subject(self, *, strict_customer, relaxed_customer, context, external_identifier=None):
        active_provider = get_active_provider_name()
        emit_metric(
            "ca_adapter_invocation",
            metric_name="ShadowCaActivity" if active_provider != COMPLYADVANTAGE_PROVIDER_NAME else "CaAdapterInvocations",
            component="adapter",
            outcome="success",
            active_provider=active_provider,
        )
        return self._get_orchestrator().screen_customer_two_pass(
            strict_customer=strict_customer,
            relaxed_customer=relaxed_customer,
            application_context=context,
            monitoring_enabled=True,
            db=self._db,
            external_identifier=external_identifier,
        )

    def _get_orchestrator(self):
        if self._orchestrator is None:
            self._orchestrator = ComplyAdvantageScreeningOrchestrator(
                self._get_client(),
                poll_timeout_seconds=self._poll_timeout_seconds,
            )
        return self._orchestrator

    def _get_client(self):
        if self._client is None:
            config = self._config or CAConfig.from_env()
            self._client = ComplyAdvantageClient(config)
        return self._client


def _combine_reports(reports):
    company_report = next((r for r in reports if r.get("company_screening_coverage") == "full"), None)
    directors = []
    ubos = []
    flags = []
    degraded = []
    provider_subjects = []
    for report in reports:
        directors.extend(report.get("director_screenings", []))
        ubos.extend(report.get("ubo_screenings", []))
        flags.extend(report.get("overall_flags", []))
        degraded.extend(report.get("degraded_sources", []))
        provider_subjects.append(report.get("provider_specific", {}).get("complyadvantage", {}))
    company_screening = (company_report or {}).get("company_screening", {})
    has_company_hit = (company_report or {}).get("has_company_screening_hit")
    company_coverage = (company_report or {}).get("company_screening_coverage", "none")
    adverse_hit = any(p.get("has_adverse_media_hit") for p in directors + ubos)
    return create_normalized_screening_report(
        provider="complyadvantage",
        normalized_version="2.0",
        any_pep_hits=any(p.get("has_pep_hit") for p in directors + ubos),
        any_sanctions_hits=any(p.get("has_sanctions_hit") for p in directors + ubos) or bool(has_company_hit),
        total_persons_screened=len(directors) + len(ubos),
        adverse_media_coverage="full" if adverse_hit else "none",
        has_adverse_media_hit=True if adverse_hit else None,
        company_screening_coverage=company_coverage,
        has_company_screening_hit=has_company_hit,
        company_screening=company_screening,
        director_screenings=directors,
        ubo_screenings=ubos,
        overall_flags=flags,
        total_hits=sum(r.get("total_hits", 0) for r in reports),
        degraded_sources=sorted(set(degraded)),
        any_non_terminal_subject=False,
        company_screening_state="completed_match" if has_company_hit else "completed_clear",
        provider_specific={"complyadvantage": {"subjects": provider_subjects}},
        source_screening_report_hash=_reports_hash(reports),
        provenance=None,
    )


def _reports_hash(reports):
    text = "|".join(r.get("source_screening_report_hash", "") for r in reports)
    return sha256(text.encode("utf-8")).hexdigest()[:32]


def _stable_id(prefix, value):
    return f"{prefix}-{sha256(str(value).encode('utf-8')).hexdigest()[:12]}"


def _application_id(application_data, company_name):
    explicit_id = _first(application_data, "application_id", "id", "ref")
    return str(explicit_id or _stable_id("application", company_name or "unknown"))


def _first(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None

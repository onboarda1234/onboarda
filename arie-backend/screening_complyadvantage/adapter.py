"""ScreeningProvider adapter for ComplyAdvantage."""

from copy import deepcopy
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

    def __init__(
        self,
        client=None,
        config=None,
        orchestrator=None,
        poll_timeout_seconds=300,
        allow_pending_on_timeout=False,
        db=None,
        monitoring_enabled=True,
    ):
        self._client = client
        self._config = config
        self._orchestrator = orchestrator
        self._poll_timeout_seconds = poll_timeout_seconds
        self._allow_pending_on_timeout = bool(allow_pending_on_timeout)
        self._db = db
        self._monitoring_enabled = bool(monitoring_enabled)

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

    def run_full_screening(self, application_data, directors, ubos, intermediaries=None, client_ip=None):
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
                external_identifier=_subject_external_identifier(
                    application_id,
                    "company",
                    subject_name=company_name,
                ),
            ))
        for group in _dedupe_person_roles(directors or [], ubos or []):
            first_kind, first_party = group[0]
            report = self._screen_party(first_party, first_kind, application_id, client_id)
            if len(group) > 1:
                report = dict(report)
                report["_ca_role_associations"] = group
            reports.append(report)
        for intermediary in intermediaries or []:
            reports.append(self._screen_intermediary(intermediary, application_id, client_id))
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
                declared_pep=_declared_pep(_first(party, "is_pep", "declared_pep")),
            ),
            external_identifier=_subject_external_identifier(
                application_id,
                kind,
                party=party,
                subject_name=name,
            ),
        )

    def _screen_intermediary(self, intermediary, application_id, client_id):
        name = _first(intermediary, "entity_name", "company_name", "legal_name", "full_name", "name")
        if not name:
            return _intermediary_gap_report(application_id, client_id, intermediary)
        subject = dict(intermediary or {})
        subject.setdefault("company_name", name)
        subject.setdefault("application_id", f"{application_id}:intermediary:{_first(intermediary, 'person_key', 'id') or name}")
        return self._screen_subject(
            strict_customer=build_customer_company(subject, strict=True),
            relaxed_customer=build_customer_company(subject, strict=False),
            context=ScreeningApplicationContext(
                application_id=application_id,
                client_id=client_id,
                screening_subject_kind="intermediary",
                screening_subject_name=name,
                screening_subject_person_key=_first(intermediary, "person_key", "id"),
            ),
            external_identifier=_subject_external_identifier(
                application_id,
                "intermediary",
                party=intermediary,
                subject_name=name,
            ),
        )

    def _screen_subject(self, *, strict_customer, relaxed_customer, context, external_identifier=None):
        active_provider = get_active_provider_name()
        config = self._get_config()
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
            monitoring_enabled=self._monitoring_enabled,
            db=self._db,
            screening_configuration_identifier=config.screening_configuration_identifier,
            external_identifier=external_identifier,
            strict_external_identifier=_pass_external_identifier(external_identifier, "strict"),
            relaxed_external_identifier=_pass_external_identifier(external_identifier, "relaxed"),
        )

    def _get_config(self):
        if self._config is None:
            self._config = CAConfig.from_env()
        return self._config

    def _get_orchestrator(self):
        if self._orchestrator is None:
            self._get_config()
            self._orchestrator = ComplyAdvantageScreeningOrchestrator(
                self._get_client(),
                poll_timeout_seconds=self._poll_timeout_seconds,
                allow_pending_on_timeout=self._allow_pending_on_timeout,
            )
        return self._orchestrator

    def _get_client(self):
        if self._client is None:
            config = self._get_config()
            self._client = ComplyAdvantageClient(config)
        return self._client


def _combine_reports(reports):
    company_report = next((r for r in reports if r.get("company_screening_coverage") == "full"), None)
    directors = []
    ubos = []
    intermediaries = []
    flags = []
    degraded = []
    provider_subjects = []
    for report in reports:
        associations = report.get("_ca_role_associations") or []
        if associations:
            base = _first_person_screening(report)
            for kind, party in associations:
                cloned = _clone_person_screening_for_role(base, kind, party)
                if kind == "ubo":
                    ubos.append(cloned)
                else:
                    directors.append(cloned)
        else:
            directors.extend(report.get("director_screenings", []))
            ubos.extend(report.get("ubo_screenings", []))
            intermediaries.extend(report.get("intermediary_screenings", []))
        flags.extend(report.get("overall_flags", []))
        degraded.extend(report.get("degraded_sources", []))
        provider_subjects.append(report.get("provider_specific", {}).get("complyadvantage", {}))
    company_screening = (company_report or {}).get("company_screening", {})
    has_company_hit = (company_report or {}).get("has_company_screening_hit")
    company_coverage = (company_report or {}).get("company_screening_coverage", "none")
    company_adverse = (company_screening.get("adverse_media") or {})
    screening_subjects = directors + ubos + intermediaries
    adverse_hit = any(p.get("has_adverse_media_hit") for p in screening_subjects) or bool(company_adverse.get("matched"))
    any_non_terminal_subject = any(
        (p.get("screening_state") not in ("completed_clear", "completed_match"))
        for p in screening_subjects
        if isinstance(p, dict)
    )
    return create_normalized_screening_report(
        provider="complyadvantage",
        normalized_version="2.0",
        any_pep_hits=any(p.get("has_pep_hit") for p in screening_subjects),
        any_sanctions_hits=any(p.get("has_sanctions_hit") for p in screening_subjects) or bool((company_screening.get("sanctions") or {}).get("matched")),
        total_persons_screened=len(directors) + len(ubos),
        total_intermediaries_screened=len(intermediaries),
        total_subjects_screened=len(directors) + len(ubos) + len(intermediaries) + (1 if company_screening else 0),
        adverse_media_coverage="full" if adverse_hit else "none",
        has_adverse_media_hit=True if adverse_hit else None,
        company_screening_coverage=company_coverage,
        has_company_screening_hit=has_company_hit,
        company_screening=company_screening,
        director_screenings=directors,
        ubo_screenings=ubos,
        intermediary_screenings=intermediaries,
        overall_flags=flags,
        total_hits=sum(r.get("total_hits", 0) for r in reports),
        degraded_sources=sorted(set(degraded)),
        any_non_terminal_subject=any_non_terminal_subject,
        company_screening_state="completed_match" if has_company_hit else "completed_clear",
        provider_specific={"complyadvantage": {"subjects": provider_subjects}},
        source_screening_report_hash=_reports_hash(reports),
        provenance=None,
    )


def _first_person_screening(report):
    items = (report.get("director_screenings") or []) + (report.get("ubo_screenings") or [])
    return items[0] if items else {}


def _clone_person_screening_for_role(base, kind, party):
    cloned = deepcopy(base or {})
    name = _first(party, "full_name", "name") or cloned.get("person_name") or "Unknown"
    declared = _declared_pep(_first(party, "is_pep", "declared_pep"))
    provider_pep = bool(cloned.get("provider_detected_pep") or cloned.get("has_pep_hit"))
    cloned["person_name"] = name
    cloned["person_type"] = kind
    cloned["declared_pep"] = "Yes" if declared else "No"
    cloned["provider_detected_pep"] = provider_pep
    cloned["undeclared_pep"] = bool(provider_pep and not declared)
    screening = cloned.get("screening")
    if isinstance(screening, dict):
        screening["person_key"] = _first(party, "person_key", "id")
        screening["shared_subject_key"] = _person_dedupe_key(party)
    return cloned


def _intermediary_gap_report(application_id, client_id, intermediary):
    subject_name = _first(intermediary or {}, "entity_name", "company_name", "legal_name", "full_name", "name") or "Unnamed intermediary"
    subject_key = _first(intermediary or {}, "person_key", "id")
    screening = {
        "provider": "complyadvantage",
        "source": "complyadvantage",
        "api_status": "failed",
        "matched": False,
        "results": [],
        "evidence_gap": True,
        "reason": "missing_required_intermediary_subject_name",
        "subject_type": "intermediary",
        "person_key": subject_key,
    }
    entry = {
        "person_name": subject_name,
        "entity_name": subject_name,
        "person_type": "intermediary",
        "subject_type": "intermediary",
        "nationality": "",
        "declared_pep": "No",
        "provider_detected_pep": False,
        "undeclared_pep": False,
        "has_pep_hit": False,
        "has_sanctions_hit": False,
        "has_adverse_media_hit": None,
        "adverse_media_coverage": "none",
        "screening": screening,
        "screening_state": "failed",
        "requires_review": True,
        "is_rca": None,
        "pep_classes": None,
        "evidence_gap": True,
    }
    return create_normalized_screening_report(
        provider="complyadvantage",
        normalized_version="2.0",
        screened_at="",
        any_pep_hits=False,
        any_sanctions_hits=False,
        total_persons_screened=0,
        total_intermediaries_screened=0,
        total_subjects_screened=0,
        adverse_media_coverage="none",
        has_adverse_media_hit=None,
        company_screening_coverage="none",
        has_company_screening_hit=None,
        company_screening={},
        director_screenings=[],
        ubo_screenings=[],
        intermediary_screenings=[entry],
        overall_flags=[],
        total_hits=0,
        degraded_sources=["intermediary_missing_required_subject_data"],
        any_non_terminal_subject=True,
        company_screening_state="completed_clear",
        provider_specific={
            "complyadvantage": {
                "screening_subject": {
                    "kind": "intermediary",
                    "scope": "entity",
                    "person_key": subject_key,
                },
                "application_id": application_id,
                "client_id": client_id,
            }
        },
        source_screening_report_hash=_reports_hash([{"source_screening_report_hash": f"intermediary-gap:{application_id}:{subject_key or subject_name}"}]),
        provenance=None,
    )


def _dedupe_person_roles(directors, ubos):
    groups = []
    seen = {}
    for kind, party in [("director", item) for item in directors] + [("ubo", item) for item in ubos]:
        key = _person_dedupe_key(party)
        if key in seen:
            groups[seen[key]].append((kind, party))
        else:
            seen[key] = len(groups)
            groups.append([(kind, party)])
    return groups


def _person_dedupe_key(party):
    name = _normalize_text(_first(party, "full_name", "name") or "unknown")
    dob = _normalize_text(_first(party, "date_of_birth", "birth_date", "dob") or "")
    country = _normalize_text(_first(party, "nationality", "country", "country_of_residence") or "")
    return "|".join((name, dob, country))


def _normalize_text(value):
    return " ".join(str(value or "").strip().lower().split())


def _declared_pep(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"yes", "true", "1", "y"}


def _reports_hash(reports):
    text = "|".join(r.get("source_screening_report_hash", "") for r in reports)
    return sha256(text.encode("utf-8")).hexdigest()[:32]


def _stable_id(prefix, value):
    return f"{prefix}-{sha256(str(value).encode('utf-8')).hexdigest()[:12]}"


def _application_id(application_data, company_name):
    explicit_id = _first(application_data, "application_id", "id", "ref")
    return str(explicit_id or _stable_id("application", company_name or "unknown"))


def _subject_external_identifier(application_id, subject_kind, *, party=None, subject_name=None):
    scope = str(application_id or "application")
    normalized_kind = {"entity": "company"}.get(subject_kind, str(subject_kind or "subject"))
    subject_key = _first(party or {}, "person_key", "id")
    if subject_key:
        discriminator = f"key-{subject_key}"
    else:
        discriminator = f"name-{sha256(str(subject_name or 'unknown').encode('utf-8')).hexdigest()[:32]}"
    return f"{scope}:{normalized_kind}:{discriminator}"


def _pass_external_identifier(external_identifier, pass_name):
    if not external_identifier:
        return None
    return f"{external_identifier}:{pass_name}"


def _first(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None

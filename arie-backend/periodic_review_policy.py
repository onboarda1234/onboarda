from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

POLICY_VERSION_V2 = "v2"
RISK_FREQUENCY_MONTHS: Dict[str, int] = {
    "LOW": 36,
    "MEDIUM": 24,
    "HIGH": 12,
    "VERY_HIGH": 6,
}
RISK_NOMINAL_INTERVAL_DAYS: Dict[int, int] = {
    36: 1095,
    24: 730,
    12: 365,
    6: 180,
}
ENHANCED_REVIEW_FLOOR_MONTHS = 12
ENHANCED_MONITORING_LANES = {"edd", "enhanced_due_diligence", "enhanced due diligence"}
EDD_ROUTE_STATUSES = {"edd_required", "edd_approved"}
CRYPTO_VASP_PATTERNS = (
    re.compile(r"\bcrypto(?:currency)?\b"),
    re.compile(r"\bvasp\b"),
    re.compile(r"virtual asset"),
    re.compile(r"digital asset"),
)
PEP_PATTERNS = (
    re.compile(r"\bpep\b"),
    re.compile(r"politically exposed"),
)
EDD_PATTERNS = (
    re.compile(r"\bedd\b"),
    re.compile(r"enhanced due diligence"),
)


def normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in RISK_FREQUENCY_MONTHS else "MEDIUM"


def frequency_months_for_risk(risk_level: Any) -> int:
    return RISK_FREQUENCY_MONTHS[normalize_risk_level(risk_level)]


def calculation_basis_for_risk(risk_level: Any) -> str:
    return f"risk_level:{normalize_risk_level(risk_level)}"


def nominal_interval_days_for_risk(risk_level: Any) -> int:
    return RISK_NOMINAL_INTERVAL_DAYS[frequency_months_for_risk(risk_level)]


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        import json

        return json.loads(text)
    except Exception:
        return value


def _nonempty_signal(value: Any) -> bool:
    value = _json_value(value)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set)):
        return any(_nonempty_signal(item) for item in value)
    if isinstance(value, dict):
        return any(_nonempty_signal(item) for item in value.values())
    return str(value).strip().lower() not in {"", "none", "null", "false", "0", "[]", "{}"}


def _value_matches_patterns(value: Any, patterns: Iterable[re.Pattern[str]]) -> bool:
    data = _json_value(value)
    if isinstance(data, dict):
        return any(
            _value_matches_patterns(key, patterns) or _value_matches_patterns(item, patterns)
            for key, item in data.items()
        )
    if isinstance(data, list):
        return any(_value_matches_patterns(item, patterns) for item in data)
    text = str(data or "").strip().lower()
    return any(pattern.search(text) for pattern in patterns)


def _decision_notes_indicate_edd(value: Any) -> bool:
    data = _json_value(value)
    if isinstance(data, dict):
        decision = str(data.get("decision") or "").strip().lower()
        if decision == "escalate_edd":
            return True
        status = str(data.get("status") or data.get("new_status") or "").strip().lower()
        if status in EDD_ROUTE_STATUSES:
            return True
        return any(
            _nonempty_signal(data.get(key))
            for key in ("edd_trigger_flags", "edd_triggers", "edd_requirements", "edd_findings")
        )
    if isinstance(data, list):
        return any(_decision_notes_indicate_edd(item) for item in data)
    return _value_matches_patterns(value, EDD_PATTERNS)


def parse_review_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
    return datetime.now(timezone.utc).date()


def add_months(anchor: Any, months: int) -> str:
    base = parse_review_date(anchor)
    month_index = base.month - 1 + int(months)
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _interval_days(anchor_date: Any, due_date: str) -> int:
    return (date.fromisoformat(due_date) - parse_review_date(anchor_date)).days


def is_crypto_vasp_application(app: Dict[str, Any]) -> bool:
    return any(
        _value_matches_patterns(app.get(field), CRYPTO_VASP_PATTERNS)
        for field in ("sector", "business_activity", "entity_type", "form_data")
    )


def has_pep_signal(app: Dict[str, Any]) -> bool:
    return any(
        _value_matches_patterns(app.get(field), PEP_PATTERNS)
        for field in ("decision_notes", "risk_escalations", "elevation_reason_text", "screening_summary", "form_data")
    )


def enhanced_monitoring_reasons(
    app: Optional[Dict[str, Any]],
    *,
    previous_status: Optional[str] = None,
) -> List[str]:
    app = dict(app or {})
    reasons: List[str] = []
    previous = str(previous_status or "").strip().lower()
    status = str(app.get("status") or "").strip().lower()
    lane = str(app.get("onboarding_lane") or "").strip().lower()
    if (
        previous in EDD_ROUTE_STATUSES
        or status in EDD_ROUTE_STATUSES
        or lane in ENHANCED_MONITORING_LANES
        or _decision_notes_indicate_edd(app.get("decision_notes"))
        or _value_matches_patterns(app.get("risk_escalations"), EDD_PATTERNS)
        or _value_matches_patterns(app.get("elevation_reason_text"), EDD_PATTERNS)
    ):
        reasons.append("edd_route")
    if is_crypto_vasp_application(app):
        reasons.append("crypto_vasp")
    if has_pep_signal(app):
        reasons.append("pep_exposure")
    deduped: List[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def policy_snapshot_for_risk(risk_level: Any, *, anchor_date: Any) -> Dict[str, Any]:
    normalized = normalize_risk_level(risk_level)
    frequency = frequency_months_for_risk(normalized)
    next_review_date = add_months(anchor_date, frequency)
    return {
        "policy_version": POLICY_VERSION_V2,
        "frequency_months": frequency,
        "calculation_basis": calculation_basis_for_risk(normalized),
        "next_review_date": next_review_date,
        "due_date": next_review_date,
        "interval_days": _interval_days(anchor_date, next_review_date),
        "risk_level": normalized,
        "enhanced_monitoring": False,
        "enhanced_monitoring_reasons": [],
    }


def policy_snapshot_for_application(
    app: Optional[Dict[str, Any]],
    *,
    anchor_date: Any,
    previous_status: Optional[str] = None,
    override_risk_level: Any = None,
) -> Dict[str, Any]:
    app = dict(app or {})
    normalized = normalize_risk_level(
        override_risk_level
        or app.get("final_risk_level")
        or app.get("risk_level")
        or app.get("base_risk_level")
    )
    reasons = enhanced_monitoring_reasons(app, previous_status=previous_status)
    if reasons and normalized != "VERY_HIGH":
        frequency = min(
            frequency_months_for_risk(normalized),
            ENHANCED_REVIEW_FLOOR_MONTHS,
        )
        calculation_basis = "enhanced_monitoring_floor:" + "+".join(reasons)
    else:
        frequency = frequency_months_for_risk(normalized)
        calculation_basis = calculation_basis_for_risk(normalized)
    next_review_date = add_months(anchor_date, frequency)
    return {
        "policy_version": POLICY_VERSION_V2,
        "frequency_months": frequency,
        "calculation_basis": calculation_basis,
        "next_review_date": next_review_date,
        "due_date": next_review_date,
        "interval_days": _interval_days(anchor_date, next_review_date),
        "risk_level": normalized,
        "enhanced_monitoring": bool(reasons),
        "enhanced_monitoring_reasons": reasons,
    }

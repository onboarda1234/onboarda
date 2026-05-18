from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any, Dict

POLICY_VERSION_V1 = "v1"
RISK_FREQUENCY_MONTHS: Dict[str, int] = {
    "LOW": 36,
    "MEDIUM": 24,
    "HIGH": 12,
    "VERY_HIGH": 6,
}


def normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in RISK_FREQUENCY_MONTHS else "MEDIUM"


def frequency_months_for_risk(risk_level: Any) -> int:
    return RISK_FREQUENCY_MONTHS[normalize_risk_level(risk_level)]


def calculation_basis_for_risk(risk_level: Any) -> str:
    return f"risk_level:{normalize_risk_level(risk_level)}"


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


def policy_snapshot_for_risk(risk_level: Any, *, anchor_date: Any) -> Dict[str, Any]:
    normalized = normalize_risk_level(risk_level)
    frequency = frequency_months_for_risk(normalized)
    return {
        "policy_version": POLICY_VERSION_V1,
        "frequency_months": frequency,
        "calculation_basis": calculation_basis_for_risk(normalized),
        "next_review_date": add_months(anchor_date, frequency),
        "risk_level": normalized,
    }

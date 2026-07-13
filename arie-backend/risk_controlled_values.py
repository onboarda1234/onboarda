"""Exact controlled-value registry for the RSMP Tier 0A mapping boundary.

The registry is deliberately inert unless ``ENABLE_RSMP_TIER0A_MAPPING_FIDELITY``
is enabled.  It does not assign a generic score to unknown values: callers get
an explicit ``unresolved`` result which Tier 0B can turn into a fail-closed
approval control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Mapping, Optional

from environment import flags


ACTIVATION_FLAG = "ENABLE_RSMP_TIER0A_MAPPING_FIDELITY"
REGISTRY_VERSION = "rsmp-tier0a-v1"
UNMAPPED_SENTINEL_PREFIX = "stale:unmapped_"
SHORT_HASH_LENGTH = 12


def mapping_fidelity_enabled() -> bool:
    """Return the deployment-controlled activation state (OFF by default)."""
    return flags.is_enabled(ACTIVATION_FLAG)


def normalize_controlled_value(value: Any) -> str:
    """Normalize identity syntax without performing fuzzy/substring matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True)
class ControlledResolution:
    family: str
    raw_value: str
    normalized_value: str
    status: str
    score: Optional[int] = None
    controlled_id: str = ""
    canonical_label: str = ""
    config_key: str = ""
    config_version: str = REGISTRY_VERSION

    @property
    def mapped(self) -> bool:
        return self.status == "mapped" and self.score is not None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _record(
    controlled_id: str,
    label: str,
    score: int,
    *,
    config_key: str = "",
    locked_score: bool = False,
) -> Dict[str, Any]:
    return {
        "id": controlled_id,
        "label": label,
        "score": int(score),
        "config_key": config_key,
        "locked_score": bool(locked_score),
    }


# Scores below are the exact code-seed values.  A risk_config map can override
# sector/entity scores only through the record's exact config key.
SECTOR_RECORDS = {
    "Fintech / Payments": _record("sector.fintech_payments", "Fintech / Payments", 3, config_key="fintech"),
    "Forex / FX Trading (Retail)": _record("sector.forex_retail", "Forex / FX Trading (Retail)", 3, config_key="forex"),
    "Forex / FX Trading (Institutional)": _record("sector.forex_institutional", "Forex / FX Trading (Institutional)", 3, config_key="forex"),
    "Crypto / Digital Assets Exchange": _record("sector.crypto_exchange", "Crypto / Digital Assets Exchange", 4, config_key="crypto"),
    "Crypto / Digital Assets Custody": _record("sector.crypto_custody", "Crypto / Digital Assets Custody", 4, config_key="crypto"),
    "Crypto / Web3 / DeFi": _record("sector.crypto_web3_defi", "Crypto / Web3 / DeFi", 4, config_key="crypto"),
    "Remittance / Money Transfer": _record("sector.remittance", "Remittance / Money Transfer", 3, config_key="remittance"),
    "E-Money / E-Wallet Provider": _record("sector.e_money", "E-Money / E-Wallet Provider", 3, config_key="e-money"),
    "Insurance / InsurTech": _record("sector.insurance", "Insurance / InsurTech", 2, config_key="insurance"),
    "Investment Management": _record("sector.investment_management", "Investment Management", 3, config_key="investment management", locked_score=True),
    "Family Office / Wealth Management": _record("sector.family_office", "Family Office / Wealth Management", 3, config_key="wealth management", locked_score=True),
    "Private Banking": _record("sector.private_banking", "Private Banking", 4, config_key="private banking", locked_score=True),
    "Banking-as-a-Service": _record("sector.banking_as_a_service", "Banking-as-a-Service", 2, config_key="banking"),
    "E-Commerce / Online Retail": _record("sector.ecommerce", "E-Commerce / Online Retail", 2, config_key="e-commerce"),
    "Import / Export": _record("sector.import_export", "Import / Export", 3, config_key="import"),
    "Precious Metals / Gems": _record("sector.precious_metals", "Precious Metals / Gems", 3, config_key="precious metals", locked_score=True),
    "Oil & Gas / Energy Trading": _record("sector.oil_gas", "Oil & Gas / Energy Trading", 3, config_key="oil"),
    "Logistics / Freight Forwarding": _record("sector.logistics", "Logistics / Freight Forwarding", 2, config_key="logistics"),
    "Software / SaaS": _record("sector.software_saas", "Software / SaaS", 2, config_key="software"),
    "Cloud Services": _record("sector.cloud_services", "Cloud Services", 2, config_key="cloud services", locked_score=True),
    "Telecommunications": _record("sector.telecommunications", "Telecommunications", 2, config_key="telecommunications"),
    "Media Technology": _record("sector.media_technology", "Media Technology", 2, config_key="media"),
    "iGaming / Online Gambling": _record("sector.igaming", "iGaming / Online Gambling", 4, config_key="gambling"),
    "Online Casino / Sports Betting": _record("sector.online_betting", "Online Casino / Sports Betting", 4, config_key="betting"),
    "NFT / Gaming Assets": _record("sector.nft_gaming", "NFT / Gaming Assets", 4, config_key="gaming"),
    "Entertainment / Media": _record("sector.entertainment_media", "Entertainment / Media", 2, config_key="media"),
    "MSB / Money Services Business": _record("sector.msb", "MSB / Money Services Business", 3, config_key="money services"),
    "Law Firm / Legal Services": _record("sector.legal", "Law Firm / Legal Services", 3, config_key="legal"),
    "Accounting / Audit Firm": _record("sector.accounting", "Accounting / Audit Firm", 3, config_key="accounting"),
    "Real Estate (Commercial)": _record("sector.real_estate_commercial", "Real Estate (Commercial)", 3, config_key="real estate"),
    "Real Estate (Development)": _record("sector.real_estate_development", "Real Estate (Development)", 3, config_key="real estate"),
    "Management Consulting": _record("sector.management_consulting", "Management Consulting", 3, config_key="management consulting"),
    "Financial / Tax Advisory": _record("sector.financial_tax_advisory", "Financial / Tax Advisory", 3, config_key="financial / tax advisory"),
    "Healthcare / MedTech": _record("sector.healthcare", "Healthcare / MedTech", 2, config_key="healthcare"),
    "Education / EdTech": _record("sector.education", "Education / EdTech", 1, config_key="education"),
    "Manufacturing": _record("sector.manufacturing", "Manufacturing", 2, config_key="manufacturing"),
    "Construction": _record("sector.construction", "Construction", 3, config_key="construction"),
    "Charity / NGO / Non-Profit": _record("sector.charity", "Charity / NGO / Non-Profit", 3, config_key="charity"),
    "Government / Public Sector": _record("sector.government", "Government / Public Sector", 1, config_key="government"),
}

UNRESOLVED_SECTOR_LABELS = frozenset({
    "Payment Processing / Gateway",
    "Lending / Credit Services",
    "Capital Markets / Brokerage",
    "Private Equity / Venture Capital",
    "Hedge Fund",
    "Crowdfunding / P2P Lending",
    "Wholesale / Distribution",
    "Commodities Trading",
    "Agricultural Commodities",
    "IT Services / Outsourcing",
    "Cybersecurity",
    "Artificial Intelligence / ML",
    "Video Games / Esports",
    "Streaming / Content Platforms",
    "Bureau de Change",
    "Licensed Brokerage",
    "Corporate Services Provider",
    "Trust / Fiduciary Services",
    "Travel & Hospitality",
    "Food & Beverage",
    "Fashion / Luxury Goods",
    "Other",
})

ENTITY_TYPE_RECORDS = {
    "Listed Company on Regulated Exchange": _record("entity_type.listed", "Listed Company on Regulated Exchange", 1, config_key="listed company"),
    "Regulated Financial Institution": _record("entity_type.regulated_financial_institution", "Regulated Financial Institution", 1, config_key="regulated financial institution"),
    "Government / Public Sector Entity": _record("entity_type.government", "Government / Public Sector Entity", 1, config_key="government"),
    "Large Private Company (revenue > USD 10m)": _record("entity_type.large_private", "Large Private Company (revenue > USD 10m)", 2, config_key="large private company"),
    "SME / Private Company": _record("entity_type.sme_private", "SME / Private Company", 2, config_key="sme"),
    "Newly Incorporated Company (< 1 year)": _record("entity_type.newly_incorporated", "Newly Incorporated Company (< 1 year)", 3, config_key="newly incorporated"),
    "Trust": _record("entity_type.trust", "Trust", 3, config_key="trust"),
    "Foundation": _record("entity_type.foundation", "Foundation", 3, config_key="foundation"),
    "Regulated Fund (CIS / Licensed)": _record("entity_type.regulated_fund", "Regulated Fund (CIS / Licensed)", 2, config_key="regulated fund"),
    "Unregulated Fund / SPV": _record("entity_type.unregulated_fund_spv", "Unregulated Fund / SPV", 3, config_key="unregulated fund", locked_score=True),
    "Non-Profit Organisation / NGO": _record("entity_type.non_profit", "Non-Profit Organisation / NGO", 3, config_key="non-profit"),
    "Shell Company / Special Purpose Vehicle": _record("entity_type.shell_spv", "Shell Company / Special Purpose Vehicle", 4, config_key="shell company"),
}

OWNERSHIP_RECORDS = {
    "Simple — direct identifiable UBOs": _record("ownership.simple", "Simple — direct identifiable UBOs", 1),
    "1–2 ownership layers": _record("ownership.layers_1_2", "1–2 ownership layers", 2),
    "3+ ownership layers / nominee shareholders": _record("ownership.layers_3_plus", "3+ ownership layers / nominee shareholders", 3),
    "Opaque — UBOs cannot be fully identified": _record("ownership.opaque", "Opaque — UBOs cannot be fully identified", 4),
}

OWNERSHIP_ALIASES = {
    "Complex multi-jurisdiction / opaque structure": "Opaque — UBOs cannot be fully identified",
}

COMPLEXITY_RECORDS = {
    "Simple — single currency, domestic corridors": _record("complexity.simple", "Simple — single currency, domestic corridors", 1),
    "Standard — multi-currency, established corridors": _record("complexity.standard", "Standard — multi-currency, established corridors", 2),
    "Complex — multiple international corridors": _record("complexity.complex", "Complex — multiple international corridors", 3),
    "Very complex — includes monitored corridors": _record("complexity.very_complex", "Very complex — includes monitored corridors", 4),
}

INTRODUCTION_RECORDS = {
    "Direct application — client initiated": _record("introduction.direct", "Direct application — client initiated", 1),
    "Introduced by regulated intermediary / agent": _record("introduction.regulated", "Introduced by regulated intermediary / agent", 1),
    "Introduced by non-regulated intermediary": _record("introduction.non_regulated", "Introduced by non-regulated intermediary", 3),
    "Unsolicited / unknown referral source": _record("introduction.unsolicited", "Unsolicited / unknown referral source", 4),
}

MONTHLY_VOLUME_RECORDS = {
    "Under USD 50,000 per month": _record("monthly_volume.under_usd_50k", "Under USD 50,000 per month", 1),
    "USD 50,000 to USD 500,000 per month": _record("monthly_volume.usd_50k_500k", "USD 50,000 to USD 500,000 per month", 2),
    "USD 500,000 to USD 5,000,000 per month": _record("monthly_volume.usd_500k_5m", "USD 500,000 to USD 5,000,000 per month", 3),
    "Over USD 5,000,000 per month": _record("monthly_volume.over_usd_5m", "Over USD 5,000,000 per month", 4),
}

FAMILY_RECORDS = {
    "sector": SECTOR_RECORDS,
    "entity_type": ENTITY_TYPE_RECORDS,
    "ownership": OWNERSHIP_RECORDS,
    "complexity": COMPLEXITY_RECORDS,
    "introduction": INTRODUCTION_RECORDS,
    "monthly_volume": MONTHLY_VOLUME_RECORDS,
}

# Founder-approved 2026-07-14 exact legacy aliases.  Keep every approved row
# (including case-only duplicates) so the runtime catalogue remains directly
# auditable against RSMP_TIER0A_FOUNDER_ALIAS_DECISIONS.md.
APPROVED_EXACT_ALIAS_ROWS = (
    ("A-001", "complexity", "Standard — multi-currency, low-risk corridors", "Standard — multi-currency, established corridors", 2),
    ("A-002", "complexity", "simple", "Simple — single currency, domestic corridors", 1),
    ("A-003", "complexity", "Simple / predictable", "Simple — single currency, domestic corridors", 1),
    ("A-004", "complexity", "Very complex — includes high-risk corridors", "Very complex — includes monitored corridors", 4),
    ("A-005", "complexity", "domestic low complexity", "Simple — single currency, domestic corridors", 1),
    ("A-006", "complexity", "Multiple international corridors", "Complex — multiple international corridors", 3),
    ("A-007", "complexity", "Very complex / high-risk corridors", "Very complex — includes monitored corridors", 4),
    ("A-008", "complexity", "Domestic low complexity", "Simple — single currency, domestic corridors", 1),
    ("A-009", "complexity", "Domestic low-volume payments", "Simple — single currency, domestic corridors", 1),
    ("A-010", "complexity", "Simple domestic payments", "Simple — single currency, domestic corridors", 1),
    ("A-011", "complexity", "simple domestic single currency", "Simple — single currency, domestic corridors", 1),
    ("A-012", "complexity", "Standard domestic and low-risk cross-border transactions", "Standard — multi-currency, established corridors", 2),
    ("A-013", "complexity", "very complex high-risk corridor", "Very complex — includes monitored corridors", 4),
    ("A-014", "entity_type", "SME", "SME / Private Company", 2),
    ("A-015", "entity_type", "Listed Company", "Listed Company on Regulated Exchange", 1),
    ("A-016", "entity_type", "Shell company", "Shell Company / Special Purpose Vehicle", 4),
    ("A-017", "entity_type", "Newly Incorporated", "Newly Incorporated Company (< 1 year)", 3),
    ("A-018", "entity_type", "Newly Incorporated Private Company", "Newly Incorporated Company (< 1 year)", 3),
    ("A-019", "entity_type", "Small Private Company (revenue < USD 1m)", "SME / Private Company", 2),
    ("A-020", "introduction", "Direct", "Direct application — client initiated", 1),
    ("A-021", "introduction", "Direct application", "Direct application — client initiated", 1),
    ("A-022", "introduction", "direct", "Direct application — client initiated", 1),
    ("A-023", "introduction", "Direct enquiry", "Direct application — client initiated", 1),
    ("A-024", "introduction", "Direct application — I found Onboarda myself", "Direct application — client initiated", 1),
    ("A-025", "introduction", "Direct online application", "Direct application — client initiated", 1),
    ("A-026", "introduction", "Unsolicited", "Unsolicited / unknown referral source", 4),
    ("A-027", "monthly_volume", "Less than USD 50,000", "Under USD 50,000 per month", 1),
    ("A-028", "monthly_volume", "under usd 50,000", "Under USD 50,000 per month", 1),
    ("A-029", "monthly_volume", "USD 25,000 monthly", "Under USD 50,000 per month", 1),
    ("A-030", "monthly_volume", "500000-1000000", "USD 500,000 to USD 5,000,000 per month", 3),
    ("A-031", "monthly_volume", "Over 5,000,000", "Over USD 5,000,000 per month", 4),
    ("A-032", "monthly_volume", "USD 500,000 – USD 5,000,000", "USD 500,000 to USD 5,000,000 per month", 3),
    ("A-033", "monthly_volume", "50,000 - 100,000", "USD 50,000 to USD 500,000 per month", 2),
    ("A-034", "monthly_volume", "More than USD 10,000,000 monthly expected volume", "Over USD 5,000,000 per month", 4),
    ("A-035", "monthly_volume", "Over 5M", "Over USD 5,000,000 per month", 4),
    ("A-036", "monthly_volume", "Under 50,000", "Under USD 50,000 per month", 1),
    ("A-037", "monthly_volume", "Under USD 50K", "Under USD 50,000 per month", 1),
    ("A-038", "monthly_volume", "USD 500,000 - USD 1,000,000", "USD 500,000 to USD 5,000,000 per month", 3),
    ("A-039", "ownership", "Simple", "Simple — direct identifiable UBOs", 1),
    ("A-040", "ownership", "Simple direct ownership", "Simple — direct identifiable UBOs", 1),
    ("A-041", "ownership", "Simple ownership structure", "Simple — direct identifiable UBOs", 1),
    ("A-042", "ownership", "simple", "Simple — direct identifiable UBOs", 1),
    ("A-043", "ownership", "Simple transparent ownership", "Simple — direct identifiable UBOs", 1),
    ("A-044", "ownership", "Simple direct natural-person ownership; one UBO owns 100%", "Simple — direct identifiable UBOs", 1),
    ("A-045", "ownership", "Simple ownership disclosure", "Simple — direct identifiable UBOs", 1),
    ("A-046", "ownership", "Single tier direct ownership", "Simple — direct identifiable UBOs", 1),
    ("A-047", "ownership", "complex opaque nominee structure", "Opaque — UBOs cannot be fully identified", 4),
    ("A-048", "ownership", "Direct individual ownership", "Simple — direct identifiable UBOs", 1),
    ("A-049", "ownership", "Multi-layered opaque nominee trust with undisclosed intermediate shareholders", "Opaque — UBOs cannot be fully identified", 4),
    ("A-050", "ownership", "Simple direct ownership by one natural person", "Simple — direct identifiable UBOs", 1),
    ("A-051", "ownership", "Complex multi-layer ownership with nominee exposure", "3+ ownership layers / nominee shareholders", 3),
    ("A-052", "ownership", "Complex nominee 3+ layers", "3+ ownership layers / nominee shareholders", 3),
    ("A-053", "ownership", "complex nominee 3+ layers", "3+ ownership layers / nominee shareholders", 3),
    ("A-054", "ownership", "Direct ownership; one natural-person UBO at 100%", "Simple — direct identifiable UBOs", 1),
    ("A-055", "ownership", "Direct UBO ownership", "Simple — direct identifiable UBOs", 1),
    ("A-056", "ownership", "Simple single-layer ownership with one UBO at 100%", "Simple — direct identifiable UBOs", 1),
    ("A-057", "ownership", "Three direct individual shareholders, all above threshold. Ownership totals 100 percent.", "Simple — direct identifiable UBOs", 1),
    ("A-058", "ownership", "Complex multi-layered nominee trust structure with opaque ownership", "Opaque — UBOs cannot be fully identified", 4),
    ("A-059", "ownership", "Complex multi-layered trust and nominee ownership structure with opaque controller arrangements", "Opaque — UBOs cannot be fully identified", 4),
    ("A-060", "ownership", "Layered nominee/trust ownership with offshore holding company", "3+ ownership layers / nominee shareholders", 3),
    ("A-061", "ownership", "Layered trust / nominee ownership with offshore holding company", "3+ ownership layers / nominee shareholders", 3),
    ("A-062", "ownership", "multi-layered nominee opaque ownership", "Opaque — UBOs cannot be fully identified", 4),
    ("A-063", "ownership", "Multi-layered nominee opaque ownership via holding SPV", "Opaque — UBOs cannot be fully identified", 4),
    ("A-064", "ownership", "Nominee multi-layer ownership with opaque structure", "Opaque — UBOs cannot be fully identified", 4),
    ("A-065", "ownership", "Opaque multi-layered nominee shareholder structure", "Opaque — UBOs cannot be fully identified", 4),
    ("A-066", "ownership", "opaque nominee trust", "Opaque — UBOs cannot be fully identified", 4),
    ("A-067", "ownership", "Simple ownership structure with one beneficial owner", "Simple — direct identifiable UBOs", 1),
    ("A-068", "sector", "Software", "Software / SaaS", 2),
    ("A-069", "sector", "Fintech", "Fintech / Payments", 3),
    ("A-070", "sector", "Construction / Infrastructure", "Construction", 3),
    ("A-071", "sector", "fintech", "Fintech / Payments", 3),
    ("A-072", "sector", "Software/SaaS", "Software / SaaS", 2),
    ("A-073", "sector", "Construction and infrastructure", "Construction", 3),
    ("A-074", "sector", "Government", "Government / Public Sector", 1),
    ("A-075", "sector", "Import / Export Trading", "Import / Export", 3),
    ("A-076", "sector", "Import export trading", "Import / Export", 3),
    ("A-077", "sector", "Logistics", "Logistics / Freight Forwarding", 2),
)


def _build_family_aliases() -> Dict[str, Dict[str, str]]:
    aliases: Dict[str, Dict[str, str]] = {"ownership": dict(OWNERSHIP_ALIASES)}
    normalized_targets: Dict[tuple[str, str], str] = {}
    for row_id, family, legacy_label, canonical_label, approved_score in APPROVED_EXACT_ALIAS_ROWS:
        record = FAMILY_RECORDS.get(family, {}).get(canonical_label)
        if not record or int(record["score"]) != approved_score:
            raise RuntimeError(f"Invalid approved alias target in {row_id}")
        normalized_key = (family, normalize_controlled_value(legacy_label))
        prior_target = normalized_targets.get(normalized_key)
        if prior_target and prior_target != canonical_label:
            raise RuntimeError(f"Conflicting normalized approved aliases in {row_id}")
        normalized_targets[normalized_key] = canonical_label
        aliases.setdefault(family, {})[legacy_label] = canonical_label
    return aliases


FAMILY_ALIASES = _build_family_aliases()


def _normalized_lookup(records: Mapping[str, Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {normalize_controlled_value(label): record for label, record in records.items()}


def resolve_controlled_score(
    family: str,
    raw_value: Any,
    *,
    configured_scores: Optional[Mapping[str, Any]] = None,
    config_version: str = REGISTRY_VERSION,
) -> ControlledResolution:
    """Resolve a controlled label by exact normalized identity.

    No substring or generic score fallback exists here.  Configurable sector and
    entity scores are read only by the record's exact canonical config key.
    """
    family_key = str(family or "").strip().lower()
    raw_text = str(raw_value or "")
    normalized = normalize_controlled_value(raw_text)
    records = FAMILY_RECORDS.get(family_key, {})
    aliases = FAMILY_ALIASES.get(family_key, {})
    alias_target = {
        normalize_controlled_value(alias): target for alias, target in aliases.items()
    }.get(normalized)
    if alias_target:
        normalized = normalize_controlled_value(alias_target)
    record = _normalized_lookup(records).get(normalized)
    if not record:
        return ControlledResolution(
            family=family_key,
            raw_value=raw_text,
            normalized_value=normalize_controlled_value(raw_text),
            status="unresolved",
            config_version=str(config_version or REGISTRY_VERSION),
        )

    score = int(record["score"])
    config_key = str(record.get("config_key") or "")
    if configured_scores is not None and config_key and not record.get("locked_score"):
        if not isinstance(configured_scores, Mapping):
            return ControlledResolution(
                family=family_key,
                raw_value=raw_text,
                normalized_value=normalize_controlled_value(raw_text),
                status="unresolved",
                controlled_id=str(record["id"]),
                canonical_label=str(record["label"]),
                config_key=config_key,
                config_version=str(config_version or REGISTRY_VERSION),
            )
        exact_scores = {
            normalize_controlled_value(key): value for key, value in configured_scores.items()
        }
        configured = exact_scores.get(normalize_controlled_value(config_key))
        try:
            configured = int(configured)
        except (TypeError, ValueError):
            configured = None
        if configured not in {1, 2, 3, 4}:
            return ControlledResolution(
                family=family_key,
                raw_value=raw_text,
                normalized_value=normalize_controlled_value(raw_text),
                status="unresolved",
                controlled_id=str(record["id"]),
                canonical_label=str(record["label"]),
                config_key=config_key,
                config_version=str(config_version or REGISTRY_VERSION),
            )
        score = configured

    return ControlledResolution(
        family=family_key,
        raw_value=raw_text,
        normalized_value=normalize_controlled_value(raw_text),
        status="mapped",
        score=score,
        controlled_id=str(record["id"]),
        canonical_label=str(record["label"]),
        config_key=config_key,
        config_version=str(config_version or REGISTRY_VERSION),
    )


COUNTRY_EXACT_ALIASES = {
    normalize_controlled_value("Hong Kong SAR"): "hong kong",
    normalize_controlled_value("Congo (DRC)"): "democratic republic of congo",
    normalize_controlled_value("Türkiye"): "turkey",
}


def resolve_tier0a_country_alias(value: Any) -> str:
    """Return only the three approved Tier 0A geography aliases."""
    normalized = normalize_controlled_value(value)
    return COUNTRY_EXACT_ALIASES.get(normalized, normalized)


def controlled_value_hash(family: str, normalized_value: Any) -> str:
    """Return the non-reversible short identity used by unmapped sentinels."""
    payload = f"{str(family or '').strip().lower()}\x1f{normalize_controlled_value(normalized_value)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:SHORT_HASH_LENGTH]


def unresolved_mapping_sentinel(family: str, normalized_value: Any) -> str:
    family_key = re.sub(r"[^a-z0-9_]+", "_", str(family or "").strip().lower()).strip("_")
    return f"{UNMAPPED_SENTINEL_PREFIX}{family_key}:{controlled_value_hash(family_key, normalized_value)}"


def structured_mapping_evidence(
    resolution: ControlledResolution,
    *,
    application_id: Any,
    request_id: Any,
    config_version: Any,
) -> Dict[str, Any]:
    """Build durable evidence without embedding the raw value in the sentinel."""
    digest = controlled_value_hash(resolution.family, resolution.normalized_value)
    evidence = {
        "family": resolution.family,
        "raw_value": resolution.raw_value,
        "normalized_value": resolution.normalized_value,
        "hash": digest,
        "application_id": str(application_id or ""),
        "request_id": str(request_id or ""),
        "config_version": str(config_version or resolution.config_version or REGISTRY_VERSION),
        "resolution_status": resolution.status,
        "controlled_id": resolution.controlled_id,
        "canonical_label": resolution.canonical_label,
        "score": resolution.score,
    }
    if resolution.status == "unresolved":
        evidence["sentinel"] = unresolved_mapping_sentinel(
            resolution.family, resolution.normalized_value
        )
    return evidence


def _reason_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return _reason_list(parsed)
    return []


def reconcile_mapping_staleness(
    current_reasons: Any,
    previous_reasons: Any,
    mapping_evidence: Any,
) -> list[str]:
    """Replace mapping sentinels while preserving unrelated stale reasons.

    A fresh score recomputes every controlled field.  Therefore all previous
    mapping sentinels can be replaced atomically by the currently unresolved
    set, while non-mapping ``stale:*`` controls remain untouched.
    """
    output: list[str] = []

    def append(reason: Any):
        text = str(reason or "").strip()
        if text and text not in output:
            output.append(text)

    for reason in _reason_list(current_reasons):
        append(reason)
    for reason in _reason_list(previous_reasons):
        if reason.startswith("stale:") and not reason.startswith(UNMAPPED_SENTINEL_PREFIX):
            append(reason)
    for item in mapping_evidence or []:
        if not isinstance(item, Mapping) or item.get("resolution_status") != "unresolved":
            continue
        append(item.get("sentinel"))
    return output

# Source Inventory

## Pre-Fix Country-Risk Sources

| Source | Location | Type | Status After PR-CR1 | Finding |
|---|---|---:|---|---|
| Hardcoded FATF/sanctions/high-risk sets | `arie-backend/rule_engine.py` | Code | Fallback only | Mixed FATF, sanctions, secrecy, and internal policy; no per-country source metadata. |
| Static jurisdiction config | `arie-backend/jurisdiction_config.json` | JSON | Legacy evidence/fallback | Contains stale FATF-style entries; not authoritative after PR-CR1. |
| `risk_config.country_risk_scores` | `risk_config` DB table | JSON blob | Legacy fallback only | No country-level source URL, effective date, version, checksum, or FATF publication reference. |
| Backoffice static country lists | `arie-backoffice.html` | Frontend static data | Display fallback only | Previously presented editable static lists as source-like settings. |
| Memo jurisdiction constants | `arie-backend/memo_handler.py` | Code | Replaced by canonical lookup | Previously emitted generic source text without snapshot evidence. |

## New Canonical Source

| Source | Location | Type | Status | Evidence |
|---|---|---:|---|---|
| Country-risk snapshot metadata | `country_risk_snapshots` | DB table | Authoritative active snapshot | `id`, `version`, `status`, `source_name`, `source_url`, `source_publication_date`, `effective_date`, `imported_at`, `last_checked_at`, `imported_by`, `checksum`, `freshness_days`, `notes`. |
| Country-risk entries | `country_risk_entries` | DB table | Authoritative active country records | Country name/key, ISO alpha-2/alpha-3, risk rating/score, FATF status, sanctions/high-risk status, source URL/name, source publication date, effective date, status, checksum, prior values. |
| Seed snapshot | `arie-backend/country_risk.py` | Seed/service module | Initial active snapshot | `ACTIVE_SNAPSHOT_ID=country-risk-fatf-2026-02-13-v1`, `ACTIVE_SNAPSHOT_VERSION=FATF-2026-02-13+REGMIND-POLICY-V1`. |
| Migration | `arie-backend/migrations/scripts/migration_035_country_risk_governance.sql` | Schema migration | Creates governed tables | Table/index creation for country-risk snapshots and entries. |
| API | `GET /api/config/country-risk` | Backend endpoint | UI/API provenance contract | Returns active snapshot plus entries, or a single lookup with source/effective/freshness metadata. |

## Seeded FATF Benchmark

FATF increased monitoring:
Algeria, Angola, Bolivia, Bulgaria, Cameroon, Cote d'Ivoire, Democratic Republic of Congo, Haiti, Kenya, Kuwait, Laos, Lebanon, Monaco, Namibia, Nepal, Papua New Guinea, South Sudan, Syria, Venezuela, Vietnam, Virgin Islands (UK), Yemen.

FATF call for action:
Iran, North Korea, Myanmar.

Non-FATF internal policy classifications remain explicitly marked with `high_risk_status` or internal source references rather than represented as FATF status.

## Source-of-Truth Verdict

After PR-CR1, risk scoring and memo generation use the canonical country-risk lookup first. Legacy DB/static/code lists are retained only as fallback for unknown/non-canonical countries and UI reference compatibility. Unknown countries fail safe to MEDIUM, never LOW.

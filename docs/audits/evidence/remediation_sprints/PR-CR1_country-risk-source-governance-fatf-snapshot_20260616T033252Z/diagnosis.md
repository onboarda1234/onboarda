# Diagnosis

## Workstream

PR-CR1 - Country Risk Source Governance and FATF Snapshot

## Base

- Base branch: `origin/main`
- Base SHA: `07c992d7716183226d53f70bf0d01bf7e87da874`
- Working branch: `codex/pr-cr1-country-risk-source-governance`

## Findings From Current `origin/main`

Country risk was not governed by one source of truth.

Evidence:
- `arie-backend/rule_engine.py` contained hardcoded `FATF_GREY`, `FATF_BLACK`, `SANCTIONED`, `HIGH_RISK`, and low-risk fallback sets.
- `arie-backend/jurisdiction_config.json` contained a separate static FATF/jurisdiction mapping.
- `risk_config.country_risk_scores` stored another JSON country-risk map with no country-level source URL, source publication date, effective date, checksum, or snapshot ID.
- `arie-backoffice.html` contained a static `COUNTRY_RISK_LISTS` object and presented those values as editable country risk classifications.
- `arie-backend/memo_handler.py` used local jurisdiction constants and emitted the generic source string `FATF/internal jurisdiction tables`.

## FATF Alignment Problem

The legacy lists mixed FATF status with sanctions, secrecy, and internal high-risk policy. They also retained stale entries, including Pakistan in FATF grey-style lists, without source metadata proving the current FATF status.

Official FATF references used for the PR-CR1 seed:
- FATF increased monitoring statement, 2026-02-13: `https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/increased-monitoring-february-2026.html`
- FATF call-for-action statement, 2026-02-13: `https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/Call-for-action-february-2026.html`

## Risk

Before PR-CR1, country risk could be silently stale or divergent across backend scoring, memo evidence, admin UI, and JSON/DB fallback data. RegMind could not defensibly prove the source/version/effective date of a country-risk value used in scoring or memo generation.

## Classification

- Current authoritative source before fix: mixed/inconsistent.
- FATF update mechanism before fix: static/hardcoded/manual.
- Source metadata before fix: missing at country-record level.
- Freshness detection before fix: missing.
- Memo provenance before fix: generic and non-auditable.

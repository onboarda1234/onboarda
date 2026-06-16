# Root Cause

Country risk had grown as a configuration convenience rather than as a governed compliance policy.

Root causes:
- Multiple country-risk definitions existed in code, JSON, DB config, memo logic, and UI static arrays.
- FATF status, sanctions, secrecy jurisdictions, and internal policy risk were mixed in the same lists.
- `risk_config.country_risk_scores` could be changed without country-level source metadata.
- Risk scoring and memo generation did not share a versioned country-risk snapshot.
- No checksum, effective date, source publication date, or freshness rule existed for country-risk classifications.
- Unknown country fallback returned a standard score without explicit provenance or officer-facing warning.

Control failure:
RegMind could not prove which source supported a country-risk classification used for onboarding risk score, memo reasoning, or review routing.

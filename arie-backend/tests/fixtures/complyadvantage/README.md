# Synthetic ComplyAdvantage normalizer fixtures

These fixtures model the s2-s7 CA response shapes used by C1.b tests. They are fully synthetic: fake names, fake dates of birth, fake jurisdictions, fake source identifiers, and `test-fixture.example.com` URLs only. No sandbox captures or real PII are committed.

- `clean_baseline.json` — s1 clean/zero-alert workflow shape.
- `sanctions_canonical.json` — sanctions/watchlist/PEP mixed alert shape.
- `pep_canonical.json` — canonical PEP shape with multiple PEP classes.
- `rca_canonical.json` — RCA person shape with relationship objects.
- `adverse_media_multi_source.json` — adverse media shape with multiple article buckets and snippets.
- `company_canonical.json` — company profile match shape.
- `monitoring_on_full_optional_fields.json` — full optional customer input fields with clean response.
- `two_pass_strict_misses_relaxed_catches.json` — synthetic two-pass rationale where relaxed finds the canonical match.
- `webhook_case_created.json` — synthetic `CASE_CREATED` webhook envelope.
- `webhook_case_alert_list_updated.json` — synthetic `CASE_ALERT_LIST_UPDATED` webhook envelope.
- `webhook_unknown_type.json` — synthetic unknown webhook envelope for accepted-and-ignored behavior.

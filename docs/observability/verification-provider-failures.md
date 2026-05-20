# Verification Provider Failure Observability

PR8 emits `verification_provider_telemetry` when document verification fails before document checks can be trusted.

Saved CloudWatch Logs Insights queries:

- `docs/observability/cloudwatch/verification_provider_failures.cwlogs` groups failures by provider, classification, reason, document type, MIME type, and file size band.
- `docs/observability/cloudwatch/verification_claude_invalid_pdf.cwlogs` lists recent Claude invalid-PDF failures for triage.

PII note: the log line intentionally excludes filenames, extracted text, party names, document numbers, and raw provider response bodies. Known staging PII decrypt noise is marked only as `pii_context_signal=true` if it appears in an exception string; it is not treated as the Claude invalid-PDF root cause.

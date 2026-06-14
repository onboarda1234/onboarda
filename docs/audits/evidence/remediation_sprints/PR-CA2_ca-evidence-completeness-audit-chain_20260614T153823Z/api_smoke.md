# PR-CA2 Staging API Smoke

Status: pending.

Required post-merge staging API smoke evidence:

- Screening queue exposes provider references where available.
- Application detail exposes provider references where available.
- Evidence completeness state exists and includes missing reason when not complete.
- CA-specific audit events exist for request/result/hit/review/disposition path where exercised.
- Officer disposition event includes before/after state and provider refs.
- Approval blocker can be traced to exact subject/hit/provider refs where applicable.
- No tokens, secrets, cookies, or webhook signatures appear in API/audit responses.
- Existing PR-CA1 provider status remains correct.

Raw JSON evidence should be saved in `runtime_json/` with secrets and personal data redacted.

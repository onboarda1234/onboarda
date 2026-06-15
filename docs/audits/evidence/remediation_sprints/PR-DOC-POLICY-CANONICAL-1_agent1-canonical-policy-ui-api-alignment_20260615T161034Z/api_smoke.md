# API Smoke

Local API/config smoke:

- `build_document_policy_payload()` exported successfully to `runtime_json/document_policy_payload.json`.
- `summarise_document_policies()` exported successfully to `runtime_json/document_policy_summary.json`.
- Endpoint-level regression passed:
  `DocumentPolicyConfigApiTest::test_document_policy_config_endpoint_exposes_canonical_registry`.
- The new route is registered at `/api/config/document-policies`.
- Existing `/api/config/verification-checks` remains backward compatible and includes the canonical registry payload.

Post-merge staging API smoke:

- Pending. This cannot be truthfully completed until the PR is merged, main is deployed to staging, and `/api/version` reports the merged SHA/image tag.


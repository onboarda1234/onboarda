# Full Suite / CI Results

Initial PR CI failures were stale static assertions that still expected the pre-fix verification renderer boundaries and inline failed/warn rendering model.

Fix applied:

- updated `test_enhanced_requirement_settings.py` to the new verification-coverage + technical-audit-drawer contract
- updated `test_ex11_ai_advisory_labels.py` to the new `buildVerificationResultsHtml -> renderDocumentAuditDetails` function boundary
- preserved simulated-AI labeling and check-type legend inside the technical audit drawer

Post-fix local regression subset:

- `118 passed`

Current GitHub CI status:

- rerun pending after amended push

To be updated after:

- PR opened
- GitHub Actions CI completes
- merge commit lands on `main`
- `deploy-staging` completes

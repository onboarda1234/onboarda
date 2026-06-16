# Test Results

## Focused PR-DOC-RECON-1 / Enhanced Requirement Tests

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest tests/test_pr_doc_recon1_policy_reconciliation.py tests/test_pr_doc2a_agent1_evidence_control.py tests/test_application_enhanced_requirements.py::test_backoffice_enhanced_requirement_upload_links_document_under_review_and_audits tests/test_application_enhanced_requirements.py::test_portal_document_upload_fulfils_requested_enhanced_requirement -q
```

Result:

```text
18 passed in 2.72s
```

## Regression Batches

Commands run before the full suite:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest tests/test_doc_policy_canonical_registry.py tests/test_pr_doc_ui1_backoffice_document_review.py tests/test_enhanced_requirement_settings.py -q
```

Result: `34 passed`

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest tests/test_upload_latency_contracts.py tests/test_application_enhanced_requirements.py -q
```

Result: `75 passed`

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest tests/test_agent_config_integrity.py tests/test_ai_agent_catalog.py -q
```

Result: `37 passed`

## Coverage Added / Updated

- Document Verification Policies page no longer shows top registry dashboard.
- Underlying Verification Check Configuration remains visible.
- Enhanced / EDD checks are shown in the simple settings editor.
- AI Agents -> Agent 1 wording and counts align with current settings scope.
- Enhanced Requirement uploads map to canonical doc types.
- Active enhanced evidence queues Agent 1 verification jobs.
- Manual-review-only enhanced evidence is not presented as runtime verified.
- Portal enhanced upload actor model remains client-safe.
- Existing DOC2A tests updated to reflect simplified settings while preserving policy metadata/auditability checks.

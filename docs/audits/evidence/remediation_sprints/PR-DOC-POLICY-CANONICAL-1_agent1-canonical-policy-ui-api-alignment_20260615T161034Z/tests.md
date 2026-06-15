# Tests

Targeted tests passed:

```text
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_doc_policy_canonical_registry.py \
  arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py \
  arie-backend/tests/test_verification_matrix.py \
  arie-backend/tests/test_agent_config_integrity.py \
  arie-backend/tests/test_person_upload.py

159 passed in 7.55s
```

Coverage added/updated:

- Canonical registry status and method classifications.
- Manual-review-only/future policies are not presented as runtime verified.
- Upload allowlist values map to canonical policy, alias, manual review, or future status.
- Identity aliases map to canonical `national_id`.
- Register of Directors, Passport, and Proof of Address reuse one canonical policy across multiple workflows.
- Director/UBO/ownership workflows expose required blockers and triggers without duplicating checks.
- Company name change maps only to Certificate of Name Change for pilot and remains manual review only.
- EDD pilot documents distinguish active SOW/SOF/bank statement/bank reference from deeper manual-only EDD evidence.
- Monitoring/regulatory evidence remains manual review only in pilot.
- SAR/STR remains future/enterprise and inactive.
- Unknown/unclassified documents block automated reliance.
- `/api/config/document-policies` exposes the canonical registry payload.
- Application Review UI keeps routine technical details out of the default card and keeps "Approval blocked" out of the green tone.
- Document Verification Policies and AI Agent Pipeline terminology align.


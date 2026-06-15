# Browser Smoke

Local/static coverage:

- Static UI regression tests passed in `test_pr_doc2a_agent1_evidence_control.py`.
- These tests cover the back-office Document Verification Policies terminology, Agent 1 pipeline boundary text, status truthfulness, SAR/STR future scope, and Application Review colour regression.

Required staging browser smoke:

- Pending until merge and staging deployment.

Staging smoke checklist to run after deploy:

1. Open Document Verification Policies.
2. Confirm document-first table/cards.
3. Confirm Active / Manual review only / Future / enterprise statuses.
4. Confirm workflow mapping for Register of Directors, Passport, Proof of Address, and Company Name Change.
5. Confirm Agent 1 pipeline copy aligns with canonical document policy language.
6. Confirm Application Review document rows remain action-first.
7. Confirm portal onboarding upload/verification path still works.
8. Confirm technical details expansion still works.
9. Confirm no console, network, or server errors.


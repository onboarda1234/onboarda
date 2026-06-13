# Full Suite Results

Local targeted and regression suites passed.

Local full backend suite is not used as closure evidence on this machine because prior remediation runs repeatedly hit the known native WeasyPrint/Pango CFFI segmentation fault path during `evidence_pack_export.py` import.

Authoritative full-suite evidence for PR-4B must come from GitHub CI after the PR is opened:
- `lint-and-test`
- `pdf-tests`
- `docker-validate`

`FSI-007` must remain open or partially fixed until GitHub CI passes and merged-main staging validation is complete.

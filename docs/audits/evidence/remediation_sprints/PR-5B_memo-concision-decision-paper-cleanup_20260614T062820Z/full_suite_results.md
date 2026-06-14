# Full Suite Results

## Local Full-Suite Status

Local full relevant backend execution is blocked by the existing WeasyPrint/Pango CFFI native dependency fault on this macOS environment.

Observed crash:

- Command attempted: combined memo/PDF batch including `arie-backend/tests/test_phase3_memo_integrity.py`
- Failure mode: process exit `139`, Python segmentation fault
- Import path: `test_phase3_memo_integrity.py` -> `server.py` -> `evidence_pack_export.py` -> `weasyprint` -> `cffi` / Pango shared library load

This is the same native dependency class previously documented in remediation evidence. It is not introduced by PR-5B. Local `test_pdf_generator.py` skips cleanly when run directly, but tests importing `server.py` can still hit the native import path before skip guards.

## Authoritative Full-Suite Evidence Required

GitHub CI must provide authoritative full-suite / PDF-native-path evidence before PR-5B can be treated as complete.

PR-5B must remain incomplete until:

- GitHub CI passes the relevant backend checks.
- The PR is merged into main.
- Merged main is deployed to staging.
- `/api/version` matches merged main SHA.
- Staging API/PDF smoke and browser smoke pass.

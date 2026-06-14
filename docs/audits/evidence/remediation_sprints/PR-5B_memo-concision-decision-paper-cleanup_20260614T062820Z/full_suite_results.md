# Full Suite Results

## Local Full-Suite Status

Final local backend suite passed after the CI-fix patch.

Command:

`pytest arie-backend/tests/ -q --tb=short --ignore=arie-backend/tests/test_pdf_generator.py`

Result:

- 5295 passed
- 17 skipped
- Duration: 281.59s

Corrective browser-defect patch full-suite run:

Command:

`pytest arie-backend/tests/ -q --tb=short --ignore=arie-backend/tests/test_pdf_generator.py`

Result:

- 5298 passed
- 17 skipped
- Duration: 190.80s

Second corrective memo-output cache invalidation patch full-suite run:

Command:

`pytest arie-backend/tests/ -q --tb=short --ignore=arie-backend/tests/test_pdf_generator.py`

Result:

- 5301 passed
- 17 skipped
- Duration: 233.50s

Local native PDF module:

`pytest -q arie-backend/tests/test_pdf_generator.py`

Result:

- 8 skipped locally because native WeasyPrint/Pango libraries are unavailable

The first full-suite attempt against the initial CI-fix patch failed only in
`arie-backend/tests/test_enhanced_requirement_memo.py` because the condensed
memo section rebuild dropped the established `enhanced_review_edd` section. The
final run passed after restoring that section through the existing sanitized
enhanced-review section builder.

## GitHub CI Evidence Required

GitHub CI passed for PR #482 before the initial PR-5B merge and for PR #483
before the first corrective merge. GitHub CI must also pass for the second
corrective memo-output cache invalidation PR before that PR can be merged.

PR-5B remains incomplete until:

- GitHub CI passes the relevant backend checks.
- The PR is merged into main.
- Merged main is deployed to staging.
- `/api/version` matches merged main SHA.
- Staging API/PDF smoke and browser smoke pass.

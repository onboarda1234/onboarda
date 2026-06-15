# PR-CA4C API Smoke Evidence

## Local API / Builder Smoke

Covered by targeted tests:

- Summary list payload omits heavy evidence by default.
- Full evidence remains available when explicitly requested.
- Search matches subject name, company name, ARF/application reference, and Mesh provider references.
- Pagination metadata is respected.
- Entity pending wording uses broad AML language.
- `Other person` type filter is available only when uncategorized person rows exist.

Key commands:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_screening_queue.py -q
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_provider_label_policy.py -q
```

Result:

```text
passed
```

Raw performance evidence:

- `runtime_json/local_queue_payload_perf.json`

## Staging API Smoke

Status:

- Pending until PR merge, staging deployment, and `/api/version` confirmation.

Required post-merge checks:

- Screening Queue endpoint returns summary list payload.
- Pagination works.
- Search matches subject/company/ARF/provider refs.
- Detail/evidence remains available on row view.
- Legacy `Company sanctions screening pending` wording is absent from relevant API/UI-copy fields.
- PR-CA4B memo/adverse-media parity still passes.
- PR-CA1/CA2/CA3 regressions still pass.


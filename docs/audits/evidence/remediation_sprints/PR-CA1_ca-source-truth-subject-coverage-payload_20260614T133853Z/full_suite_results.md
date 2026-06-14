# PR-CA1 Full Suite Results

## First Full Backend Suite Attempt

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
3 failed, 5323 passed, 25 skipped in 344.15s
```

Failure analysis:

- `test_backoffice_review_audit.py::TestPhaseSixComplyAdvantageStatusUI::test_api_status_panel_lists_complyadvantage_with_correct_responsibility`
  - stale assertion expected old `ComplyAdvantage KYB / Media / Monitoring`.
- `test_monitoring_alerts_sprint1_static.py::test_monitoring_alert_detail_renders_compact_provider_evidence_without_fake_links`
  - stale assertion expected old `ComplyAdvantage payload`.
- `test_phase6_complyadvantage_readiness.py::test_complyadvantage_status_is_not_live_when_unconfigured`
  - stale assertion expected old `implementation_status=in_progress`.

The stale assertions were updated to the PR-CA1 source-of-truth behavior and rerun successfully. See `test_results.md`.

## Second Full Backend Suite Attempt

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
Fatal Python error: Segmentation fault
```

Crash path:

```text
weasyprint -> cffi -> evidence_pack_export.py -> server.py
```

## Third Full Backend Suite Attempt

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests --ignore=arie-backend/tests/test_temp_db_import_order.py -q
```

Result:

```text
Fatal Python error: Segmentation fault
```

Crash path:

```text
weasyprint -> cffi -> evidence_pack_export.py -> server.py
```

## GitHub CI

Run: `https://github.com/onboarda1234/onboarda/actions/runs/27500906323`

Head SHA: `81a99caf79bd5226f1f7fade9d99c8facbeac134`

Result:

```text
lint-and-test: passed in 13m23s
pdf-tests: passed in 35s
docker-validate: passed in 50s
CodeRabbit: passed / review skipped
```

## Verdict

Local full-suite rerun is blocked by a native WeasyPrint/CFFI dependency crash in this macOS environment. Targeted PR-CA1 tests and the required closed-remediation subset pass locally. GitHub CI passed for branch head `81a99caf79bd5226f1f7fade9d99c8facbeac134`.

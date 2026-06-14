# PR-CA3 Full Suite Results

## Local Full Backend Suite

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
BLOCKED locally by native WeasyPrint/Pango CFFI segmentation fault during test collection.
Exit code: 139.
```

Observed failure:

```text
Fatal Python error: Segmentation fault
...
File "/opt/homebrew/lib/python3.11/site-packages/weasyprint/text/ffi.py", line 451 in _dlopen
...
File "/private/tmp/onboarda-pr-ca3/arie-backend/evidence_pack_export.py", line 21 in <module>
File "/private/tmp/onboarda-pr-ca3/arie-backend/server.py", line 972 in <module>
File "/private/tmp/onboarda-pr-ca3/arie-backend/tests/test_temp_db_import_order.py", line 12 in <module>
```

Notes:

- This is the known local native dependency failure mode called out in the PR instructions.
- The corrected PR-CA3 focused and closed-control regression subsets passed locally.
- Full-suite closure must rely on GitHub CI after PR creation unless the native local WeasyPrint/Pango issue is resolved.

## Earlier Local Full Suite Before Final Fixes

An earlier full-suite attempt completed collection and execution but exposed five regressions:

- Three stale-screening message regressions.
- Two screening review false-positive clearance projection regressions.

Those were fixed and verified by the focused affected set:

```text
199 passed in 2.51s
```

## GitHub CI

Pending until PR is opened.

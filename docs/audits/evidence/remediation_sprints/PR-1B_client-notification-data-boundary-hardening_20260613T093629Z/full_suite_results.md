# PR-1B Full Suite Results

## Local Full Backend Suite

### Initial Branch Run

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
5253 passed, 25 skipped in 273.13s (0:04:33)
```

Verdict:

`PASS`

This local run did not reproduce the prior PR-1 WeasyPrint/Pango CFFI segmentation fault.

### Post-Review-Feedback Rerun

After addressing CodeRabbit feedback, targeted tests were rerun successfully. A second full-suite attempt was started:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
Exit 139 - segmentation fault during WeasyPrint/Pango CFFI import through evidence_pack_export.py
```

This matches the known local native dependency instability observed during PR-1. The failure occurred during collection in the WeasyPrint/Pango CFFI import path, before PR-1B tests ran.

## GitHub CI

GitHub CI must be treated as authoritative for the final branch commit if the local native dependency crash recurs.

# PR-1 Full Suite Results

## Command

```bash
/opt/homebrew/bin/python3.11 -m pytest tests -q
```

## Result

Full suite is currently blocked by local native PDF runtime instability, not by a PR-1 assertion failure.

### Full run before stale expectation reconciliation

The first full run completed and reported:

```text
2 failed, 5248 passed, 25 skipped in 254.92s
```

Both failures were stale tests that still expected a client token to read the internal `/api/applications` list. Those expectations were updated to the PR-1 contract:

- internal `/api/applications` returns `403` for client tokens
- `/api/portal/applications` remains client-readable and portal-safe

The updated failing cases passed in targeted reruns.

### Clean retries after stale expectation reconciliation

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests -q
```

Result:

```text
Exit 139 - segmentation fault during WeasyPrint/Pango CFFI import through evidence_pack_export.py
```

Second retry produced the same exit 139 native crash.

Fallback command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests --ignore=tests/test_temp_db_import_order.py -q
```

Result:

```text
Exit 139 - segmentation fault during WeasyPrint/Pango CFFI import through evidence_pack_export.py
```

## Verdict

Full-suite validation is `BLOCKED / NEEDS EVIDENCE` due to the existing WeasyPrint/Pango native dependency crash. Targeted PR-1 and adjacent regression coverage passed.

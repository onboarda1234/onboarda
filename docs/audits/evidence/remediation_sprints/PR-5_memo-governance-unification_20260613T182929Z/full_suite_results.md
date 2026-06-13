# PR-5 Full Suite Results

## First Full-Suite Attempt

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
57 failed, 5221 passed, 25 skipped in 196.46s (0:03:16)
```

Interpretation:

- Failures were caused by test fixtures that inserted `review_status='approved'` memo rows without an `approval_reason`.
- PR-5 intentionally makes approval reason a mandatory approval invariant.
- Fixtures were updated so tests not about memo approval reasons supply a neutral documented reason.
- The previously failing subset was rerun and passed: `84 passed in 3.07s`.

## Second Full-Suite Attempt

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
Process exited 139 - Fatal Python error: Segmentation fault
```

Blocker:

- Local native dependency crash during `evidence_pack_export.py` import.
- Stack trace enters `weasyprint/text/ffi.py` via CFFI/Pango loading.
- This is the known local WeasyPrint/Pango CFFI blocker already tracked in the remediation program.

Conclusion:

- Full local backend suite evidence is BLOCKED by the native PDF dependency path.
- Targeted PR-5 and FSI regression evidence passed under the repo-required Python 3.11 runtime.
- GitHub CI must provide authoritative full-suite evidence before merged-main closure.

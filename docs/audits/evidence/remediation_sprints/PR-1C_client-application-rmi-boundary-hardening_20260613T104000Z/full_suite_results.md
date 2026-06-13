# Full Suite Results

Local full backend suite:

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:
- BLOCKED locally by the known WeasyPrint/Pango CFFI segmentation fault during test collection.
- Failure occurs while importing `evidence_pack_export.py` through `server.py`.
- Exit code: 139.

GitHub CI requirement:
- GitHub CI must be used as the authoritative full-suite evidence for this branch.
- FSI-001 must not be marked CLOSED from local targeted tests alone.

# Full Suite Results

Command:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests
```

Result:

```text
BLOCKED locally by native WeasyPrint/Pango CFFI segmentation fault during test collection.
The crash occurs while importing evidence_pack_export.py via server.py from tests/test_temp_db_import_order.py.
```

Relevant stack excerpt:

```text
Fatal Python error: Segmentation fault
...
File "/opt/homebrew/lib/python3.11/site-packages/weasyprint/text/ffi.py", line 451 in _dlopen
File "/opt/homebrew/lib/python3.11/site-packages/weasyprint/__init__.py", line 372 in <module>
File ".../arie-backend/evidence_pack_export.py", line 20 in <module>
File ".../arie-backend/server.py", line 964 in <module>
File ".../arie-backend/tests/test_temp_db_import_order.py", line 12 in <module>
```

Disposition:

- Do not treat local full-suite evidence as passed.
- Use GitHub CI as full-suite evidence only after PR creation if it passes the configured backend CI suite.
- `FSI-007` cannot be closed from local evidence alone.

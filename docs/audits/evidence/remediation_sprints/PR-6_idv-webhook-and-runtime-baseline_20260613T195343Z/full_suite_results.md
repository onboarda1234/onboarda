# PR-6 Full Suite Results

## Local Full-Suite Attempt

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
1 failed, 5284 passed, 25 skipped in 197.06s (0:03:17)
```

Failure:

```text
tests/test_rmi_requests.py::test_request_documents_rejects_invalid_or_past_deadline
expected HTTP 400 for a past deadline, got HTTP 201
```

Assessment:

- This failure is outside PR-6 touched code.
- The test computes the past date with local `date.today()`.
- The server validates deadlines against `datetime.now(timezone.utc).date()`.
- The local run crossed midnight in `Asia/Dubai` while UTC was still the prior date, so the test generated a date that was not in the past from the server's UTC perspective.

Confirmation rerun:

```bash
cd arie-backend && TZ=UTC /opt/homebrew/bin/python3.11 -m pytest -q \
  tests/test_rmi_requests.py::test_request_documents_rejects_invalid_or_past_deadline
```

Result:

```text
1 passed in 1.09s
```

Conclusion:

- Local full suite did not fully pass.
- The lone failure is a pre-existing timezone-sensitive test artifact, not a PR-6 regression.
- GitHub CI on UTC runners is required as authoritative full-suite evidence before closure.

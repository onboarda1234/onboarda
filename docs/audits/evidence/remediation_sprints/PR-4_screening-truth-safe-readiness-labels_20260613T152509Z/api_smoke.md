# API Smoke

## Branch-Level Local Semantic Smoke

Command:

```bash
PYTHONPATH=arie-backend python3 - <<'PY'
from screening_state import build_screening_truth_summary
report = {
    "screened_at": "2026-06-13T00:00:00Z",
    "company_screening": {
        "found": True,
        "sanctions": {
            "matched": True,
            "results": [{"name": "Watchlist Hit", "is_sanctioned": True}],
            "source": "complyadvantage",
            "api_status": "live",
        },
    },
    "director_screenings": [],
    "ubo_screenings": [],
    "kyc_applicants": [],
}
summary = build_screening_truth_summary(report, {"company_name": "Unsafe Match Ltd"}, [])
print({k: summary.get(k) for k in (
    "canonical_state", "screening_terminal", "screening_provider_clear",
    "defensible_clear", "screening_gate_ready", "approval_ready",
    "approval_gate_ready", "approval_blocking", "approval_blocked_reasons",
    "has_uncleared_completed_match",
)})
PY
```

Result:

```text
{
  "canonical_state": "completed_match",
  "screening_terminal": true,
  "screening_provider_clear": false,
  "defensible_clear": false,
  "screening_gate_ready": false,
  "approval_ready": false,
  "approval_gate_ready": false,
  "approval_blocking": true,
  "approval_blocked_reasons": ["company_watchlist:live_terminal_match"],
  "has_uncleared_completed_match": true
}
```

Raw branch evidence:

```text
runtime_json/local_screening_truth_semantic_check.json
```

## Staging API Smoke

Pending merge and staging deployment.

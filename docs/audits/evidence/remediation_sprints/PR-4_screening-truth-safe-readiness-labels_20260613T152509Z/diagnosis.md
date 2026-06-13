# Diagnosis

## Scope

PR-4 targets `FSI-007 - Screening truth summary has unsafe approval-ready terminology`.

Base `origin/main` used for diagnosis:

```text
e61800bedc61752885313ab9e70718f6c4a021f3
```

Branch:

```text
codex/pr4-screening-truth-safe-readiness-labels
```

## Reproduction on current main

Current main still contained the unsafe screening readiness contract.

Backend reproduction:

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
    "canonical_state", "terminal", "screening_result", "defensible_clear",
    "approval_ready", "approval_blocking", "blocking_reasons",
    "has_uncleared_completed_match",
)})
PY
```

Observed result on current main before the fix:

```text
{
  "canonical_state": "completed_match",
  "terminal": true,
  "screening_result": "match",
  "defensible_clear": false,
  "approval_ready": true,
  "approval_blocking": true,
  "blocking_reasons": ["company_watchlist:live_terminal_match"],
  "has_uncleared_completed_match": true
}
```

This confirms the contradictory state: the screening truth summary could report `approval_ready=true` while also reporting `approval_blocking=true`.

## UI diagnosis

The back-office fallback summary in `arie-backoffice.html` had the same unsafe derivation:

```text
approval_ready: terminal && (canonicalState === 'completed_clear' || canonicalState === 'completed_match')
```

Case Command Centre and application approval blockers also consumed `!screeningTruth.approval_ready`, so a completed match could avoid the screening blocker if the backend summary or client fallback marked the completed match as approval-ready.

## Status

`FSI-007` exists on the diagnosed main SHA and requires code remediation.

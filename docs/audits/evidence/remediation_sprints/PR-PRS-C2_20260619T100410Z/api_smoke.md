# PR-PRS-C2 API Smoke (Staging)

## Deploy provenance (proven)

- Merge SHA: `cda0cc77967c052f0bab90081701123d4119cda5`
- Deploy run `27816794109` → `deploy` job `success`; image SHA-pinned to the merge SHA.
- In-workflow checks `Verify deployment health` and `Verify portal and backoffice`: both `success`.

## Live runtime probe (operator verification pending)

This session has read-only GitHub access and no authenticated staging path, so the
live `/api/version` probe + behavioural smoke must be run by an operator (or Codex)
against staging. Expected values and exact steps below.

### 1. Version match
```
GET https://staging.regmind.co/api/version   (authenticated)
```
Expected:
- `git_sha == "cda0cc77967c052f0bab90081701123d4119cda5"`
- `image_tag == "cda0cc77967c052f0bab90081701123d4119cda5"`

### 2. Memo-gate quarantine (fail-closed)
1. Create + complete a periodic review with memo generation forced to fail
   (e.g. transient DB/template fault, or the test injection path).
2. Expect: review `status == "completion_pending_memo"`, `completed_at` null,
   completion response `memo_gate.quarantined == true`, a `generation_failed`
   memo row persisted, audit `periodic_review.memo_generation_quarantined`.
3. Confirm canonical application risk **was** elevated (if the outcome was a
   confirmed risk change) — elevate-now contract.
4. Confirm **no** next-cycle review row was scheduled — defer-cycle contract.

### 3. Recovery
```
POST https://staging.regmind.co/api/monitoring/reviews/<id>/memo/recover   (admin|sco)
```
Expect: `200`, `status == "completed"`, `memo_gate.finalized == true`,
`completed_at` set, a `generated` memo row, next-cycle review now scheduled,
audit `periodic_review.completion_finalized`.

### 4. Queue visibility
- While quarantined: review appears in the **active** review queue.
- After recovery: review appears as **terminal/completed** and is excluded from the active queue.

## Configuration knobs (staging)

| Env var | Default |
|---|---|
| `PERIODIC_REVIEW_MEMO_RECOVERY_ENABLED` | on for staging/prod |
| `PERIODIC_REVIEW_MEMO_RECOVERY_SWEEP_SECONDS` | `900` |
| `PERIODIC_REVIEW_MEMO_INLINE_ATTEMPTS` | `2` |
| `PERIODIC_REVIEW_MEMO_SWEEP_BATCH_SIZE` | `25` |
| `PERIODIC_REVIEW_MEMO_ALERT_THRESHOLD` | `3` |

All behavioural assertions in §2–§4 are already proven deterministically by the
automated suite (see `evidence_matrix.md` rows 3–11); the staging probe confirms
they hold against the deployed SHA-pinned image.

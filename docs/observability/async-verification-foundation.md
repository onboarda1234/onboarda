# Async Verification Foundation

PR6 adds the dark async-verification foundation behind `FF_ASYNC_VERIFY=false`.
The default synchronous verification path remains authoritative until PR7
explicitly flips the flag in staging.

## Invariant

- `documents.verification_status` and `documents.verification_results` remain
  the compatibility fields read by portal and Back Office.
- `verification_jobs` is a worker coordination table, not a replacement source
  of truth for document state.
- System-driven transitions use `actor_type=system` and include `job_id` and
  `worker_id` in the audit detail.
- Screening provider selection, Sumsub timing, and ComplyAdvantage activation
  are unchanged by this PR.

## SLA Contract

- Maximum pending age: 900 seconds.
- Maximum in-progress age: 1200 seconds.
- Stuck-job threshold: 1200 seconds.
- Retry backoff: 120 seconds.
- Maximum attempts: 3.
- Alert destination: saved CloudWatch query
  `verification_async_stuck_jobs.cwlogs`, routed to compliance operations.
- Manual recovery: inspect the provider/file failure, resolve the root cause,
  then requeue the failed job or rerun synchronous verification from Back
  Office while the async flag remains off.

## Sumsub / Mesh Hazard Note

This PR does not alter Sumsub applicant creation, Sumsub AML checks, screening
provider selection, ComplyAdvantage abstraction state, or downstream screening
workflow timing. If PR7 later enables async verification, soak validation must
confirm that any downstream logic expecting immediate document verification
completion still sees truthful `pending`/`in_progress` states and does not treat
queued jobs as approval evidence.

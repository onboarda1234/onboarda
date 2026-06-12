# KYC-1A - Sumsub IDV Back-Office Visibility

## Source-of-Truth Verification

| Item | Value |
| --- | --- |
| GitHub repo | `onboarda1234/onboarda` |
| Branch | `codex/kyc-1a-sumsub-idv-backoffice-visibility` |
| origin/main SHA at branch start | `7372ed71cbc90ca24793738c95704f7143d78bee` |
| Branch base SHA | `7372ed71cbc90ca24793738c95704f7143d78bee` |
| Local implementation commit | `7ec69e011f6546e2757e14f62b41abf98acdc1d8` |
| PR | `#459` - KYC-1A: Add Sumsub IDV back-office visibility |
| Deployed `/api/version` SHA | Pending post-merge staging validation |
| Staging matches final main SHA | Pending post-merge staging validation |
| Ignored local artifacts | Existing untracked audit reports, `tmp/`, and `arie-treasury-portal.html` were not used as source evidence or included in this change. |

## Executive Verdict

Local implementation verdict: **PASS WITH STAGING PENDING**.

KYC-1A now exposes officer-ready, person-scoped Sumsub identity verification status through back-office application detail and a focused read-only API endpoint. Sumsub status is displayed as individual identity verification only and is separated from AML, PEP, sanctions, adverse-media, company screening, and ongoing monitoring.

No approval gates, memo logic, supervisor logic, live provider calls, or real application data mutation were introduced.

## Design

Chosen source-of-truth strategy:
- Build a durable read-only projection from existing persisted sources: `sumsub_applicant_mappings`, `webhook_processed_events`, `audit_log`, `sumsub_unmatched_webhooks`, and legacy `prescreening_data`.
- No new table was added in KYC-1A because the current durable sources can support visibility without changing write architecture.
- Optional-table reads fail closed to `not_started` / unavailable-style visibility in legacy fixtures instead of failing the endpoint.
- Raw provider payloads are not exposed in the API or UI.

Person matching:
- Prefer application-scoped Sumsub applicant mapping rows.
- Match by person type and name, or by external user ID / persisted person key.
- Legacy `prescreening_data.sumsub_applicant_ids` is used only as a fallback and marked with `source_of_truth=prescreening_data`.
- Unmatched webhook rows are surfaced only in the admin/SCO summary.

Status rules:
- `approved` only from GREEN / approved evidence.
- `rejected` from RED evidence and rejection metadata where available.
- `pending` when review evidence exists but no final answer is available.
- `applicant_created` when applicant mapping exists but no final provider event exists.
- `not_started` when no applicant mapping exists for a person.
- `unmatched` for webhook events that cannot be linked to an application/person.
- Missing status never renders as approved.

## API Surface

Added:
- `GET /api/applications/:id/kyc/identity-verifications`

Also included for officer application detail responses:
- `sumsub_idv_statuses`

Access:
- `admin`, `sco`, `co`, `analyst`
- Client users do not receive the new officer visibility payload through application detail.

Provider fields:
- `provider=sumsub`
- `provider_label=Sumsub Identity Verification`
- `provider_scope=individual_kyc_identity_verification`

No live Sumsub status endpoint is called by the new read path.

## Back-Office UI

Added a dedicated panel:
- Title: `Individual Identity Verification`
- Provider label: `Sumsub Identity Verification`
- Scope: `individual_kyc_identity_verification`

Displayed per person:
- Name
- Role
- Verification status badge
- Review answer
- Masked applicant ID
- Applicant-created timestamp
- Webhook-received timestamp
- Evidence source
- Evidence-backed flag
- Rejection labels
- Warning/blocking flags

Removed:
- Legacy Sumsub webhook card from the AML/watchlist screening review panel.

## Files Changed

| File | Purpose |
| --- | --- |
| `arie-backend/sumsub_idv_status.py` | New read-only person-scoped Sumsub IDV projection. |
| `arie-backend/server.py` | Adds IDV projection to officer application detail and registers focused read-only endpoint. |
| `arie-backoffice.html` | Adds separate Individual Identity Verification panel and removes Sumsub IDV from screening review panel. |
| `arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py` | Adds focused projection, endpoint/static, and provider-label tests. |
| `arie-backend/tests/test_phase4_remediation.py` | Updates legacy test wording to reflect separated IDV visibility. |

## Validation

Commands run:

```text
python3 -m py_compile arie-backend/server.py arie-backend/sumsub_idv_status.py arie-backend/screening.py arie-backend/screening_state.py arie-backend/security_hardening.py arie-backend/base_handler.py arie-backend/rule_engine.py arie-backend/sumsub_client.py
```

Result: passed.

```text
pytest -q tests/test_kyc_1a_sumsub_idv_visibility.py tests/test_sumsub_hardening_pr14.py tests/test_sumsub_verification.py
```

Result: 68 passed.

```text
pytest -q tests/test_backoffice_monitoring_navigation_static.py tests/test_backoffice_ca_truthflow_static.py tests/test_phase4_remediation.py
```

Result: 37 passed.

```text
pytest -q tests/test_screening_state_priority_a.py tests/test_screening_queue_state_integrity.py tests/test_screening_queue.py tests/test_screening_review.py tests/test_screening_freshness.py tests/test_approval_gate.py
```

Result: 145 passed.

```text
pytest -q tests/test_api.py -k "sumsub or kyc or identity or verification"
```

Result: 9 passed, 128 deselected.

```text
pytest -q tests/test_api.py -k "screening or review or approval or sumsub or kyc"
```

Result: 31 passed, 106 deselected.

Product-source provider-label search:
- Excluded audit evidence, `tmp/`, and ignored local prototype artifacts.
- Result: no prohibited removed-provider or provider-responsibility label hits in product source touched by this sprint.

## Scope Controls

Confirmed:
- No live Sumsub call added.
- No live ComplyAdvantage call added.
- No real application data mutation added.
- No approval blocker added.
- No memo or supervisor enforcement added.
- No CA screening state logic changed.
- No raw provider payload is dumped to the officer UI.
- Applicant IDs are masked in officer UI/API output.

## Remaining Gaps

Pending post-merge:
- Open PR and merge after review.
- Deploy final main to staging.
- Confirm authenticated `/api/version` deployed SHA.
- Capture ECS task definition.
- Run authenticated staging API validation for the new endpoint.
- Run browser validation for the Individual Identity Verification panel.

Future sprints:
- KYC-1B: include Sumsub IDV in memo/supervisor visibility.
- KYC-1C: add approval gate enforcement if policy approves.

## Final Local Verdict

**PASS WITH STAGING PENDING**

The local branch implements KYC-1A visibility and passes focused regression tests. Final closure still requires merged-main deployment and authenticated staging validation.

# PR-PRS-QUEUE-LIST-LITE-PERF-1 — Queue list-lite serializer

**Scope:** Periodic Review Queue (`GET /api/monitoring/reviews`) load performance. Backend only. No lifecycle / blocker / memo / risk / screening logic changes. Phases 2–3 explicitly out of scope.

## Problem
The list endpoint called the **detail** serializer `_serialize_periodic_review_row` for **every** row, which fired ~5 DB queries per review (required items, application re-fetch, document requests, risk-reassessment snapshot, alerts) plus a `workspace`+`refine` pass — to render a ~10-column table. Cost ≈ **9×N + 4** queries.

## Root-cause insight (why it's safe to skip)
The batched **projection** (`build_review_projection`) already runs the **same** `derive_operational_review_status` (server's `_derive_periodic_review_operational_status` is literally that function — `server.py:238` alias) with the **same** document signals (`missing_count` / `review_required_count` from `_periodic_review_document_request_status`). So the projection's `status_label` / `queue_status_label` / `is_blocked` are already the final values the detail serializer's `workspace`+`refine` recompute. **The per-row detail work is redundant for the queue.**

## Change
- `_serialize_periodic_review_row(..., lite=False)`: in lite mode, all projection-derived display fields still populate, but the per-row DB work (required items, application re-fetch, document requests, risk-reassessment snapshot, alerts) and the redundant `workspace`+`refine` are skipped.
- **Response contract preserved:** lite mode sets safe lightweight defaults for the detail fields the full serializer computes (`required_items`, `required_items_count`, `client_attestation`, `periodic_review_baseline`, `periodic_review_document_requests`, `periodic_review_document_request_count`, `risk_reassessment`, `open_document_issues_count`, `open_alerts_count`, `screening_status`) — reusing projection-computed counts where available — so **no key silently disappears** from the list response. Exact values for these live on the detail endpoint.
- `PeriodicReviewsListHandler` (the queue/list endpoint) calls it with `lite=True`.
- `PeriodicReviewDetailHandler` (detail) is unchanged — still uses the full serializer.

Files: `arie-backend/server.py` (lite path + handler), `arie-backend/tests/test_periodic_review_queue_list_lite.py` (new).

## Proof
**Parity (test, PASS):** `tests/test_periodic_review_queue_list_lite.py` asserts the lite serializer produces identical queue-displayed fields AND identical embedded-projection operational fields vs the full serializer, across states:
- in_progress + attestation submitted
- awaiting client attestation
- awaiting_information
- completed
- **with a missing mandatory document request** (exercises the document-signal label path in both the projection and the full serializer's workspace) → still identical.

**Performance — serializer (test, PASS):** the lite serializer issues **0** per-row DB queries (projection supplied); the full serializer issues ≥3 per row.

**Performance — endpoint (test, PASS):** `test_list_endpoint_does_no_per_row_detail_work` spies the per-row detail helpers (`_list_backoffice_periodic_review_document_requests`, `get_required_items`, `build_reassessment_snapshot`) during a real `GET /api/monitoring/reviews` with multiple rows and asserts **0 calls** — a regression guard at the handler level (not just the serializer). `test_detail_endpoint_still_does_full_per_row_work` asserts the detail endpoint **does** call them (>0), proving the skip is list-only.

**Contract (test, PASS):** `test_lite_preserves_list_response_contract_keys` asserts the lite response contains every key in the list contract set (no silent key drop), and that the contract set is real (full serializer sets them too).

**Regression (PASS):** full periodic-review suite — **348 passed, 1 skipped**; plus the reworked list-lite file (**9 passed**) and the endpoint-consuming suites (handlers/workspace/queue-hygiene/phase1 — **87 passed**). Detail endpoint behaviour unchanged.

> Note: this removes the serializer's ~5 queries/row. The projection's own per-row work (`build_review_projection`) remains and is the **Phase 2** target; the endpoint query count therefore still scales with N until Phase 2, but the detail over-fetch is gone.

## Acceptance criteria
| Test | Result |
|---|---|
| Queue list endpoint returns same displayed fields | ✅ parity test |
| Full detail endpoint still returns full detail | ✅ detail path untouched + suite |
| Active / Completed / All filters work | ✅ unchanged (R2 + queue-hygiene tests) + browser smoke |
| Pending Memo row still shows correctly | ✅ parity (queue_status/status from projection) + browser smoke |
| Stale memo flag still available | ✅ memo GET endpoint unchanged (not a list field) |
| Query count materially reduced | ✅ query-count test (0 per-row in lite vs ≥3 full) |
| Load time improved on seeded N reviews | ⏳ browser smoke (staging) |
| No console errors | ⏳ browser smoke (staging) |

## Not included (parked per scope)
Phase 2 (batch projection blocker/readiness, pagination, indexes), Phase 3 (FE parallelize), persisted screening summary, projection rewrite, queue UX. The projection's own per-row work remains and is the Phase 2 target if needed at high N.

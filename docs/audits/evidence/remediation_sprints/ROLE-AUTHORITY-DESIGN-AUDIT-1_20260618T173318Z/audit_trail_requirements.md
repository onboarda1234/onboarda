# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Audit Trail Requirements (code-grounded)

**Question answered:** *Can the full authority lifecycle (who submitted, who approved/rejected, who did
the second review, who overrode/waived, and every blocked attempt) be reconstructed from the audit log?*

**Answer:** the **happy path is strongly reconstructable**; the **principal gaps are blocked/denied
attempts and override/waiver event vocabulary.**

## 1. Audit infrastructure (today)

### `audit_log` table (`db.py:805-815` Postgres / `1973-1983` SQLite)
Columns: `id`, `timestamp` (DB default), `user_id`, `user_name`, `user_role`, `action`, `target`,
`detail` (free text / JSON), `ip_address`. Plus `before_state`, `after_state` added by **migration v2.18**
(`server.py:5117-5127`).
There is **no dedicated `risk_level` / `decision` / `reason` / `second_reviewer` column** — those facts
live inside the JSON `detail` / `after_state`.

### Helpers (`base_handler.py`)
- `log_audit(user, action, target, detail, before_state, after_state, ...)` — `565-586`. Business-event rows.
- `log_governance_attempt(user, action, target, outcome, status_code, reason, payload_summary, ...)` — `594-664`. Writes `action = "Governance Attempt"`; the **real** action goes into `detail.action` with `outcome`, `response_code`, `rejection_reason` (≤512), `payload_summary`, `path`, `method`, `ts`. **Best-effort: write failure is swallowed** (`657-661`) — no fallback.
- `log_authz_denial(user, event, resource_id, extra, ...)` — `666-729`. Autonomous DB connection (survives caller rollback) + structured `AUDIT_FALLBACK` stderr fallback. Used by `require_backoffice_auth` (`511`) and client-ownership denial (`736`).

## 2. Canonical event vocabulary that exists today

### Governance-attempt actions (in `detail.action`) — accepted **and** rejected outcomes
| `detail.action` | trigger | file:line | blocked attempts audited? | reason captured? |
|---|---|---|---|:--:|
| `application.decision` | approve / reject / escalate_edd / request_documents | accept `25731`; rejects `25243…25579` (incl. role/memo/second-review/dual-control) | **Yes (all)** | Yes |
| `application.pre_approval_decision` | PRE_APPROVE/REJECT/REQUEST_INFO | accept `8882`; rejects `8706…8774` | Yes | Yes |
| `application.status_change` | PATCH status | rejects `6211`, `6263` (**second-review block only**) | **Partial** | Yes (that path) |
| `application.assignment` | reassign | reject `6351` | Yes (wrong-role) | Yes |
| `screening.review_disposition` | screening 1st/2nd review | accept `20986`; rejects `20640…20884` (incl. 403 second-review, 409 distinct-reviewer) | Yes | Yes |
| `edd.case_update` | EDD stage/closure | accept `29961`; rejects `29706` (incl. closure-role 403, same-officer 403) | Yes | Yes |

### Business-event actions (`log_audit`)
| `action` | trigger | file:line |
|---|---|---|
| `Decision` | final approve/reject/escalate (before/after, override fields, risk-at-decision) | `25692` |
| `First Approval (Pending Second)` | dual-control first approval (HIGH/VH) | `25538` |
| `Pre-Approval: {decision}` | pre-approval | `8875`, `8888` |
| `Status Change` | PATCH transition (before/after) | `6310` |
| `Screening Review` | screening disposition (four-eyes JSON, before/after) | `20955` |
| `EDD Update` | EDD stage change | `29871` |
| `EDD Closure (dual-control)` | terminal EDD close (assigned/senior/closed_by) | `29882` |
| `EDD Created` | EDD open | `29667` |
| `approval_blocked_screening_second_review_pending` | blocked approval helper | `23565` |
| `KYC Submitted` / `KYC Attestation Submitted` | client submit | `8634`, `8642` |
| `authz_denied_internal_api` | `require_backoffice_auth` denial | `base_handler.py:511` |
| `authz_denied_not_owner` | client accessing others' app | `base_handler.py:736` |

## 3. Lifecycle reconstruction — what works today
- **Approver / rejecter + risk at decision time**: `Decision` row `after_state` carries `risk_level`/`risk_score` (`server.py:25685-25686`). ✅
- **Dual-control approval**: first approver in `First Approval (Pending Second)` (`first_approver_id`); final approver is `decision_by` on `Decision` (`first_approver_id` echoed `25690`). Reconstructable by join. ✅
- **Screening second-review actor/timestamp**: distinct columns `second_reviewer_id`, `second_reviewer_name`, `second_reviewed_at` written (`20821-20825`) and surfaced (`20911-20912`, `four_eyes_status` `20950`). ✅
- **EDD dual-control closure**: `EDD Closure (dual-control)` captures assigned_officer, senior_reviewer, closed_by, decision_reason. ✅
- **Reassignment**: `Reassign` row with structured before/after + reason (per PR-APP-ROLE-AUDIT-GATES-1). ✅

## 4. GAPS (severity) — where the lifecycle CANNOT be reconstructed

- **[P0] Decorator-level role denials are NOT audited.** `require_auth(roles=[...])` returns a **silent 403** (`base_handler.py:493-496`). There are **132 `require_auth(roles=[...])` call sites in `server.py` and zero `log_authz_denial` calls**. So an `analyst` POSTing `/decision` (`25192`), or any wrong-role hit on an endpoint whose check is only at the decorator, produces **no audit row**. Only handlers that re-check role **inside** the body (`/decision` override `25317`, co-HIGH `25353`, assignment `6346`, EDD closure `29817`, screening second-review `20787`) capture the blocked attempt. *Blocked-attempt coverage is therefore partial.*
- **[P1] PATCH `/applications/:id` drops governance audit on most block branches.** Block returns at `6122` (invalid transition), `6133` (HIGH-not-pre-approved), `6174` (HIGH-not-reviewed), `6186`/`6193` (screening), `6199` (no memo), `6092` (client) do `db.close(); return self.error(...)` with **no `log_governance_attempt`**. Only the second-review block (`6211`,`6263`) and assignment (`6351`) are audited.
- **[P2] No distinct `override_used` event.** Override is discoverable only by JSON-scanning `Decision` rows for `override_ai=true` (`25606`); there is no first-class action string to filter on.
- **[P2] Waiver act has no audit event.** Waiver state (`waived_by`/`waived_at`/`waiver_reason`) lives on `application_enhanced_requirements` (referenced `server.py:3550-3557`); **no `log_audit`/`log_governance_attempt` emits a waiver-specific action**.
- **[P2] `log_governance_attempt` failures are silently swallowed** (`base_handler.py:657-661`) — unlike `log_authz_denial`, no stderr fallback. "Blocked attempt audited" is best-effort, not guaranteed.

## 5. Required fields per authority event (target contract)
Every authority event (accept **and** reject) must capture:
`actor_id`, `actor_role`, `actor_type`, `application_ref`, `action`, `outcome` (accepted/blocked), `response_code`,
`from_status`, `to_status`, `risk_level_at_decision`, `risk_score_at_decision`, `reason`/`rejection_reason`,
`blocker_snapshot` (for blocked approvals and submissions), `source_surface`, `ip_address`, `timestamp`,
plus action-specific: `first_approver_id`/`second_approver_id` (dual-control), `first_reviewer_id`/`second_reviewer_id` (screening four-eyes), `override_used`+`override_basis` (override), `waiver_reason`+`requirement_id` (waiver), `is_privileged_admin_action` (admin high-risk).

## 6. Canonical event vocabulary to ADD (target)
- `application.submit_to_compliance` (governance) + `Submit to Compliance` (business event) — submitter, basis tags, discretionary-vs-mandatory flag, blocker snapshot.
- `application.decision_blocked` — first-class blocked-decision event from the single `can_decide` gate (covers blocked approve **and** reject), with structured blocker list. **Emitted by PR1, not deferred.**
- `application.override_used` / `application.waiver_used` — distinct, filterable override/waiver events.
- `authz_denied_role` — route **all** `require_auth(roles=[...])` denials through `log_authz_denial` (closes P0), as `require_backoffice_auth` already does.

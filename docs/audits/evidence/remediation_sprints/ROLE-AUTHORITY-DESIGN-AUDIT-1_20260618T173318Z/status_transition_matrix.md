# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Status Transition Matrix (code-grounded)

**Source:** `arie-backend/branding.py:95-113` (labels) and `arie-backend/server.py:6101-6118` (transition map).

## Statuses that exist today (`STATUS_LABELS`, `branding.py:95-113`)
`draft`, `submitted`, `prescreening_submitted`, `pricing_review`, `pricing_accepted`, `pre_approval_review`, `pre_approved`, `kyc_documents`, `kyc_submitted`, `compliance_review`, `in_review`, `under_review`, `edd_required`, `approved`, `rejected`, `rmi_sent`, `withdrawn`.

## Requested status existence check

| Status | Exists now? | Evidence |
|---|:--:|---|
| `pricing_review` | ✅ | `branding.py` + transition map |
| `pre_approval_review` | ✅ | high-risk pre-approval stage |
| `pre_approved` | ✅ | PRE_APPROVE writes `kyc_documents` though (see note) |
| `kyc_documents` / `kyc_submitted` | ✅ | KYC stages |
| `compliance_review` | ✅ | `branding.py:105` "Compliance Review in Progress"; transition node `server.py:6108,6111,6115` |
| `in_review` / `under_review` | ✅ | review states |
| `edd_required` | ✅ | EDD route/escalation |
| `rmi_sent` | ✅ | request-more-info |
| `approved` / `rejected` | ✅ | terminal (`rejected`→`draft` reopen allowed) |
| `withdrawn` | ⚠️ | has a **label** (`branding.py:112`) but **no entry** in the transition map (`6101-6118`) — set elsewhere or dead; verify |
| **`submitted_to_compliance`** | ❌ | **does not exist** — grep of `arie-backend/` returns zero matches |

## Authoritative transition map (`ApplicationDetailHandler.patch`, `server.py:6101-6118`)
```
draft                 → submitted, prescreening_submitted
prescreening_submitted→ pricing_review, pre_approval_review
pre_approval_review   → pre_approved, rejected, draft
pre_approved          → kyc_documents
pricing_review        → pricing_accepted
pricing_accepted      → kyc_documents, pre_approval_review
kyc_documents         → kyc_submitted, compliance_review
kyc_submitted         → compliance_review
submitted             → under_review, rejected
compliance_review     → in_review, edd_required, approved, rejected
in_review             → edd_required, approved, rejected
under_review          → edd_required, approved, rejected
edd_required          → under_review, in_review, approved, rejected
rmi_sent              → kyc_documents, kyc_submitted, compliance_review
approved              → (terminal)
rejected              → draft   (reopen)
```

## Which endpoint performs each authority-relevant transition

| Transition | Endpoint(s) | Authority notes |
|---|---|---|
| → `approved` | `ApplicationDetailHandler.patch` (`6296-6302`, bare `require_auth`) **and** `ApplicationDecisionHandler` `decision="approve"` (`25587`, write `25627`, admin/sco/co + co-HIGH block + dual-approval) | **two paths, unequal authority** → P0-1 |
| → `rejected` | PATCH (`6111-6114`) and `/decision` `reject` (`25587`); also `/pre-approval-decision` REJECT (`8815`) | reject is officer decision (admin/sco/co); pre-approval reject SCO/admin |
| → `edd_required` | PATCH (`6111-6114`, syncs `onboarding_lane='EDD'` `6289`) and `/decision` `escalate_edd` (`25590`) | escalation allowed to admin/sco/co |
| → `rmi_sent` | `/decision` `request_documents` (`25591`); exits gated by `_rmi_continuation_readiness` (`6141-6168`) | — |
| → `compliance_review` | PATCH from `kyc_documents`/`kyc_submitted`/`rmi_sent` (`6108,6109,6115`) | this is the current "in compliance" state |
| PRE_APPROVE | `/pre-approval-decision` → `kyc_documents` (`8787`) | SCO/admin; note label says `pre_approved` but write target is `kyc_documents` (P2 wording drift) |

## Authority is evaluated against CURRENT risk (target-compliant today)
- `/decision`: `approval_risk_level, approval_risk_score = _application_risk_snapshot(app)` (`25351`) where `app` is a freshly row-locked `SELECT ... FOR UPDATE` (`25213-25226`). `_application_risk_snapshot` (`317-342`) reads live `final_risk_level`/`risk_level`, refusing the stale HIGH/0 fallback.
- PATCH: `risk_level = (app.get("risk_level") or "").upper()` from the current row (`6130`).
- No stored "submission-time lane" drives authority; `onboarding_lane` is only a side-effect sync on EDD routing (`6289`).

## Design implications for `submitted_to_compliance`
1. **Add** `submitted_to_compliance` as the single source-of-truth handoff status (the SCO queue is a projection of it — decision #1). Recommended transition edges:
   - **in:** `compliance_review`, `in_review`, `under_review`, `kyc_submitted` (and discretionary from `compliance_review` for clean LOW/MED) → `submitted_to_compliance`.
   - **out:** `submitted_to_compliance` → `approved` / `rejected` / `edd_required` / `rmi_sent` (SCO/admin decision) and → back to `compliance_review`/`in_review` (return to officer).
2. `submitted_to_compliance` must **lock decision/authority + risk fields but keep document collection/prep open** (decision #7) — no hard freeze.
3. Submission must **not** be blocked by screening-second-review-pending / EDD-required / material-screening / high-risk (decision #3); those gate *final approval* only.
4. Portal label for `submitted_to_compliance` must map to neutral **"Under Review"** (decision #9; extend `getClientPortalStatusLabel` `arie-portal.html:11199`).
5. Resolve the `withdrawn` map gap and the `pre_approved` label-vs-write drift while touching the transition map.

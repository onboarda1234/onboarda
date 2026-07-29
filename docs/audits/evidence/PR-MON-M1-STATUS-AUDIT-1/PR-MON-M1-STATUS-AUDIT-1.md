# PR-MON-M1-STATUS-AUDIT-1 — Monitoring Alert Runtime Audit

Captured: `2026-07-29T09:38:54.249298+00:00`

Environment: `staging`

Deployed source SHA: `4b8b27690fbe5e639494f39607e3257bc0feef2d`

## Safety and methodology

This is a diagnostic, read-only inventory. The collector used
`transaction_read_only=true`; REPEATABLE READ; ROLLBACK only. Every SQL statement is statically restricted to
`SELECT`, `WITH`, or `SHOW`; the collector has no commit path. It did not update,
delete, backfill, route, resolve, or otherwise mutate any alert or linked object.

The evidence output excludes client email addresses, credentials, free-form
officer notes, raw provider payloads, and audit-log detail text. Application and
client names never leave the database query. Client labels are omitted, staff
are represented by pseudonymous ID/role, free-form source references and human
detector labels use HMAC-SHA-256 with a fresh per-run secret that is never
persisted, and provider payloads are reduced to identifier/scope fields.

## Statistics

| Metric | Count |
|---|---|
| Total alerts | 19 |
| Open (exact status) | 13 |
| Resolved (exact status) | 1 |
| Terminal (all terminal statuses) | 4 |
| Legacy status | 2 |
| Invalid status | 0 |
| Unknown status | 0 |
| Duplicate alerts | 0 |
| Orphaned alerts | 4 |
| Alerts with missing/broken/impossible linkage | 4 |
| Alerts with missing linkage | 4 |
| Alerts with broken/impossible linkage | 0 |
| Migration ready | 4 |
| Future migration candidate | 1 |
| Manual review required | 14 |

## Findings

* Orphaned alerts: **1, 2, 54, 55**.
* Legacy stored status: **1, 583**. An orphan
  classification takes precedence in the single primary classification, while
  `status_validity` preserves the legacy finding.
* Terminal status without `resolved_at`: **2, 581, 582, 585**.
* Alerts on non-fixture applications with test-like names requiring provenance
  confirmation: **603, 606, 608, 609, 610, 611**.
* Exact duplicate alert groups: **0**.
* Repeated source-reference groups: **1**.
* Alerts with multiple explicit workflow owners:
  **0**.
* Alerts with broken or impossible links:
  **0**.
* Alerts with missing required links: **4**.

## Complete alert inventory

| ID | Application | Client | Type | Severity | Current status | Classification | Owner | Migration disposition |
|---|---|---|---|---|---|---|---|---|
| 1 | — | — | Sanctions Match | Critical | escalated | Orphaned | Screening Review | Manual review required |
| 2 | — | — | Risk Drift | Low | dismissed | Orphaned | Manual | Manual review required |
| 54 | — | — | manual_pr290_schema_validation | medium | open | Orphaned | Manual | Manual review required |
| 55 | — | — | other | medium | open | Orphaned | Manual | Manual review required |
| 581 | RM-PILOT-004 | — | adverse_media | low | dismissed | Valid | Screening Review | Manual review required |
| 582 | RM-PILOT-005 | — | sanctions | low | resolved | Valid | Periodic Review | Manual review required |
| 583 | RM-PILOT-024 | — | sanctions | high | in_review | Legacy | EDD | Candidate for future migration |
| 584 | RM-PILOT-025 | — | adverse_media | medium | open | Valid | EDD | Manual review required |
| 585 | RM-PILOT-041 | — | adverse_media | low | dismissed | Valid | Periodic Review | Manual review required |
| 586 | ARF-QAFIX-006 | qafix-client | media | medium | open | Valid | Screening Review | Migration ready |
| 587 | ARF-QAFIX-006 | qafix-client | media | medium | open | Valid | Screening Review | Migration ready |
| 591 | ARF-QAFIX-007 | qafix-client | media | medium | open | Valid | Screening Review | Migration ready |
| 592 | ARF-QAFIX-007 | qafix-client | media | medium | open | Valid | Screening Review | Migration ready |
| 603 | ARF-2026-100432 | — | media | medium | open | Valid | Screening Review | Manual review required |
| 606 | ARF-2026-100432 | — | pep | medium | open | Valid | Screening Review | Manual review required |
| 608 | ARF-2026-100434 | 3c14dd0540c843fa | document_expiry_missing | medium | open | Valid | Documents | Manual review required |
| 609 | ARF-2026-100434 | 3c14dd0540c843fa | document_expiry_missing | medium | open | Valid | Documents | Manual review required |
| 610 | ARF-2026-100435 | 3c14dd0540c843fa | document_expiry_missing | medium | open | Valid | Documents | Manual review required |
| 611 | ARF-2026-100435 | 3c14dd0540c843fa | document_expiry_missing | medium | open | Valid | Documents | Manual review required |

The machine-readable inventory records created/updated dates, detection source,
sanitised source reference, assigned officer, resolution-evidence presence,
document/screening/EDD/periodic-review/change-request linkage, and sanitised
audit trail for every row.

## Distribution

### By alert type

| Alert type | Count |
|---|---|
| Risk Drift | 1 |
| Sanctions Match | 1 |
| adverse_media | 3 |
| document_expiry_missing | 4 |
| manual_pr290_schema_validation | 1 |
| media | 5 |
| other | 1 |
| pep | 1 |
| sanctions | 2 |

### By owner workflow

| Owner | Count |
|---|---|
| Documents | 4 |
| EDD | 2 |
| Manual | 3 |
| Periodic Review | 2 |
| Screening Review | 8 |

### By severity

| Severity | Count |
|---|---|
| critical | 1 |
| high | 1 |
| low | 4 |
| medium | 13 |

## Guardrail conclusion

Monitoring is not assigned as the owner of any row. The inferred owner is always
Documents, Screening Review, EDD, Change Management, Periodic Review, or Manual.
Runtime flag evidence: `ENABLE_AGENT1_REFRESH_VERIFICATION=OFF`, `ENABLE_DOCUMENT_RENEWAL_AUTOMATION=OFF`, `ENABLE_MONITORING_AUTO_RESOLUTION=OFF`, `ENABLE_MONITORING_SCREENING_CHANGE=OFF`. No Monitoring workflow or
feature flag was activated by this audit.

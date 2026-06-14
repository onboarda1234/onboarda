# PR-DOC1 Diagnosis - DOC-001

## Scope

- Remediation ID: DOC-001
- PR: PR-DOC1 - KYC, Memo and Approval Document Verification Gates
- Branch: `codex/pr-doc1-kyc-memo-approval-document-verification-gates`
- Current `origin/main` SHA diagnosed: `6b6ea16881ae7f93a0eeb4256bb4f205692be757`
- Diagnosis source: latest `origin/main`, not prior reports or screenshots.

## Re-diagnosis Result

DOC-001 existed on latest `origin/main`.

Required onboarding/KYC documents could be relied on without verified, current, Agent 1-backed verification evidence:

- Upload created a `documents` row with `verification_status='pending'` and empty verification evidence.
- `/api/documents/:id/verify` was the only explicit Agent 1 trigger; upload did not enqueue verification.
- KYC submit checked document presence/status narrowly and did not require complete verification results, `verified_at`, current document state, or Agent 1 execution proof.
- Memo generation could read active documents and generate approval-reliance memos without a hard document evidence gate.
- Memo validation and approval did not fail closed on pending, failed, skipped, stale, superseded, unsupported, or missing-proof document evidence.
- Application approval blocked flagged documents but did not block all non-reliance states.
- Agent-disabled/skipped verification could return skipped without persisting a downstream-blocking document state.
- UI readiness could treat uploaded/status-only documents as complete enough for readiness presentation.

## Reproduced Behavior Matrix

| Scenario | Diagnosed branch-main behavior | PR-DOC1 behavior |
| --- | --- | --- |
| Pending document | Could satisfy presence-based KYC paths and was not consistently blocked by memo/approval reliance gates. | Blocks KYC submit, memo generation, memo approval, and application approval with document-specific blockers. |
| Failed document | Not uniformly blocked downstream except in limited review/status flows. | Blocks all reliance gates. |
| Skipped verification | Skipped Agent 1 path did not persist a blocking document state. | Persists `verification_status='skipped'`, audit, Agent execution, and blocks reliance unless governed manual acceptance exists. |
| Missing execution proof | Status-only verified documents could look clean. | Blocks where Agent 1 proof is required. |
| Stale verification | No shared stale policy gate for canonical KYC documents. | Blocks verified evidence older than the policy window. |
| Flagged document | Existing flagged behavior blocked in some approval paths. | Remains blocked unless controlled admin/SCO manual acceptance exists. |
| Manual fallback | Existing accepted review state was not enough governance for reliance. | Requires admin/SCO role, reason, actor, timestamp, and auditable acceptance fields. |

## Inspected Areas

- Application document upload and versioning.
- `/api/documents/:id/verify`.
- `documents.verification_status`, `verification_results`, `verified_at`.
- `agent_executions` linkage for Agent 1 document verification.
- KYC submit handler.
- Compliance memo generation, validation, and approval handlers.
- Application approval validator and decision handler.
- Application list/detail/evidence-pack serializers.
- Portal document verification status rendering.
- Back-office document readiness and approval blocker rendering.


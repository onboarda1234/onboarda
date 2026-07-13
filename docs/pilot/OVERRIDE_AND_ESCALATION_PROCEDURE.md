# Override and Escalation Procedure

**Use with:** `docs/pilot/COMPLIANCE_OFFICER_SOP.md`
**Version:** Draft v0.2

An override is an accountable human exception within an authorized control path. It is not a way around a backend gate, missing evidence, or a regulatory prohibition.

## 1. When an override may be considered

An override may be considered only when all of the following are true:

- the actor is an authorized role for the action;
- the backend explicitly permits the override path;
- the case and evidence have been reviewed;
- the reason is specific, contemporaneous, and documented;
- required senior review or four-eyes control is complete;
- the override does not contradict a non-overridable hard blocker;
- the resulting decision remains defensible from the underlying evidence.

The officer must record what was overridden, why the normal path was insufficient, what evidence was reviewed, who authorized the exception, and what follow-up remains.

## 2. Overrides that are prohibited

Do not override or manually bypass:

- an unresolved confirmed sanctions hit;
- a failed required IDV check without an approved exception process;
- missing required documents where the gate requires them;
- stale or quarantined risk or memo provenance;
- incomplete or unavailable screening;
- a backend-denied action;
- a missing audit, decision, or sign-off record;
- an officer's inability to evidence the rationale;
- a provider-mode restriction or controlled-pilot boundary;
- a production-only or enterprise workflow that management has not enabled.

Do not use direct database changes, alternate endpoints, browser manipulation, or another user's credentials to create an apparent override.

## 3. Senior escalation triggers

Escalate to an SCO or management-designated senior reviewer when a case involves:

- high or very high risk;
- PEP, sanctions, or adverse-media matching;
- complex, opaque, nominee, or rapidly changing ownership;
- a material change identified during Periodic Review;
- an `INCONSISTENT` supervisor verdict;
- contradictory memo, risk, screening, IDV, or document evidence;
- suspicious-activity indicators or uncertainty about the appropriate route;
- a hard blocker that appears incorrect or cannot be resolved;
- a proposed override or exception;
- an action by a non-owner officer;
- an incident affecting evidence integrity, auditability, provider mode, or data handling.

Escalate before taking a terminal decision. A pending escalation must remain pending unless the authorized reviewer records a decision.

## 4. Handling an `INCONSISTENT` supervisor verdict

Follow this procedure:

1. Do not rely on the supervisor result alone and do not treat a favorable sentence as a clearance.
2. Read the deterministic approval blockers and confirm which are authoritative.
3. Review the underlying corporate, ownership, identity, document, screening, and client-provided evidence.
4. Compare the memo, risk output/provenance, screening status, IDV status, document status, and supervisor details.
5. Identify the exact contradiction, missing input, stale result, or interpretation difference.
6. Escalate to an SCO when the contradiction is unresolved, material, or affects approval authority.
7. Record the final human rationale, reviewer, evidence, and follow-up action.
8. Do not approve solely because the AI or supervisor output appears favorable.

If the inconsistency is caused by missing or stale data, resolve the data/recomputation path first. Do not relabel the result or delete the evidence to make the verdict appear consistent.

## 5. Escalation workflow

### Officer

- Stop the affected action.
- Capture the application ID/ref, current status, evidence, and exact issue.
- Create or update the approved escalation record.
- Notify the named senior reviewer.
- Keep the case pending until an authorized outcome is recorded.

### Senior Compliance Officer

- Confirm the officer's authority and the scope of the escalation.
- Review the source evidence and deterministic gates.
- Decide whether to resolve, request more information, reject, or approve through the permitted path.
- Record the rationale, limitations, and required follow-up.
- Confirm the audit/activity and decision records are complete.

### Admin or management

- Resolve access, configuration, storage, or environment issues through the approved support path.
- Do not decide the compliance outcome solely because the issue is technical.
- Preserve the original evidence and incident record.

## 6. Required escalation record

Record all of the following:

- application ID and reference;
- person escalating and their role;
- named officer of record;
- date and time in UTC;
- exact trigger and question for the senior reviewer;
- evidence, memo, risk, screening, IDV, and document records reviewed;
- deterministic blockers and their status;
- requested action or decision;
- senior reviewer and role;
- final decision and rationale;
- follow-up owner and due date;
- link/reference to the audit, activity, or incident record.

## 7. Decision standard after escalation

The senior reviewer must be able to explain the outcome from the underlying evidence without relying on an AI summary. If the evidence remains incomplete, contradictory, stale, or outside the approved pilot scope, hold or reject the case and record why.

## 8. Pilot limitation

This procedure does not authorize SAR/STR, live provider, GDPR erasure, production notification, or other excluded workflows. Those require separate management approval and an approved operating procedure.

## Controlled Document

- **Version:** Draft v0.2
- **Approval date:** ____________________
- **Owner:** ____________________
- **Repository reference:** `docs/pilot/OVERRIDE_AND_ESCALATION_PROCEDURE.md`

The Markdown file in the repository is the controlled master. Word and PDF exports are review copies unless they are separately version-controlled and approved. Every distributed copy must retain its version, approval date, owner, and repository reference to prevent document drift.

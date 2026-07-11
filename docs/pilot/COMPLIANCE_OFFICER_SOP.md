# Compliance Officer SOP - RegMind Controlled Pilot

**Document:** P13-7
**Status:** Draft for pilot review
**Audience:** Compliance Officers, Senior Compliance Officers, Admins, Analysts, and pilot management
**Operating environment:** Approved RegMind controlled-pilot scope only

## 1. Purpose and scope

This SOP defines the minimum operating discipline for compliance officers using RegMind during a controlled pilot. It covers application review, risk and screening interpretation, memo review, escalation, evidence handling, and exception recording.

RegMind supports compliance decisioning; it does not replace compliance officer judgment. The officer of record remains accountable for the quality, rationale, and authorization of every decision. A clean user interface, favorable AI output, or a successful API response is not by itself approval evidence.

This document is an operating control, not a production-readiness statement. It applies only to the pilot users, applications, providers, workflows, and environment explicitly approved by management.

## 2. Pilot access and onboarding

Before receiving access, each officer must:

- be named in the approved pilot user list;
- complete the current compliance, data-handling, and RegMind operating briefing;
- understand their assigned role and decision authority;
- understand the escalation route and the Senior Compliance Officer contact;
- acknowledge the prohibited-actions list in Section 12;
- confirm that staging, sandbox, fixture, and pilot evidence must not be presented as production evidence.

Pilot management must maintain:

- the approved officer and admin list;
- the approved client/application scope;
- the provider mode and provider-call boundaries;
- the monitoring scope, including any explicitly excluded features;
- the named owner for each pilot application;
- the support and incident escalation contact.

Do not share credentials, use another officer's account, or use the system for an unapproved case. Analysts may prepare and review information within their role but must not perform actions reserved for an authorized decision-maker.

## 3. Roles and responsibilities

| Role | Responsibilities | Decision boundary |
|---|---|---|
| Compliance Officer (CO) | Review assigned cases, evidence, risk, screening, memos, blockers, and requests for information; record rationale; escalate uncertainty | Follow role/risk authority and backend gates; do not approve cases outside authority |
| Senior Compliance Officer (SCO) | Review escalations, high or very high risk, complex ownership, PEP/sanctions/adverse-media concerns, overrides, and inconsistent verdicts | Provide senior review where required; document the rationale and outcome |
| Admin | Manage approved pilot access and operational configuration; support controlled recovery and audit access | Do not use administrative access to bypass a compliance gate or conceal an exception |
| Analyst | Gather, compare, and document evidence; identify gaps and prepare work for an authorized officer | No terminal approval, rejection, implementation, or override unless separately authorized and enforced |
| Client user | Provide accurate information, documents, attestations, and responses to requests | Cannot approve, override, alter audit evidence, or access officer-only information |

The named officer of record is the accountable owner for a pilot case. A different officer may act only when authorized and the exception is recorded with the reason, actor, reviewer, and timestamp.

## 4. Daily officer workflow

At the start of each operating day:

1. Confirm the pilot environment, user identity, approved scope, and current operating notices.
2. Check the assigned queue and identify overdue, escalated, high-risk, and blocked cases.
3. Review new applications for completeness and correct ownership.
4. Review blockers, requests for information, document requirements, and client responses.
5. Review current risk output and its provenance. Treat stale or quarantined risk as a stop condition.
6. Review screening and adverse-media results, including the provider mode and whether a result is sandbox or fixture-assisted.
7. Review IDV and document-verification status, exceptions, and reviewer comments.
8. Review the compliance memo and supervisor verdict. Compare them with the underlying evidence.
9. Record exceptions, uncertainty, and required follow-up in the approved audit or case record.
10. Escalate cases meeting Section 8 triggers before taking a terminal decision.
11. Complete the applicable checklist in `PILOT_REVIEW_CHECKLIST.md`.
12. Record the decision only after required blockers and review obligations are satisfied.

At the end of the day, reconcile open escalations, unanswered information requests, and cases awaiting another officer. Do not leave an action appearing complete when its evidence or audit record is incomplete.

## 5. Human accountability and system authority

- AI, Claude, agent, and supervisor output is advisory.
- Deterministic backend approval and authorization gates are authoritative.
- Officers must not approve when required blockers remain, even if an AI summary appears favorable.
- Officers must compare AI output with source documents, screening evidence, IDV results, risk output, memo validation, and the activity/audit record.
- An override or escalation requires a clear human rationale, the evidence reviewed, the authorized actor, and the timestamp.
- A backend-denied action must not be recreated manually through another interface or database path.
- If the officer cannot explain why a case meets the applicable standard, the case must be escalated or left pending.

## 6. Application review

Use `PILOT_REVIEW_CHECKLIST.md` for the detailed review. At minimum, confirm:

- the company identity, registration information, country, sector, entity type, and ownership information are coherent;
- required corporate documents and person-level documents are present and attributable;
- directors, UBOs, controllers, and intermediaries have been reviewed;
- screening, IDV, document verification, risk, memo, RMI, and audit status are current;
- all critical blockers are understood and resolved or formally escalated;
- the selected route and proposed status match the evidence;
- the application has a named owner and any non-owner action is documented.

Do not infer a clean result from a missing result. Missing, stale, failed, or unavailable evidence is a reason to stop and resolve or escalate.

## 7. Pre-approval and final decisions

Before pre-approval, verify the risk and memo are current, screening and IDV are complete for the route, required evidence is available, and any four-eyes or senior review obligation is satisfied.

Before final approval, rejection, or other terminal decision:

- read the final evidence, not only the summary;
- confirm the status and route are consistent with the evidence;
- confirm no stale risk, stale memo, unresolved RMI, or unresolved screening issue remains;
- confirm the backend gate permits the action;
- record the rationale and reviewer identity;
- verify the audit/activity record is present after the action.

For rejection, record a specific evidence-based reason and consider the approved client communication process. Never use rejection as a workaround for an unresolved system or data issue without documenting the issue and escalation.

## 8. Override and senior escalation

Use `OVERRIDE_AND_ESCALATION_PROCEDURE.md`. Escalate before acting when the case involves:

- high or very high risk;
- PEP, sanctions, or adverse-media concerns;
- complex, opaque, or changing ownership;
- a material periodic-review change;
- an `INCONSISTENT` supervisor verdict;
- suspicious-activity indicators;
- a hard blocker that appears incorrect;
- uncertainty about authority, evidence, or the appropriate route.

An escalation is not approval. The case remains pending until the authorized reviewer records the outcome.

## 9. Memo and supervisor procedure

Review the memo for source completeness, current risk and screening context, document reliance, contradictions, missing evidence, and clear decision rationale. Review validation and supervisor status, including warnings and inconsistencies.

For an `INCONSISTENT` supervisor verdict, do not approve from the supervisor output. Follow the seven-step procedure in `OVERRIDE_AND_ESCALATION_PROCEDURE.md`, reconcile the underlying evidence, and obtain senior review where the inconsistency is unresolved.

Memo approval is a human compliance action. A memo that is generated, validated, or visually complete is not necessarily approved or sufficient for a final application decision.

## 10. Evidence export

Use `EVIDENCE_EXPORT_PROCEDURE.md` before relying on an evidence pack for approval, senior review, QA, an audit request, or a pilot record. Treat every export as a timestamped snapshot. Confirm its application identity, scope, completeness, and known limitations before storing or sharing it.

## 11. Periodic Review

For each periodic review:

1. Confirm the review belongs to the correct application and named owner.
2. Review the client attestation and all reported changes.
3. Compare material changes in ownership, directors, UBOs, business activity, geography, products, and risk factors.
4. Check requested and received evidence, document verification, screening, and risk reassessment.
5. Review any memo addendum, supervisor result, RMI, and open blockers.
6. Escalate material changes, uncertainty, or inconsistent evidence.
7. Close the review only when required evidence and controls are complete.
8. Confirm the next review date/cadence and record the completion rationale.

## 12. Monitoring and alerts

Monitoring and alert handling is in pilot scope only where management has explicitly enabled and documented it. If it is excluded, officers must not represent the screen, alert, or queue as an active pilot control.

Where enabled, review alert ownership, source, severity, evidence, linked application, status, and follow-up. Do not dismiss, clear, or close an alert without the required rationale and authorization. Escalate suspected sanctions, PEP, adverse media, suspicious activity, or data-integrity issues. Follow the separate monitoring procedure when one is approved.

## 13. Incident and exception logging

Record an incident or exception when:

- a provider, storage, readiness, or database dependency is unavailable;
- a risk, memo, screening, IDV, or document result is stale, missing, contradictory, or unexpected;
- a backend gate blocks an action unexpectedly;
- an audit/evidence record is missing or appears inconsistent;
- a client reports an incorrect or unsafe result;
- an officer acts outside the normal ownership or escalation path;
- a prohibited action is attempted.

Record the time, application ID/ref, actor, role, observed behavior, evidence reviewed, immediate containment, escalation recipient, and follow-up owner. Do not edit or delete the original audit/evidence record to make the exception disappear.

## 14. Prohibited actions during the controlled pilot

Officers must not:

- use RegMind for clients or applications outside the approved pilot scope;
- trigger real provider production checks or uncontrolled provider calls;
- send real emails, notifications, or client chases unless management has approved the action;
- approve a case with unresolved hard blockers;
- rely solely on AI, Claude, agent, or supervisor output;
- bypass a backend-denied action through another route or direct database access;
- delete, rewrite, or alter audit or evidence records;
- use SAR/STR workflows unless management has explicitly enabled them;
- run live GDPR erasure or deletion workflows unless explicitly enabled and approved;
- treat staging, fixture, sandbox, or pilot validation as production readiness;
- use demo/test credentials or fixture records as evidence for a real applicant.

## 15. Pilot limitations

The controlled pilot is bounded by the approved environment, users, application population, provider modes, monitoring scope, and operating procedures. ComplyAdvantage provider use remains sandbox or otherwise explicitly attested within the approved scope. Provider callbacks, AI outputs, storage probes, and fixture-assisted evidence must not be described as production-grade evidence.

Risk scoring remains subject to the approved risk-model governance and founder/compliance review. Monitoring, SAR/STR, GDPR erasure, real email/chase actions, and other enterprise workflows are excluded unless management explicitly enables them with a documented procedure.

Nothing in this SOP establishes production readiness, broad customer authorization, or regulatory approval.

## 16. Pilot readiness sign-off

Before pilot use, management should confirm:

- [ ] Approved pilot users are listed and trained.
- [ ] Approved pilot clients/applications are defined.
- [ ] Provider scope and sandbox/prohibition rules are confirmed.
- [ ] Monitoring scope is confirmed or explicitly excluded.
- [ ] Risk scoring model status and review owner are documented.
- [ ] This SOP and the related checklists are reviewed.
- [ ] Escalation and support contacts are assigned.
- [ ] Evidence export procedure is understood.
- [ ] Known limitations are accepted.
- [ ] Each pilot application has a named officer of record.

**Founder / management approval:** ____________________

**Compliance owner:** ____________________

**Date:** ____________________

**Scope approved:** ____________________

**Limitations accepted:** ____________________

# SOP — Marking Screening Matches (True Positive / False Positive)

_RegMind compliance operating procedure · ComplyAdvantage Mesh screening · v1 (2026-07-02)_
_Status: **Adopted** — MLRO signed off 2026-07-02._

## 1. Purpose

This procedure tells compliance officers how to review and dispose of screening
matches returned by ComplyAdvantage (CA) Mesh in the RegMind back office. It exists
so that dispositions are consistent, defensible to the regulator (FSC Mauritius), and
recorded with an audit trail.

## 2. Key principle — you decide at the **record (entity) level**

CA returns one or more **matched records** ("entities") for each screened subject.
Each matched record already carries its own set of **risk categories** (e.g. Sanctions,
PEP, Adverse media) determined by CA — **you do not select or confirm categories one
by one.**

> **Your decision is a single question about each matched record:
> "Is this matched record genuinely my customer / connected party — yes or no?"**

You are confirming **identity of the match**, not judging each risk category
separately. When you mark a record True Positive, every risk category CA attached to
that record is treated as confirmed for that customer. This is how the CA tool works
and how the risk score is calculated.

## 3. The four status values

| Status | Use when | Effect |
|--------|----------|--------|
| **Not reviewed** | You have not yet assessed the record | No score contribution; case cannot be finalised |
| **In review** | You are actively assessing / awaiting evidence | No disposition yet |
| **False positive** | The record is **not** your customer (name coincidence, wrong DOB/jurisdiction, etc.) | Cleared; excluded from risk score |
| **True positive** | The record **is** your customer / connected party | Confirmed; its risk categories feed the risk score |

**Only records explicitly marked True Positive contribute to the risk score.** A record
left "Not reviewed" contributes nothing — so never close a case with genuine matches
left un-reviewed.

## 4. How the risk level is calculated (so you understand the output)

- Each risk category on a confirmed record has a score (Sanctions/Terrorist
  financing/Proliferation financing = 100; Money laundering / PEP = 75; Regulatory &
  reputational / Fraud = 50).
- The scores of the categories on a True-Positive record are **summed**, then mapped to
  a band: **Low 0–49 · Medium 50–74 · High 75–99 · Prohibited 100+**.
- Because scores sum, a record carrying several serious categories escalates quickly —
  **this is intended.** A confirmed customer with multiple serious flags *should* rate
  higher than one with a single flag. The scheme deliberately errs conservative:
  over-flagging creates review work; under-flagging risks clearing someone you should not.

> **A single Sanctions, Terrorist-financing, or Proliferation-financing confirmation is
> a hard stop → Prohibited.** These are never cleared on the strength of the score alone.

**Important:** CA's risk level is a **screening triage signal**. The customer's
authoritative KYC risk rating is produced by RegMind's own risk engine and the officer's
disposition — not by CA's number in isolation.

## 5. Step-by-step

1. Open the screening review for the subject in the RegMind back office.
2. For **each** matched record, assess whether it is genuinely your customer:
   - Compare name, date of birth, nationality/jurisdiction, and any identifiers.
   - For **adverse-media** records, **open and read the source** (article/source link
     where provided) before deciding — do not clear on the headline alone. If no source
     link is present, note that and seek corroborating evidence.
3. Set the status:
   - Not your customer → **False positive** (record why: e.g. "DOB mismatch, different
     nationality").
   - Genuinely your customer → **True positive**.
   - Unsure / awaiting evidence → **In review**.
4. Record a **rationale** for every disposition (minimum ~15 characters; state the
   evidence reviewed and why the decision is appropriate). Attach supporting evidence
   where available.
5. Do not leave genuine matches "Not reviewed" when finalising.

## 6. Sensitive clears require a second reviewer

Clearing a sensitive match (sanctions / watchlist / provider risk / adverse media) as a
**False positive** triggers **maker-checker**: a **distinct second officer** must confirm
the clearance before it is final. The first and second reviewer must not be the same
person. This control is enforced by the system and must not be bypassed.

## 7. Record-keeping

- Every disposition, rationale, reviewer identity, and timestamp is written to the audit
  trail automatically.
- The threshold and scoring configuration are documented change decisions; do not alter
  screening configuration or risk models without compliance sign-off.

## 8. Escalation

- Any confirmed Sanctions / TF / PF match → escalate immediately per the firm's
  sanctions-hit escalation policy; do not self-clear.
- Genuine uncertainty about identity → keep **In review** and seek further evidence
  rather than guessing.

---
_Owner: Compliance (MLRO). Review annually or on material change to the CA configuration._

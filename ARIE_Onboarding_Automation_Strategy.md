# ARIE Finance — AI-Powered Onboarding: Strategy & Implementation Guide

**Prepared for:** ARIE Finance Ltd (FSC Licensed PIS, Mauritius)
**Date:** March 2026
**Objective:** Achieve fastest-in-class corporate client onboarding through AI automation, compliance integrity, and a frictionless client experience.

---

## Overview: The Two-Phase Onboarding Flow

```
Phase 1: Pre-Screening        Phase 2: Full KYC Onboarding
─────────────────────         ──────────────────────────────
Client fills form       →     AI document verification
     ↓                              ↓
AI auto-screening       →     Adverse media / sanctions
     ↓                              ↓
Compliance review       →     Risk scoring & EDD (if needed)
     ↓                              ↓
Accept / Reject         →     Final approval → Account live
(Target: <4h)                 (Target: <24h)
```

**Competitive target:** Sub-48h total onboarding from application to live account.

---

## 1. Client Portal (What Was Built)

The portal delivered is a single-file HTML prototype demonstrating:

- **Pre-screening form** — all fields from your Account Application Form Corporate, including directors table, UBO table, estimated activity, and 3-year financial forecast
- **AI verification dashboard** — real-time status badges for sanctions screening, adverse media, PEP checks, company registry, country risk, and document checks
- **Document upload centre** — organised by category (Corporate Docs, Director/UBO Identity, Business Evidence), with AI verification indicators per document
- **Progress tracking** — step-based progress bar through the full onboarding journey
- **Status timeline** — shows each milestone from submission to account activation
- **Application tracking** — clients can look up status by reference number or email

For production, this prototype should be built as a full-stack web application (recommended stack below).

---

## 2. Recommended Technology Stack

### Frontend
- **Framework:** React (Next.js) — fast, SEO-friendly, great developer ecosystem
- **Styling:** Tailwind CSS — rapid UI development matching the dark fintech aesthetic
- **Component library:** Shadcn/UI or Radix UI — accessible, customisable
- **State management:** Zustand or React Query

### Backend / API
- **Framework:** Node.js (Express) or Python (FastAPI)
- **Database:** PostgreSQL — for structured KYC data, audit logs
- **Document storage:** AWS S3 or Azure Blob Storage with server-side encryption (AES-256)
- **Queue system:** Redis + BullMQ — for async AI processing jobs
- **Auth:** Auth0 or Supabase Auth — MFA mandatory, OAuth2, JWT tokens

### Infrastructure
- **Hosting:** AWS (ECS + RDS + S3) or Azure — both available in South Africa / EU regions
- **CDN:** CloudFront or Cloudflare — for fast global document uploads
- **Security:** WAF, DDoS protection, VPN for internal admin panel
- **Compliance:** ISO 27001, SOC 2 aligned infrastructure

---

## 3. AI & Automation Layer — Component by Component

### 3.1 AI Document Verification

**What to automate:**
- Authenticity checks (detect forgeries, tampered documents, metadata inconsistencies)
- OCR data extraction (name, DOB, document number, expiry from passport/ID)
- Cross-reference extracted data vs. what the client entered in the form
- Expiry date validation (utility bills ≤ 3 months, passports not expired)
- Quality check (ensure document is legible, all four corners visible, no glare)

**Recommended vendors:**
| Vendor | Best For | Notes |
|--------|----------|-------|
| **Onfido** | Passport/ID biometric verification | Industry leader, FSC-acceptable |
| **Jumio** | Document OCR + liveness check | Strong in financial services |
| **Stripe Identity** | Lightweight, fast integration | Good for lower-risk clients |
| **AWS Textract** | OCR on corporate documents (PDFs) | Cost-effective for non-ID docs |
| **Google Document AI** | Structured data extraction | Excellent for financial statements |

**For corporate documents specifically** (COI, M&A, financial statements):
- Use AWS Textract or Google Document AI for OCR
- Build custom classifiers (using OpenAI or Anthropic API) to validate document type, extract key fields, and flag inconsistencies

### 3.2 Automated AML/CFT Screening

**What to automate:**
- Sanctions screening: OFAC, UN, EU, HMT lists
- PEP (Politically Exposed Person) screening
- Adverse media screening (news, court records, regulatory actions)
- Country risk assessment (FATF lists, Transparency International CPI)
- Ongoing monitoring (re-screening on a schedule or when news alerts trigger)

**Recommended vendors:**
| Vendor | Coverage | Notes |
|--------|----------|-------|
| **Dow Jones Risk & Compliance** | Global, comprehensive | High quality, premium pricing |
| **LexisNexis (Acuity/WorldCompliance)** | As specified in your compliance manual | Already in your compliance framework |
| **ComplyAdvantage** | Real-time, AI-native | Excellent adverse media, API-first |
| **Refinitiv World-Check** | SWIFT-grade, global | Used by Tier 1 banks |
| **Ondato** | Mid-market, good API | Cost-effective European option |

**Integration approach:**
- Trigger screening immediately upon pre-screening form submission (before human review)
- Provide the compliance team with a pre-populated screening report alongside each application
- Flag matches as: Clear, Possible Match (human review), Confirmed Match (decline)
- For ongoing monitoring: run re-screening quarterly and on any transaction anomaly alert

### 3.3 Company Registry Verification

**What to automate:**
- Confirm entity exists and is in good standing
- Verify registration number, registered name, registered address, directors on record

**Data sources:**
- **Mauritius:** Companies and Insolvency Service portal (CBRIS)
- **UK companies:** Companies House API (free, real-time)
- **US companies:** State-level SOS APIs (varies)
- **Global:** Dun & Bradstreet, Bureau van Dijk (Orbis), OpenCorporates API
- **EU:** European Business Registry (BRIS network)

Build automated lookups that query these registries using the BRN/registration number provided in the pre-screening form, returning a confidence score.

### 3.4 AI-Powered Risk Scoring Engine

Build a rules-based + ML risk scoring engine that calculates a risk score (Low / Medium-Low / Medium / High) for each applicant based on:

**Inputs:**
- Country of incorporation & operation (FATF status, AML risk tier)
- Business sector (crypto, gaming, forex = higher inherent risk)
- Estimated transaction volumes
- Screening results (PEP, sanctions, adverse media)
- Ownership structure complexity (number of layers, offshore entities)
- Document quality scores from AI verification
- Licence status (regulated vs. unregulated)

**Output:**
- Risk tier → determines CDD vs. EDD pathway
- Auto-approve (Low risk, clean screening, straightforward structure)
- Auto-escalate (High risk or matched screening → human compliance review)
- Auto-decline (Confirmed sanctions hit, FATF high-risk jurisdiction without EDD support)

This engine dramatically reduces manual work by handling the obvious cases automatically, letting your compliance team focus only on edge cases.

### 3.5 Automated Communications

Use a workflow automation platform to trigger:

| Trigger | Action |
|---------|--------|
| Form submitted | Instant confirmation email with reference number |
| AI screening complete | Email to compliance team with summary report |
| Application approved | Welcome email to client with next steps link |
| Application rejected | Formal decline notice (compliant, non-specific) |
| Document missing / expired | Automated chase email with specific document request |
| Document verified by AI | Real-time portal status update |
| Outstanding document > 5 days | Escalation to relationship manager |

**Recommended tools:** SendGrid / AWS SES for email; Twilio for SMS/WhatsApp; n8n or Zapier for workflow automation

---

## 4. Full Onboarding Document Checklist (from Compliance Manual)

### Corporate Entity Documents
1. Certificate of Incorporation
2. Certificate of Registration
3. Memorandum & Articles of Association (Constitution)
4. Register of Directors (certified, current)
5. Register of Shareholders (showing all beneficial owners)
6. Latest Annual Report & Audited Financial Statements
7. Bank Reference Letter (on letterhead, from a reputable institution)
8. Regulatory licences held (if any)

### For Each Director & UBO (≥ 20% ownership)
9. Valid passport or government-issued photo ID (certified copy)
10. Proof of address — bank statement OR utility bill (≤ 3 months old, certified)

### Business Evidence
11. Evidence of business activity (contracts, invoices, website screenshots)
12. Source of funds documentation (bank statements, investment records)
13. AML/CFT policy (for regulated entities)
14. Partnership Agreement (if applicable)

### AI Automation for Each Document Type
- **Passport/ID:** Biometric + MRZ (machine-readable zone) verification via Onfido/Jumio
- **Proof of address:** AI date extraction → confirm ≤ 90 days old; address cross-reference
- **COI / M&A / Registry docs:** OCR extraction → cross-reference with company registry API
- **Financial statements:** AI extraction of key financial metrics (revenue, assets) for plausibility check
- **Bank reference letters:** NLP analysis of letterhead, signatory details, institution name

---

## 5. Compliance Automation — Keeping the Human in the Loop

The goal is not to remove compliance officers — it is to elevate their work. AI handles the 80% of routine, clear-cut cases. Humans focus on the 20% that are complex or ambiguous.

**Recommended workflow:**

```
Client submits pre-screening
         ↓
AI runs: sanctions, PEP, adverse media, company registry, risk score
         ↓
         ├─ All clear + Low/Med risk → AUTO-APPROVE pre-screening (notify client instantly)
         ├─ Possible match OR High risk → QUEUE for compliance officer review (SLA: 4h)
         └─ Confirmed sanction hit → AUTO-DECLINE (log, report as required)
                    ↓
Client proceeds to full KYC document upload
         ↓
AI verifies each document (authenticity, OCR, cross-reference, expiry)
         ↓
         ├─ All docs valid + risk score maintained → QUEUE for final sign-off (1-click for CO)
         ├─ Document issue detected → AUTO-REQUEST specific replacement from client
         └─ EDD required → Escalate to Senior CO with full dossier
                    ↓
Compliance officer one-click approval → Account activated
```

This can realistically achieve:
- **Best case (Low risk, clean):** 2–4 hours total
- **Average case:** 24 hours
- **Complex/EDD case:** 3–5 business days

---

## 6. Admin / Compliance Dashboard

Build an internal admin portal for your compliance team with:

- **Application pipeline view** — Kanban board (Pending AI / Awaiting Review / EDD / Approved / Rejected)
- **Client dossier** — all submitted data, documents, and AI check results in one view
- **One-click decision** — approve / request more info / reject, with mandatory note
- **Audit trail** — every action timestamped and logged (required by FSC/FIAML)
- **Screening history** — full record of all screening results and alerts
- **Risk dashboard** — aggregate risk metrics, client portfolio breakdown
- **Re-screening scheduler** — automated periodic re-screening with alert queue

---

## 7. Security & Data Protection

Given you are handling financial data and KYC documents, the following are non-negotiable:

- **Encryption at rest:** AES-256 for all stored documents and PII
- **Encryption in transit:** TLS 1.3 for all API calls
- **Access control:** Role-based access (RBAC) — compliance officers see only what they need
- **MFA:** Mandatory for all staff and client portal logins
- **Document watermarking:** Auto-watermark downloaded KYC documents with user ID and timestamp
- **Data retention:** Comply with FSC requirements (minimum 7 years for KYC records per FIAML)
- **Data protection:** Align with Mauritius Data Protection Act 2017 and GDPR (for EU clients)
- **Penetration testing:** Annual third-party pen test
- **Audit logging:** All actions on client data logged immutably

---

## 8. Vendor Shortlist & Budget Estimates

| Category | Recommended Vendor | Est. Monthly Cost |
|----------|-------------------|-------------------|
| ID Verification (biometric) | Onfido | $0.50–2.00 per check |
| AML/Sanctions/PEP | ComplyAdvantage | $500–2,000/mo |
| Adverse Media | ComplyAdvantage / Dow Jones | Included above / $1,000+/mo |
| Company Registry | OpenCorporates API | $200–500/mo |
| OCR (corporate docs) | AWS Textract | Pay-per-use ~$0.015/page |
| Email Comms | SendGrid | $15–80/mo |
| Workflow Automation | n8n (self-hosted) | ~$0 self-hosted |
| Portal Hosting | AWS / Vercel | $50–300/mo |
| Database | AWS RDS PostgreSQL | $100–300/mo |
| Document Storage | AWS S3 | $23/TB/mo |

**Total estimated stack cost:** ~$2,000–5,000/month at launch scale, scaling with volume.

---

## 9. Implementation Roadmap

### Phase 1 — MVP Portal (6–8 weeks)
- Deploy client pre-screening form (online version of the HTML prototype)
- Manual compliance team review with digital workflow (email-based initially)
- Integrate one AML screening vendor (ComplyAdvantage recommended)
- Basic document upload + secure storage

### Phase 2 — AI Integration (8–12 weeks)
- Integrate ID verification (Onfido or Jumio)
- Integrate company registry lookups (OpenCorporates)
- Build AI risk scoring engine (rules-based first, ML later)
- Launch compliance officer admin dashboard
- Automated email workflows

### Phase 3 — Full Automation (12–20 weeks)
- Deploy AI document verification (OCR, cross-reference, authenticity)
- Adverse media monitoring (ongoing, not just at onboarding)
- One-click approval workflow for compliance officers
- Client-facing status portal with live updates
- Audit trail and reporting module

### Phase 4 — Optimisation (ongoing)
- ML model training on decision history for risk scoring
- A/B test form UX to maximise completion rates
- Integrate with your core banking / payment platform
- Expand to individual client onboarding if applicable

---

## 10. Key Metrics to Track

| Metric | Target |
|--------|--------|
| Average pre-screening to decision | < 4 hours |
| Average full onboarding time | < 48 hours |
| Document rejection rate (client error) | < 15% |
| AI auto-approval rate (low risk) | > 60% |
| False positive screening rate | < 5% |
| Client portal completion rate | > 80% |
| Cost per onboarded client | Reduce by 60% vs. manual |

---

## Summary

ARIE Finance has the opportunity to be genuinely the fastest and most frictionless PIS onboarding experience in the Mauritius/African fintech market. The combination of:

1. A beautifully designed, self-service client portal
2. AI-powered document verification and AML screening
3. Automated risk scoring routing
4. A one-click compliance officer decision workflow

...can realistically take you from the industry average of 2–3 weeks manual onboarding to a **24–48 hour** experience for most clients — a significant competitive differentiator.

The delivered HTML prototype demonstrates the full client-facing journey and can be used for stakeholder presentations, investor demos, or as a design blueprint for your development team.

---

*Document prepared based on ARIE Finance's Business Plan, Compliance Manual (May 2025), and Account Application Form for Corporate Clients.*

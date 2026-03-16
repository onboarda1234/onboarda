# ARIE Finance RegTech Platform: Technical Dossier

**Document Version:** 1.0
**Last Updated:** 2026-03-15
**Audience:** RegTech Architects, System Auditors, Enterprise Integration Teams
**Classification:** Technical Reference

---

## 1. Architecture Overview

### 1.1 Infrastructure Foundation

The ARIE Finance platform is built on a single-instance Python/Tornado asynchronous web server architecture optimized for high-throughput API processing and concurrent user sessions. The core application server runs on **port 8080** with optional reverse proxy (Nginx/Caddy) for production TLS termination.

**Server Technology Stack:**
- **Runtime:** Python 3.8+
- **Web Framework:** Tornado (async, non-blocking I/O)
- **Entry Point:** server.py (~4100 lines, modular handler architecture)
- **Application Pattern:** Stateless REST API + static HTML frontends
- **Process Manager:** Optional supervisor framework for production deployments
- **Metrics:** Prometheus endpoint at `/metrics`
- **Logging:** JSON structured logging (JSONFormatter class)

### 1.2 Modular Handler Architecture

Server.py implements a modular handler pattern with specialized Tornado RequestHandler subclasses organized by functional domain:

```
AuthHandler (login, registration, token validation)
ApplicationHandler (CRUD operations on applications)
ScreeningHandler (API orchestration for external screening)
DocumentHandler (file upload, validation, verification)
KYCHandler (Sumsub identity verification orchestration)
SARHandler (Suspicious Activity Report workflow)
MonitoringHandler (ongoing compliance checks)
AdminConfigHandler (risk model, agent configuration)
AuditHandler (compliance audit trail)
DashboardHandler (analytics and reporting)
```

Each handler enforces JWT authentication, role-based access control, and input sanitization before processing requests.

### 1.3 Database Architecture

**Development Environment:**
- SQLite with Write-Ahead Logging (WAL) mode enabled
- File path: `./data/arie.db` (auto-created on startup)
- Configuration: `PRAGMA journal_mode=WAL` for concurrent read optimization
- Default DB_PATH environment variable: `data/arie.db`

**Production Environment:**
- PostgreSQL 12+ required
- Connection via DATABASE_URL environment variable (format: `postgresql://user:pass@host:port/dbname`)
- Automatic migration and schema initialization on startup
- Connection pooling via asyncpg or psycopg2

**Key Architectural Characteristics:**
- No ORM dependency; raw SQL with parameterized queries for security and transparency
- ACID compliance for transaction integrity across compliance workflows
- Audit logging for all data mutations
- Support for atomic multi-step workflows (e.g., application status transitions)

### 1.4 Frontend Architecture

Two separate HTML frontends served as static assets by Tornado:

**arie-portal.html** (Client Application)
- Responsive SPA for applicants applying for onboarding
- Form validation, document upload, pricing acceptance
- Real-time status tracking
- Session persistence via JWT localStorage
- Embedded AI assistant for guidance

**arie-backoffice.html** (Compliance Officer Dashboard)
- Compliance team workspace
- Application review, decision-making, compliance memo generation
- Monitoring alerts, SAR workflow management
- Analytics dashboards
- Role-based UI rendering (analyst, compliance officer, admin)

### 1.5 Concurrency & Performance

**ThreadPoolExecutor Configuration:**
- 30-thread pool for parallel API screening calls
- 30-second timeout for all external API requests
- Graceful fallback to simulated screening if APIs timeout
- Non-blocking I/O for database operations

**Database Connection Model:**
- Connection pooling in production (PostgreSQL)
- SQLite WAL mode allows concurrent readers
- Single writer model for compliance data integrity

**Metrics & Observability:**
- Prometheus-compatible metrics endpoint: `GET /metrics`
- JSON structured logging with timestamp, severity, context
- Audit trail for all user actions and system decisions

---

## 2. AI Agent Pipeline

### 2.1 Pipeline Architecture

ARIE implements a **10-agent sequential pipeline** for onboarding (5 agents) and ongoing monitoring (5 agents). The pipeline is designed with decision gates at each stage to allow compliance teams to request additional information or escalate risk before proceeding to the next stage.

**Total Compliance Checks:** 97 distributed across all agents

**Agent Execution Model:**
- Agents execute sequentially during onboarding
- Agents may run in parallel during monitoring
- Each agent is configurable, can be disabled, and has custom compliance rules
- Results are stored in the database for audit purposes
- AI recommendations are generated for each agent stage

### 2.2 Onboarding Pipeline (Agents 1-5)

#### Agent 1: Account Risk Assessment
**Purpose:** Validate applicant identity and detect account-level fraud signals
**Checks:** 12 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A1_EMAIL_FORMAT | Valid email format | High |
| A1_EMAIL_DUPLICATE | Email uniqueness check (prevent duplicate applications) | Medium |
| A1_EMAIL_DOMAIN | Verify email domain reputation (reject disposable domains) | Low |
| A1_IP_GEOLOCATION | Validate IP geolocation consistency with declared country | High |
| A1_IP_VPN_PROXY | Detect VPN/proxy usage at account creation | High |
| A1_IP_DATACENTER | Identify datacenter IPs (indicator of bot/fraud) | Medium |
| A1_DEVICE_FINGERPRINT | Consistent device across sessions | Medium |
| A1_PHONE_VERIFICATION | Phone number validation and SMS verification | Medium |
| A1_VELOCITY_CHECK | Prevent rapid account creation from same IP | High |
| A1_BLACKLIST_EMAIL | Check against known fraud email lists | High |
| A1_ACCOUNT_AGE | Minimum account age requirement (0 days soft) | Low |
| A1_REGISTRATION_ANOMALY | Detect unusual registration patterns | Medium |

**Risk Scoring:**
- Aggregates individual check results into 0-100 score
- VPN/proxy detection triggers immediate escalation flag
- Multiple email domain registrations from same IP = HIGH risk

**Integration:** ipapi.co for IP geolocation, simulated blacklist checks

---

#### Agent 2: Pre-Screening Risk Assessment
**Purpose:** Assess sector, business model, and geographic risk before proceeding to deep screening
**Checks:** 14 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A2_SECTOR_RISK | Sector risk classification (30+ sectors mapped to 1-4 risk scale) | High |
| A2_PROHIBITED_SECTOR | Detect prohibited sectors (weapons, sanctions, etc.) | Critical |
| A2_BUSINESS_MODEL_RISK | Assess business model legitimacy | High |
| A2_HIGH_RISK_JURIS | Apply FATF country risk classification (GREY, BLACK, SANCTIONED) | High |
| A2_PEP_COUNTRY | Identify countries with elevated PEP risk | Medium |
| A2_JURISDICTION_MISMATCH | Declared vs. operational jurisdiction analysis | Medium |
| A2_CROSS_BORDER_RISK | Multi-jurisdictional operations assessment | Medium |
| A2_REGULATORY_HISTORY | Historical compliance violations in jurisdiction | High |
| A2_SANCTIONS_REGIME | OFAC/UNSC sanctions regime applicability | Critical |
| A2_ENTITY_TYPE_RISK | Entity type risk (sole trader vs. corporate) | Low |
| A2_OWNERSHIP_TRANSPARENCY | Ownership structure clarity assessment | Medium |
| A2_BENEFICIAL_OWNER_KNOWN | Beneficial owner must be identifiable | High |
| A2_INDUSTRY_AML_SCORE | Industry-specific AML risk | Medium |
| A2_DECLARED_TURNOVER | Turnover threshold checks | Low |

**Risk Scoring:**
- Critical checks (prohibited sectors, sanctions) auto-fail application
- FATF BLACK countries require escalation to compliance officer
- Geographic risk combines country rating + sector rating

**Integration:** Uses hardcoded FATF country classifications, sector database

---

#### Agent 3: Document Verification
**Purpose:** Validate document authenticity and extract key identity information
**Checks:** 15 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A3_DOCUMENT_REQUIRED | At least one document per director/UBO | Critical |
| A3_DOCUMENT_TYPE_VALID | Accepted document types (passport, ID, corporate registry) | High |
| A3_DOCUMENT_EXPIRY | Document validity date check | High |
| A3_DOCUMENT_READABILITY | OCR-extracted text quality threshold | Medium |
| A3_DOCUMENT_QUALITY | Image quality, resolution, completeness | Medium |
| A3_DOCUMENT_FRAUD | AI-powered fraud detection on documents | High |
| A3_SELFIE_LIVENESS | Facial liveness detection for identity documents | High |
| A3_FACE_MATCH | Selfie matches identity document photo | High |
| A3_NAME_CONSISTENCY | Name consistency across all documents | Medium |
| A3_DOB_CONSISTENCY | Date of birth consistency across documents | High |
| A3_NATIONALITY_MATCH | Declared nationality matches document | Medium |
| A3_SANCTIONS_ID_SCREENING | Sanctions screening on extracted identity data | Critical |
| A3_SANCTIONS_FACIAL | Facial recognition sanctions screening | High |
| A3_DOCUMENT_TAMPERING | Detect document tampering or synthesis | High |
| A3_BULK_DATA_EXTRACTION | Extract name, DOB, nationality, document number | High |

**Risk Scoring:**
- Document mismatch with declared identity = VERY_HIGH risk
- Sanctions hits on identity data auto-escalate to compliance
- Liveness check failure requires re-submission

**Integration:** Sumsub KYC API (document + selfie + liveness verification)

---

#### Agent 4: Business Narrative Analysis
**Purpose:** Assess plausibility of stated business model and source of funds
**Checks:** 16 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A4_NARRATIVE_PROVIDED | Business narrative must be provided | High |
| A4_NARRATIVE_LENGTH | Minimum narrative length (500 characters) | Low |
| A4_NARRATIVE_COHERENCE | AI semantic analysis of narrative coherence | Medium |
| A4_SOURCE_OF_FUNDS_CLARITY | Source of funds explicitly described | High |
| A4_WEALTH_PLAUSIBILITY | Declared wealth plausible for business type | High |
| A4_BUSINESS_LEGITIMACY | Business model is recognized/legal | High |
| A4_INDUSTRY_STANDARD_MATCH | Business narrative matches industry standards | Medium |
| A4_TRANSACTION_PURPOSE_CLARITY | Stated transaction purpose is clear | High |
| A4_CROSS_BORDER_JUSTIFICATION | Cross-border activities justified | Medium |
| A4_THIRD_PARTY_FUNDING | If applicable, third-party funding documentation | High |
| A4_HISTORICAL_PERFORMANCE | Business historical performance consistency | Medium |
| A4_REGULATORY_COMPLIANCE_NARRATIVE | Stated regulatory compliance history | Medium |
| A4_RED_FLAG_LANGUAGE | Detect evasive, vague, or high-risk language patterns | High |
| A4_POLITICALLY_EXPOSED_NARRATIVE | Narrative consistency with PEP status | Medium |
| A4_FINANCIAL_SANCTIONS_NARRATIVE | No narrative indicating sanctions violations | High |
| A4_AI_RECOMMENDATION | AI model recommends proceed/escalate | High |

**Risk Scoring:**
- Vague narratives on source of funds = escalation
- Narrative contradicts other supplied data = HIGH risk
- Third-party funding without documentation = MEDIUM risk

**Integration:** OpenAI/Claude API for semantic analysis (with simulated fallback)

---

#### Agent 5: UBO Verification
**Purpose:** Verify beneficial ownership structure and identify actual controllers
**Checks:** 13 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A5_UBO_IDENTIFIED | At least one UBO identified | Critical |
| A5_UBO_COUNT_REASONABLE | Reasonable number of UBOs (max 10 for corporate) | Medium |
| A5_UBO_OWNERSHIP_THRESHOLD | Each UBO ownership >= 25% or significant control | High |
| A5_UBO_DOCUMENTED | UBO documentation provided (corporate registry) | High |
| A5_OWNERSHIP_CHAIN_COMPLETE | Full ownership chain mapped (no gaps) | High |
| A5_CIRCULAR_OWNERSHIP | Detect circular/shell ownership structures | High |
| A5_SHELL_COMPANY_INDICATOR | Assess probability of shell company structure | High |
| A5_REGISTRY_LOOKUP | OpenCorporates company registry verification | High |
| A5_REGISTRY_SANCTIONS | Sanctions screening on company registry data | Critical |
| A5_DIRECTOR_SANCTIONS | Sanctions screening on registered directors | Critical |
| A5_COMPANY_STATUS | Company must be active/in-good-standing | High |
| A5_COMPANY_AGE | Company operational for minimum period (0 soft) | Low |
| A5_ADVERSE_MEDIA_CHECK | Search for adverse media on company/owners | Medium |

**Risk Scoring:**
- Inability to verify UBO = application rejection
- Circular ownership structures = escalation
- Registry sanctions hits = auto-escalation
- Shell company indicators = compliance review required

**Integration:** OpenCorporates API for company registry lookup and sanctions screening

---

### 2.3 Monitoring Pipeline (Agents 6-10)

#### Agent 6: Financial Crime Intelligence (Sanctions & PEP)
**Purpose:** Real-time sanctions and PEP screening of all beneficial owners and directors
**Checks:** 12 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A6_SANCTIONS_EXACT | Exact name match against OFAC/UNSC/EU sanctions lists | Critical |
| A6_SANCTIONS_FUZZY | Fuzzy name matching against sanctions lists | High |
| A6_SANCTIONS_ALIAS | Detect known aliases/alternate names | Critical |
| A6_SANCTIONS_DOB_MATCH | DOB match strengthens sanctions scoring | High |
| A6_PEP_STATUS_DECLARED | PEP status must be explicitly declared | High |
| A6_PEP_DETECTION | AI-powered PEP detection from news/databases | High |
| A6_UNDECLARED_PEP | Detect PEP status not declared by applicant | Critical |
| A6_PEP_FAMILY_CONNECTIONS | Detect family connections to PEPs | Medium |
| A6_PEP_ASSOCIATE_SCREENING | Screen associates of identified PEPs | Medium |
| A6_SANCTIONS_REGIME_CHANGE | Immediate update if sanctions status changes | Critical |
| A6_MATCH_CONFIDENCE_SCORE | Calculate match confidence (name, DOB, nationality) | High |
| A6_FALSE_POSITIVE_REVIEW | Manual review queue for low-confidence matches | Medium |

**Risk Scoring:**
- Exact sanctions match = immediate rejection
- Undeclared PEP = VERY_HIGH risk, triggers compliance review
- Fuzzy match confidence > 85% = escalation
- Sanctions regime change = immediate alert

**Integration:** OpenSanctions API (combines OFAC, UNSC, EU, national lists)

---

#### Agent 7: Source of Funds Verification
**Purpose:** Continuous validation of declared source of funds
**Checks:** 14 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A7_SOURCE_CONSISTENT | Declared source remains consistent | Medium |
| A7_WEALTH_ASSESSMENT | Wealth amount plausible for declared source | High |
| A7_TRANSACTION_ALIGNMENT | Transactions align with declared source | High |
| A7_INCOME_VERIFICATION | Income level verification (if applicable) | Medium |
| A7_ASSET_VERIFICATION | Asset ownership verification | Medium |
| A7_BUSINESS_REVENUE_CONSISTENCY | Business revenue consistency with source | High |
| A7_THIRD_PARTY_FUNDING_CONSISTENCY | Third-party funding documentation remains valid | High |
| A7_INHERITANCE_VERIFICATION | Inheritance documentation if applicable | Medium |
| A7_LOAN_VERIFICATION | Loan documentation if source is borrowed funds | Medium |
| A7_GIFT_VERIFICATION | Gift documentation with source identification | Medium |
| A7_INVESTMENT_RETURN_PLAUSIBILITY | Investment return consistency | Medium |
| A7_ILLICIT_FUNDS_INDICATOR | Indicators of illicit funds origin | Critical |
| A7_ECONOMIC_SANITY_CHECK | Economic feasibility assessment | High |
| A7_ONGOING_MONITORING | Continuous monitoring of source changes | High |

**Risk Scoring:**
- Transaction patterns diverge from declared source = escalation
- Illicit funds indicators = immediate investigation
- Wealth assessment failures = MEDIUM+ risk

---

#### Agent 8: Risk Decision Engine
**Purpose:** Synthesize all agent findings into final risk determination and onboarding lane assignment
**Checks:** 8 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A8_RISK_AGGREGATION | Aggregate all agent risk scores | Critical |
| A8_RISK_DIMENSION_CALC | Calculate 5D risk score (Account, Sector, KYC, Narrative, Ownership) | Critical |
| A8_RISK_LEVEL_MAPPING | Map composite score to risk level (LOW, MEDIUM, HIGH, VERY_HIGH) | Critical |
| A8_CRITICAL_OVERRIDE | Override to VERY_HIGH if any critical check fails | Critical |
| A8_ONBOARDING_LANE | Assign onboarding lane (Simple, Standard, Enhanced, Escalated) | Critical |
| A8_COMPLIANCE_REVIEW_GATE | Route to compliance review if MEDIUM+ risk | High |
| A8_PRICING_ASSIGNMENT | Assign pricing tier based on risk level | High |
| A8_ESCALATION_FLAG | Auto-escalate if risk thresholds exceeded | High |

**Scoring Model - 5 Dimensions:**
1. **Account Risk (0-100):** Account creation fraud signals
2. **Sector Risk (0-100):** Business sector risk classification
3. **KYC Risk (0-100):** Document verification + identity match
4. **Narrative Risk (0-100):** Business model plausibility + SOF clarity
5. **Ownership Risk (0-100):** UBO verification + ownership transparency

Final Score: Weighted average of 5 dimensions

```
Final Risk Score = (Account×20% + Sector×20% + KYC×20% + Narrative×20% + Ownership×20%)
```

**Risk Level Mapping:**
- 0-20: LOW (Simple onboarding lane)
- 21-50: MEDIUM (Standard onboarding lane)
- 51-75: HIGH (Enhanced onboarding lane)
- 76-100: VERY_HIGH (Escalated onboarding lane)

**Onboarding Lanes:**
- **Simple:** LOW risk, direct to KYC after pricing
- **Standard:** MEDIUM risk, standard KYC workflow
- **Enhanced:** HIGH risk, compliance review before KYC
- **Escalated:** VERY_HIGH risk, escalation to senior compliance

---

#### Agent 9: Compliance Memo Generator
**Purpose:** Auto-generate structured compliance memo for compliance officer review
**Checks:** 11 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A9_MEMO_TEMPLATE | Structured memo template application | High |
| A9_CLIENT_SUMMARY | Client summary with key identifying information | High |
| A9_OWNERSHIP_SUMMARY | Ownership structure summary | High |
| A9_SCREENING_RESULTS | Screening results aggregation | High |
| A9_DOCUMENT_VERIFICATION | Document verification results | High |
| A9_SANCTIONS_STATUS | Sanctions/PEP status summary | Critical |
| A9_RISK_ASSESSMENT_NARRATIVE | Narrative assessment of risk factors | High |
| A9_SOF_NARRATIVE | Source of funds assessment narrative | High |
| A9_AI_RECOMMENDATION | AI-generated recommendation (approve/escalate/reject) | High |
| A9_COMPLIANCE_DECISION_GATE | Memo review gate before final decision | Critical |
| A9_MEMO_AUDIT_TRAIL | Memo version history and review audit trail | High |

**Memo Structure:**
```
1. Executive Summary (risk level, recommendation)
2. Client Overview (company details, entity type, jurisdiction)
3. Beneficial Ownership & Control (UBO listing, ownership chain)
4. Screening Results (sanctions, PEP, adverse media status)
5. Document Verification (identity verification status)
6. Risk Assessment (5D risk scores, key risk factors)
7. Source of Funds Assessment (declared source, plausibility)
8. Compliance Officer Notes (manual review findings)
9. AI Recommendation (proceed/escalate/reject)
10. Decision & Approval (signed by compliance officer)
```

**Integration:** Claude API for memo generation (simulated fallback available)

---

#### Agent 10: Ongoing Monitoring
**Purpose:** Continuous surveillance for changes in risk profile, sanctions status, adverse media, and regulatory updates
**Checks:** 16 compliance checks

| Check ID | Description | Risk Signal |
|----------|-------------|------------|
| A10_TRANSACTION_MONITORING | Monitor transaction patterns for anomalies | High |
| A10_TRANSACTION_VOLUME_CHANGE | Detect unusual transaction volume changes | Medium |
| A10_TRANSACTION_TYPOLOGY | Detect high-risk transaction typologies | High |
| A10_SANCTIONS_UPDATE_CHECK | Periodic re-screening against updated sanctions lists | Critical |
| A10_ADVERSE_MEDIA_SCAN | Continuous adverse media monitoring | High |
| A10_REGISTRY_CHANGE_CHECK | Detect changes in company registration | Medium |
| A10_UBO_CHANGE_DETECTION | Detect changes in ownership structure | High |
| A10_DIRECTOR_CHANGE_MONITORING | Monitor director/officer changes | Medium |
| A10_RISK_DRIFT_DETECTION | Detect increases in risk profile | High |
| A10_GEOGRAPHIC_EXPOSURE_SHIFT | Monitor changes in geographic exposure | Medium |
| A10_SECTOR_RISK_UPDATE | Sector classification changes | Low |
| A10_REGULATORY_ENVIRONMENT_CHANGE | Monitor regulatory environment changes | Medium |
| A10_PEP_STATUS_CHANGE | Immediate alert if PEP status changes | Critical |
| A10_PERIODIC_REVIEW_TRIGGER | Trigger periodic reviews based on risk level | High |
| A10_ALERT_ESCALATION_LOGIC | Escalate alerts based on severity | High |
| A10_SAR_AUTO_TRIGGER | Auto-trigger SAR if critical thresholds met | Critical |

**Monitoring Alert Severity Levels:**
- **Critical:** Sanctions match, PEP status change, SAR trigger threshold
- **High:** Adverse media, transaction anomalies, risk drift
- **Medium:** Registry changes, geographic exposure shifts
- **Low:** Informational updates

**Alert Actions:**
- Dismiss (with documented reason)
- Escalate (to senior compliance)
- Trigger Periodic Review (initiate enhanced due diligence)
- File SAR (for suspicious activity)

---

### 2.4 Agent Configuration Management

Agents are database-configurable with the following attributes:

```json
{
  "agent_number": 1,
  "name": "Account Risk Assessment",
  "stage": "onboarding",
  "enabled": true,
  "icon": "shield-account",
  "description": "Validate applicant identity and detect account-level fraud signals",
  "checks": [
    {
      "check_id": "A1_EMAIL_FORMAT",
      "description": "Valid email format",
      "risk_signal": "High",
      "weight": 0.05,
      "enabled": true
    }
  ]
}
```

Compliance teams can:
- Enable/disable individual agents
- Enable/disable specific checks
- Adjust check weights for risk scoring
- View historical check execution results
- Audit manual overrides of agent decisions

---

## 3. API Reference

### 3.1 Health & Metrics

#### GET /api/health
Returns application health status and version information.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2026-03-15T14:30:00Z",
  "version": "1.0.0",
  "database": "connected",
  "services": {
    "opensanctions": "operational",
    "opencorporates": "operational",
    "sumsub": "operational",
    "ipapi": "operational"
  }
}
```

#### GET /metrics
Prometheus-compatible metrics endpoint for monitoring.

**Response (200 OK):**
```
# HELP arie_api_requests_total Total API requests
# TYPE arie_api_requests_total counter
arie_api_requests_total{method="POST",endpoint="/api/auth/login"} 1523
arie_api_requests_total{method="GET",endpoint="/api/applications"} 4102

# HELP arie_api_request_duration_seconds API request duration
# TYPE arie_api_request_duration_seconds histogram
arie_api_request_duration_seconds_bucket{endpoint="/api/screening/run",le="0.1"} 45
arie_api_request_duration_seconds_bucket{endpoint="/api/screening/run",le="1.0"} 892
```

---

### 3.2 Authentication Endpoints

#### POST /api/auth/officer/login
Compliance officer login.

**Request:**
```json
{
  "email": "officer@arie.co.uk",
  "password": "securepassword123"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": 1,
    "email": "officer@arie.co.uk",
    "full_name": "Jane Smith",
    "role": "compliance_officer",
    "status": "active"
  }
}
```

**Error Responses:**
- 401 Unauthorized: Invalid credentials
- 429 Too Many Requests: Rate limited (10 attempts/15 min)
- 403 Forbidden: Account suspended

#### POST /api/auth/client/login
Client/applicant login.

**Request:**
```json
{
  "email": "applicant@company.com",
  "password": "clientpassword"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "client": {
    "id": 42,
    "email": "applicant@company.com",
    "company_name": "Acme Corp",
    "status": "active"
  }
}
```

#### POST /api/auth/client/register
Register new client/applicant account.

**Request:**
```json
{
  "email": "newapplicant@company.com",
  "password": "securepassword123",
  "company_name": "New Company Ltd"
}
```

**Response (201 Created):**
```json
{
  "client_id": 43,
  "email": "newapplicant@company.com",
  "company_name": "New Company Ltd",
  "status": "active"
}
```

**Error Responses:**
- 409 Conflict: Email already exists
- 429 Too Many Requests: Rate limited (5 attempts/30 min)
- 400 Bad Request: Invalid input

#### GET /api/auth/me
Get current authenticated user/client information.

**Request Headers:**
```
Authorization: Bearer {access_token}
```

**Response (200 OK):**
```json
{
  "type": "officer",
  "id": 1,
  "email": "officer@arie.co.uk",
  "full_name": "Jane Smith",
  "role": "compliance_officer",
  "status": "active",
  "permissions": ["applications:read", "applications:write", "decisions:make"]
}
```

---

### 3.3 Applications Endpoints

#### GET /api/applications
List applications with filtering and pagination.

**Query Parameters:**
```
status=prescreening_submitted&risk_level=HIGH&limit=50&offset=0&sort_by=submitted_at&sort_order=desc
```

**Response (200 OK):**
```json
{
  "applications": [
    {
      "id": 101,
      "reference": "ARF-2026-00101",
      "company_name": "Acme Corp",
      "status": "kyc_submitted",
      "risk_level": "MEDIUM",
      "risk_score": 45,
      "submitted_at": "2026-03-14T10:30:00Z",
      "assigned_to": {
        "id": 2,
        "full_name": "John Analyst"
      },
      "onboarding_lane": "Standard"
    }
  ],
  "total": 245,
  "limit": 50,
  "offset": 0
}
```

#### POST /api/applications
Create new application.

**Request:**
```json
{
  "company_name": "New Venture Ltd",
  "business_registration_number": "12345678",
  "country": "GB",
  "sector": "FinTech",
  "entity_type": "Limited Company",
  "ownership_structure": "50-50 partnership"
}
```

**Response (201 Created):**
```json
{
  "id": 102,
  "reference": "ARF-2026-00102",
  "company_name": "New Venture Ltd",
  "status": "draft",
  "created_at": "2026-03-15T14:30:00Z"
}
```

#### GET /api/applications/:id
Retrieve application details.

**Response (200 OK):**
```json
{
  "id": 101,
  "reference": "ARF-2026-00101",
  "client_id": 42,
  "company_name": "Acme Corp",
  "brn": "98765432",
  "country": "GB",
  "sector": "FinTech",
  "entity_type": "Limited Company",
  "ownership_structure": "50-50 partnership",
  "prescreening_data": {
    "business_narrative": "We provide software solutions...",
    "source_of_funds": "Retained earnings from previous operations"
  },
  "status": "kyc_submitted",
  "risk_score": 45,
  "risk_level": "MEDIUM",
  "risk_dimensions": {
    "account_risk": 25,
    "sector_risk": 40,
    "kyc_risk": 30,
    "narrative_risk": 50,
    "ownership_risk": 55
  },
  "onboarding_lane": "Standard",
  "assigned_to": 2,
  "submitted_at": "2026-03-14T10:30:00Z",
  "decided_at": null,
  "decision_by": null,
  "decision_notes": null,
  "agents_summary": [
    {
      "agent_number": 1,
      "name": "Account Risk Assessment",
      "status": "completed",
      "risk_score": 25,
      "checks_passed": 11,
      "checks_failed": 1
    }
  ]
}
```

#### PUT /api/applications/:id
Update application details.

**Request:**
```json
{
  "company_name": "Acme Corp International",
  "sector": "Financial Services"
}
```

**Response (200 OK):** Updated application object

#### PATCH /api/applications/:id
Partial update to application (used by clients for form progression).

**Request:**
```json
{
  "prescreening_data": {
    "business_narrative": "Updated narrative...",
    "expected_transaction_volume": "£500,000 per month"
  }
}
```

**Response (200 OK):** Updated application object

#### POST /api/applications/:id/submit
Submit application for prescreening (transitions from draft).

**Request:**
```json
{
  "prescreening_data": {
    "business_narrative": "...",
    "source_of_funds": "..."
  }
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "status": "prescreening_submitted",
  "prescreening_results": {
    "agents_executed": [1, 2, 4, 5, 8],
    "risk_score": 45,
    "risk_level": "MEDIUM",
    "onboarding_lane": "Standard"
  }
}
```

**Note:** Triggers agents 1-5 and 8 for prescreening analysis.

#### POST /api/applications/:id/accept-pricing
Accept pricing tier and move to KYC stage.

**Request:**
```json
{
  "pricing_tier": "MEDIUM"
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "status": "kyc_documents",
  "pricing_tier": "MEDIUM",
  "pricing_amount": 1500
}
```

#### POST /api/applications/:id/submit-kyc
Submit KYC documents (triggers Sumsub verification).

**Request:**
```json
{
  "sumsub_applicant_id": "sbx_applicant_12345"
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "status": "kyc_submitted",
  "kyc_verification_status": "pending",
  "kyc_submitted_at": "2026-03-14T15:45:00Z"
}
```

**Note:** Triggers Sumsub document verification and agents 3, 6, 9.

#### POST /api/applications/:id/memo
Generate or retrieve compliance memo.

**Request:**
```json
{
  "regenerate": false
}
```

**Response (200 OK):**
```json
{
  "memo_version": 1,
  "memo_data": {
    "executive_summary": "Applicant presents MEDIUM risk...",
    "client_overview": {...},
    "beneficial_ownership": [...],
    "screening_results": {...},
    "risk_assessment": {...},
    "ai_recommendation": "approve"
  },
  "generated_by": 0,
  "generated_at": "2026-03-14T16:00:00Z",
  "review_status": "pending_review"
}
```

#### POST /api/applications/:id/decision
Record compliance decision on application.

**Request:**
```json
{
  "decision": "approve",
  "notes": "Low-risk profile, all checks passed"
}
```

**Response (200 OK):**
```json
{
  "id": 101,
  "status": "approved",
  "decision": "approve",
  "decided_at": "2026-03-14T16:15:00Z",
  "decision_by": 1,
  "decision_notes": "Low-risk profile, all checks passed"
}
```

**Decision Options:** approve, reject, escalate_edd, request_documents

#### POST /api/applications/:id/notify
Send notification to client (approval/rejection/request for information).

**Request:**
```json
{
  "notification_type": "approved",
  "message": "Your application has been approved"
}
```

**Response (200 OK):**
```json
{
  "notification_id": 5001,
  "sent_at": "2026-03-14T16:20:00Z"
}
```

---

### 3.4 Documents Endpoints

#### POST /api/applications/:id/documents
Upload document for application.

**Request (multipart/form-data):**
```
document_type: "passport"
person_id: 1
file: <binary file data>
```

**Validation:**
- File size <= 10MB
- Accepted types: .pdf, .jpg, .jpeg, .png
- MIME type validation

**Response (201 Created):**
```json
{
  "document_id": 501,
  "application_id": 101,
  "document_type": "passport",
  "file_name": "passport_scan.pdf",
  "file_size": 2048576,
  "mime_type": "application/pdf",
  "uploaded_at": "2026-03-14T14:00:00Z",
  "verification_status": "pending"
}
```

#### POST /api/documents/:id/verify
Trigger document verification via Sumsub.

**Request:**
```json
{
  "verify_liveness": true
}
```

**Response (200 OK):**
```json
{
  "document_id": 501,
  "verification_status": "in_progress",
  "sumsub_document_id": "doc_12345"
}
```

---

### 3.5 Screening Endpoints

#### POST /api/screening/run
Run full screening suite on application.

**Request:**
```json
{
  "application_id": 101,
  "agents": [1, 2, 4, 5, 6, 8]
}
```

**Response (202 Accepted):**
```json
{
  "screening_job_id": "job_abc123",
  "status": "in_progress",
  "agents_queued": [1, 2, 4, 5, 6, 8]
}
```

**Note:** Runs agents in parallel with 30-second timeout per external API call.

#### POST /api/screening/sanctions
Sanctions screening on individual or entity.

**Request:**
```json
{
  "search_type": "name",
  "first_name": "John",
  "last_name": "Doe",
  "date_of_birth": "1980-05-15",
  "nationality": "GB"
}
```

**Response (200 OK):**
```json
{
  "screening_id": "screen_12345",
  "matches": [
    {
      "match_type": "exact",
      "source": "OFAC",
      "name": "John Doe",
      "confidence": 0.95,
      "risk_level": "critical"
    }
  ],
  "overall_risk": "critical",
  "timestamp": "2026-03-15T14:30:00Z"
}
```

**Integration:** OpenSanctions API

#### POST /api/screening/company
Company registry and sanctions screening.

**Request:**
```json
{
  "company_name": "Acme Corp",
  "jurisdiction": "GB"
}
```

**Response (200 OK):**
```json
{
  "company_data": {
    "company_number": "12345678",
    "status": "active",
    "formation_date": "2015-01-15",
    "directors": [
      {
        "name": "Jane Smith",
        "nationality": "GB",
        "sanctions_status": "clean"
      }
    ]
  },
  "sanctions_screening": {
    "company_match": false,
    "director_matches": []
  },
  "source": "OpenCorporates"
}
```

**Integration:** OpenCorporates API

#### GET /api/screening/ip
IP geolocation and VPN detection.

**Query Parameters:**
```
ip=192.168.1.1
```

**Response (200 OK):**
```json
{
  "ip_address": "192.168.1.1",
  "country": "GB",
  "city": "London",
  "latitude": 51.5074,
  "longitude": -0.1278,
  "is_vpn": false,
  "is_proxy": false,
  "is_datacenter": false,
  "threat_level": "low"
}
```

**Integration:** ipapi.co API

#### GET /api/screening/status
Get status of ongoing screening job.

**Query Parameters:**
```
job_id=job_abc123
```

**Response (200 OK):**
```json
{
  "job_id": "job_abc123",
  "status": "completed",
  "completed_agents": [1, 2, 4, 5, 6, 8],
  "aggregate_risk_score": 45,
  "aggregate_risk_level": "MEDIUM",
  "completion_time": "2026-03-15T14:35:00Z"
}
```

---

### 3.6 KYC Endpoints

#### POST /api/kyc/applicant
Create Sumsub applicant for KYC verification.

**Request:**
```json
{
  "first_name": "John",
  "last_name": "Doe",
  "date_of_birth": "1980-05-15",
  "nationality": "GB",
  "email": "john@example.com"
}
```

**Response (201 Created):**
```json
{
  "applicant_id": "sbx_applicant_12345",
  "user_id": "user_12345",
  "status": "pending",
  "created_at": "2026-03-15T14:30:00Z"
}
```

**Integration:** Sumsub KYC API

#### POST /api/kyc/token
Generate Sumsub SDK token for applicant.

**Request:**
```json
{
  "applicant_id": "sbx_applicant_12345"
}
```

**Response (200 OK):**
```json
{
  "token": "sbx_sdk_token_abcdef123456",
  "expires_at": "2026-03-15T15:30:00Z"
}
```

#### GET /api/kyc/status/:id
Get KYC verification status for applicant.

**Response (200 OK):**
```json
{
  "applicant_id": "sbx_applicant_12345",
  "status": "approved",
  "review_status": "completed",
  "review_answer": "GREEN",
  "document_verification": {
    "status": "approved",
    "document_type": "PASSPORT",
    "extracted_data": {
      "first_name": "John",
      "last_name": "Doe",
      "date_of_birth": "1980-05-15"
    }
  },
  "liveness_verification": {
    "status": "approved",
    "confidence": 0.98
  },
  "face_match": {
    "status": "approved",
    "confidence": 0.97
  }
}
```

#### POST /api/kyc/document
Submit document via Sumsub SDK (client-side initiated, server-side validation).

**Request:**
```json
{
  "applicant_id": "sbx_applicant_12345",
  "document_type": "PASSPORT",
  "country": "GB"
}
```

**Response (200 OK):**
```json
{
  "document_id": "doc_12345",
  "status": "pending_processing"
}
```

#### POST /api/kyc/webhook
Webhook endpoint for Sumsub KYC updates (called by Sumsub servers).

**Request (from Sumsub):**
```json
{
  "applicantId": "sbx_applicant_12345",
  "applicantStatus": "approved",
  "createdAt": "2026-03-15T14:30:00Z",
  "reviewStatus": "completed"
}
```

**Response (200 OK):**
```json
{
  "status": "received",
  "application_id": 101,
  "kyc_status_updated": true
}
```

---

### 3.7 SAR (Suspicious Activity Report) Endpoints

#### GET /api/sar
List Suspicious Activity Reports.

**Query Parameters:**
```
status=pending_review&severity=critical&limit=50
```

**Response (200 OK):**
```json
{
  "reports": [
    {
      "id": 1001,
      "sar_reference": "SAR-2026-00001",
      "application_id": 101,
      "subject_name": "Acme Corp",
      "subject_type": "business",
      "risk_level": "critical",
      "filing_status": "pending_review",
      "created_at": "2026-03-14T10:00:00Z",
      "prepared_by": 1
    }
  ],
  "total": 12,
  "limit": 50,
  "offset": 0
}
```

#### POST /api/sar
Create new Suspicious Activity Report.

**Request:**
```json
{
  "application_id": 101,
  "alert_id": 5001,
  "report_type": "suspected_pep",
  "subject_name": "Acme Corp",
  "subject_type": "business",
  "risk_level": "critical",
  "narrative": "Applicant declared non-PEP status, but sanctions screening identified...",
  "indicators": [
    "undeclared_pep",
    "sanctions_match"
  ],
  "transaction_details": {...},
  "supporting_documents": [501, 502]
}
```

**Response (201 Created):**
```json
{
  "id": 1001,
  "sar_reference": "SAR-2026-00001",
  "filing_status": "draft",
  "created_at": "2026-03-15T14:30:00Z",
  "prepared_by": 1
}
```

#### GET /api/sar/:id
Retrieve SAR details.

**Response (200 OK):**
```json
{
  "id": 1001,
  "sar_reference": "SAR-2026-00001",
  "application_id": 101,
  "subject_name": "Acme Corp",
  "narrative": "...",
  "indicators": [...]
,
  "filing_status": "pending_review",
  "reviewed_by": null,
  "approved_by": null,
  "filed_at": null,
  "regulatory_body": null
}
```

#### PUT /api/sar/:id
Update SAR.

**Request:**
```json
{
  "narrative": "Updated narrative with additional findings...",
  "filing_status": "pending_review"
}
```

**Response (200 OK):** Updated SAR object

#### POST /api/sar/:id/workflow
Execute SAR workflow action (review, approve, file, reject, archive).

**Request:**
```json
{
  "action": "approve",
  "notes": "Approved for filing with FCA"
}
```

**Response (200 OK):**
```json
{
  "id": 1001,
  "filing_status": "approved",
  "approved_by": 1,
  "approved_at": "2026-03-15T14:45:00Z"
}
```

#### POST /api/sar/auto-trigger
Check for conditions that auto-trigger SAR filing.

**Request:**
```json
{
  "application_id": 101
}
```

**Response (200 OK):**
```json
{
  "auto_trigger_conditions_met": false,
  "checks_evaluated": [
    {
      "check": "critical_sanctions_match",
      "met": false
    },
    {
      "check": "undeclared_pep_confirmed",
      "met": false
    }
  ]
}
```

---

### 3.8 Monitoring Endpoints

#### GET /api/monitoring/dashboard
Monitoring dashboard overview.

**Response (200 OK):**
```json
{
  "total_clients": 245,
  "clients_by_risk_level": {
    "LOW": 100,
    "MEDIUM": 95,
    "HIGH": 35,
    "VERY_HIGH": 15
  },
  "alerts_pending": 23,
  "alerts_critical": 5,
  "periodic_reviews_due": 12,
  "sars_filed_this_month": 4,
  "recent_sanctions_updates": 3
}
```

#### GET /api/monitoring/clients
List clients under monitoring.

**Query Parameters:**
```
risk_level=HIGH&alert_status=pending&limit=50
```

**Response (200 OK):**
```json
{
  "clients": [
    {
      "id": 101,
      "reference": "ARF-2026-00101",
      "company_name": "Acme Corp",
      "risk_level": "HIGH",
      "onboarded_at": "2026-01-15T10:00:00Z",
      "pending_alerts": 3,
      "last_screening": "2026-03-10T08:00:00Z",
      "next_review_due": "2026-06-15"
    }
  ],
  "total": 35
}
```

#### GET /api/monitoring/alerts
List monitoring alerts.

**Query Parameters:**
```
severity=critical&status=open&limit=50
```

**Response (200 OK):**
```json
{
  "alerts": [
    {
      "id": 5001,
      "client_id": 101,
      "alert_type": "sanctions_match",
      "severity": "critical",
      "status": "open",
      "triggered_at": "2026-03-14T10:30:00Z",
      "details": {
        "match_type": "exact",
        "source": "OFAC",
        "confidence": 0.95
      },
      "ai_recommendation": "escalate_sar",
      "action_taken": null
    }
  ],
  "total": 23,
  "limit": 50
}
```

#### POST /api/monitoring/alerts
Create manual monitoring alert.

**Request:**
```json
{
  "client_id": 101,
  "alert_type": "adverse_media",
  "severity": "high",
  "description": "Negative news article about company founder"
}
```

**Response (201 Created):**
```json
{
  "id": 5002,
  "client_id": 101,
  "alert_type": "adverse_media",
  "severity": "high",
  "status": "open",
  "created_at": "2026-03-15T14:30:00Z"
}
```

#### GET /api/monitoring/alerts/:id
Retrieve alert details.

**Response (200 OK):** Alert object with full details and audit trail

#### PATCH /api/monitoring/alerts/:id
Update alert status and action.

**Request:**
```json
{
  "status": "resolved",
  "action_taken": "escalate",
  "notes": "Escalated to compliance officer for review"
}
```

**Response (200 OK):** Updated alert object

#### GET /api/monitoring/agents
List monitoring agents and their execution status.

**Response (200 OK):**
```json
{
  "monitoring_agents": [
    {
      "agent_id": 6,
      "name": "Financial Crime Intelligence",
      "last_run": "2026-03-15T08:00:00Z",
      "next_scheduled_run": "2026-03-16T08:00:00Z",
      "clients_screened": 245,
      "alerts_generated": 5,
      "enabled": true
    },
    {
      "agent_id": 10,
      "name": "Ongoing Monitoring",
      "last_run": "2026-03-14T08:00:00Z",
      "next_scheduled_run": "2026-03-15T08:00:00Z",
      "clients_screened": 245,
      "alerts_generated": 8,
      "enabled": true
    }
  ]
}
```

#### POST /api/monitoring/agents/:id/run
Manually trigger monitoring agent execution.

**Request:**
```json
{
  "client_id": 101
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "monitor_job_12345",
  "agent_id": 6,
  "client_id": 101,
  "status": "in_progress"
}
```

#### GET /api/monitoring/reviews
List periodic compliance reviews.

**Query Parameters:**
```
status=due&risk_level=HIGH&limit=50
```

**Response (200 OK):**
```json
{
  "reviews": [
    {
      "id": 2001,
      "client_id": 101,
      "review_status": "due",
      "risk_level": "HIGH",
      "last_review": "2025-09-15",
      "due_date": "2026-03-15",
      "assigned_to": 2,
      "created_at": "2026-03-15T00:00:00Z"
    }
  ],
  "total": 12,
  "limit": 50
}
```

#### GET /api/monitoring/reviews/:id
Retrieve periodic review details.

**Response (200 OK):**
```json
{
  "id": 2001,
  "client_id": 101,
  "company_name": "Acme Corp",
  "risk_level": "HIGH",
  "review_period": {
    "from": "2025-09-15",
    "to": "2026-03-15"
  },
  "screening_updates": {...},
  "transaction_monitoring": {...},
  "alert_summary": {...},
  "recommendation": "continue",
  "review_decision": null,
  "reviewed_by": null,
  "reviewed_at": null
}
```

#### POST /api/monitoring/reviews/:id/decision
Record compliance decision on periodic review.

**Request:**
```json
{
  "decision": "continue",
  "notes": "Client profile unchanged, no escalation warranted"
}
```

**Response (200 OK):**
```json
{
  "id": 2001,
  "review_decision": "continue",
  "reviewed_by": 1,
  "reviewed_at": "2026-03-15T14:45:00Z"
}
```

**Decision Options:** continue, enhanced_monitoring, request_info, exit_relationship

#### POST /api/monitoring/reviews/schedule
Schedule periodic reviews (bulk operation).

**Request:**
```json
{
  "run_immediately": true
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "schedule_job_12345",
  "reviews_scheduled": 35,
  "due_for_low_risk": 0,
  "due_for_medium_risk": 12,
  "due_for_high_risk": 18,
  "due_for_very_high_risk": 5
}
```

**Scheduling Rules:**
- LOW risk: Biennial (every 2 years)
- MEDIUM risk: Annual (every 12 months)
- HIGH risk: Semi-annual (every 6 months)
- VERY_HIGH risk: Quarterly (every 3 months)

---

### 3.9 Configuration Endpoints

#### GET /api/config/risk-model
Retrieve current risk scoring model configuration.

**Response (200 OK):**
```json
{
  "risk_dimensions": {
    "account_risk": {
      "weight": 0.20,
      "description": "Account creation fraud signals"
    },
    "sector_risk": {
      "weight": 0.20,
      "description": "Business sector risk classification"
    },
    "kyc_risk": {
      "weight": 0.20,
      "description": "Document verification + identity match"
    },
    "narrative_risk": {
      "weight": 0.20,
      "description": "Business model plausibility + SOF clarity"
    },
    "ownership_risk": {
      "weight": 0.20,
      "description": "UBO verification + ownership transparency"
    }
  },
  "risk_level_thresholds": {
    "LOW": [0, 20],
    "MEDIUM": [21, 50],
    "HIGH": [51, 75],
    "VERY_HIGH": [76, 100]
  },
  "onboarding_lanes": {
    "Simple": {"min_score": 0, "max_score": 20},
    "Standard": {"min_score": 21, "max_score": 50},
    "Enhanced": {"min_score": 51, "max_score": 75},
    "Escalated": {"min_score": 76, "max_score": 100}
  },
  "pricing_tiers": {
    "LOW": 500,
    "MEDIUM": 1500,
    "HIGH": 3500,
    "VERY_HIGH": 5000
  }
}
```

#### PUT /api/config/risk-model
Update risk scoring model configuration.

**Request:**
```json
{
  "risk_dimensions": {
    "account_risk": {
      "weight": 0.15
    },
    "sector_risk": {
      "weight": 0.25
    }
  }
}
```

**Response (200 OK):** Updated configuration object

#### GET /api/config/ai-agents
List all AI agent configurations.

**Response (200 OK):**
```json
{
  "agents": [
    {
      "agent_number": 1,
      "name": "Account Risk Assessment",
      "stage": "onboarding",
      "enabled": true,
      "icon": "shield-account",
      "description": "Validate applicant identity and detect account-level fraud signals",
      "checks_count": 12,
      "checks": [...]
    }
  ]
}
```

#### POST /api/config/ai-agents
Create new AI agent configuration (advanced use case).

**Request:**
```json
{
  "agent_number": 11,
  "name": "Custom Agent",
  "stage": "monitoring",
  "enabled": true,
  "description": "Custom monitoring agent"
}
```

**Response (201 Created):** New agent object

#### PUT /api/config/ai-agents/:id
Update AI agent configuration.

**Request:**
```json
{
  "enabled": false,
  "checks": [
    {
      "check_id": "A1_EMAIL_FORMAT",
      "weight": 0.10
    }
  ]
}
```

**Response (200 OK):** Updated agent object

#### DELETE /api/config/ai-agents/:id
Delete AI agent configuration (compliance-sensitive operation).

**Response (204 No Content)**

---

### 3.10 Users & Admin Endpoints

#### GET /api/users
List users (admin only).

**Query Parameters:**
```
role=analyst&status=active&limit=50
```

**Response (200 OK):**
```json
{
  "users": [
    {
      "id": 1,
      "email": "officer@arie.co.uk",
      "full_name": "Jane Smith",
      "role": "compliance_officer",
      "status": "active"
    }
  ],
  "total": 5
}
```

#### POST /api/users
Create new user account (admin only).

**Request:**
```json
{
  "email": "newuser@arie.co.uk",
  "full_name": "New User",
  "role": "analyst",
  "password": "secure_initial_password"
}
```

**Response (201 Created):** User object (password not returned)

#### PUT /api/users/:id
Update user details.

**Request:**
```json
{
  "full_name": "Jane Smith Updated",
  "role": "senior_compliance_officer",
  "status": "inactive"
}
```

**Response (200 OK):** Updated user object

#### GET /api/audit
Retrieve audit log (admin/compliance officer only).

**Query Parameters:**
```
action=applications:decision&user_id=1&date_from=2026-03-01&limit=100
```

**Response (200 OK):**
```json
{
  "audit_entries": [
    {
      "timestamp": "2026-03-15T14:45:00Z",
      "user_id": 1,
      "user_name": "Jane Smith",
      "user_role": "compliance_officer",
      "action": "applications:decision",
      "target": "application:101",
      "detail": "Decision: approve",
      "ip_address": "192.168.1.100"
    }
  ],
  "total": 245,
  "limit": 100
}
```

---

### 3.11 Reports & Dashboard

#### GET /api/reports/generate
Generate compliance report.

**Query Parameters:**
```
report_type=monthly_summary&date_from=2026-02-01&date_to=2026-02-28
```

**Response (200 OK):**
```json
{
  "report_type": "monthly_summary",
  "period": {
    "from": "2026-02-01",
    "to": "2026-02-28"
  },
  "statistics": {
    "applications_received": 25,
    "applications_approved": 18,
    "applications_rejected": 2,
    "applications_pending": 5,
    "average_risk_score": 42,
    "sars_filed": 2,
    "sanctions_matches": 1
  },
  "generated_at": "2026-03-01T00:00:00Z"
}
```

#### GET /api/dashboard
Get dashboard summary for authenticated user.

**Response (200 OK):**
```json
{
  "user_role": "compliance_officer",
  "applications_assigned": 12,
  "pending_decisions": 5,
  "alerts_pending": 3,
  "reviews_due": 2,
  "recent_activity": [...]
}
```

#### GET /api/notifications
Get user notifications.

**Query Parameters:**
```
unread_only=true&limit=20
```

**Response (200 OK):**
```json
{
  "notifications": [
    {
      "id": 1001,
      "type": "application_submitted",
      "title": "New Application Submitted",
      "message": "Application ARF-2026-00101 submitted for review",
      "read": false,
      "created_at": "2026-03-15T14:00:00Z"
    }
  ]
}
```

#### PATCH /api/notifications/:id/read
Mark notification as read.

**Request:**
```json
{
  "read": true
}
```

**Response (200 OK):** Updated notification object

---

### 3.12 AI Assistant

#### POST /api/ai/assistant
Query AI assistant for guidance.

**Request:**
```json
{
  "application_id": 101,
  "query": "What are the main risk factors for this application?",
  "context": "compliance_review"
}
```

**Response (200 OK):**
```json
{
  "response": "Based on the application analysis, the main risk factors are: 1) Sector risk is classified as HIGH due to FinTech classification. 2) PEP detection found one undeclared politically exposed person. 3) Narrative on source of funds is vague regarding initial capital source.",
  "confidence": 0.87,
  "sources": [1, 2, 4, 6, 9]
}
```

---

### 3.13 Application Save/Resume

#### GET /api/save-resume
Retrieve saved application draft.

**Response (200 OK):**
```json
{
  "application_id": 101,
  "last_saved": "2026-03-15T13:00:00Z",
  "saved_data": {
    "company_name": "Acme Corp",
    "prescreening_data": {...}
  }
}
```

#### POST /api/save-resume
Save application draft (auto-save).

**Request:**
```json
{
  "application_id": 101,
  "data": {
    "company_name": "Acme Corp",
    "prescreening_data": {...}
  }
}
```

**Response (200 OK):**
```json
{
  "application_id": 101,
  "saved_at": "2026-03-15T14:30:00Z"
}
```

---

## 4. Database Schema

### 4.1 Core Tables

#### users Table
Compliance team members (officers, analysts, admin).

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT CHECK (role IN ('admin', 'sco', 'co', 'analyst')),
  status TEXT CHECK (status IN ('active', 'inactive', 'suspended')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login TIMESTAMP
);

-- Indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_status ON users(status);
```

**Roles:**
- `admin`: Full system access, user management, configuration
- `sco`: Senior Compliance Officer, approval authority for major decisions
- `co`: Compliance Officer, application review and decision-making
- `analyst`: Junior analyst, application screening and fact-checking

---

#### clients Table
Client/applicant user accounts (self-service onboarding applicants).

```sql
CREATE TABLE clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  company_name TEXT NOT NULL,
  status TEXT CHECK (status IN ('active', 'inactive', 'banned')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login TIMESTAMP,
  account_notes TEXT
);

-- Indexes
CREATE INDEX idx_clients_email ON clients(email);
CREATE INDEX idx_clients_status ON clients(status);
CREATE INDEX idx_clients_company ON clients(company_name);
```

---

#### applications Table
Core application records for onboarding workflow.

```sql
CREATE TABLE applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  reference TEXT UNIQUE NOT NULL, -- ARF-YYYY-NNNNN format

  -- Company Information
  company_name TEXT NOT NULL,
  brn TEXT, -- Business Registration Number
  country TEXT NOT NULL, -- ISO 3166-1 alpha-2
  sector TEXT,
  entity_type TEXT, -- Limited Company, Partnership, Sole Trader, etc.
  ownership_structure TEXT,

  -- Prescreening Data (JSON)
  prescreening_data JSON, -- Contains business_narrative, source_of_funds, etc.

  -- Workflow Status
  status TEXT CHECK (status IN (
    'draft',
    'prescreening_submitted',
    'pricing_review',
    'pricing_accepted',
    'kyc_documents',
    'kyc_submitted',
    'compliance_review',
    'in_review',
    'edd_required',
    'approved',
    'rejected',
    'rmi_sent',
    'withdrawn'
  )),

  -- Risk Assessment
  risk_score REAL, -- 0-100
  risk_level TEXT CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')),
  risk_dimensions JSON, -- {account_risk, sector_risk, kyc_risk, narrative_risk, ownership_risk}
  onboarding_lane TEXT CHECK (onboarding_lane IN ('Simple', 'Standard', 'Enhanced', 'Escalated')),

  -- Assignment
  assigned_to INTEGER, -- user_id

  -- Pricing
  pricing_tier TEXT,
  pricing_amount INTEGER,

  -- Workflow Timestamps
  submitted_at TIMESTAMP,
  decided_at TIMESTAMP,

  -- Decision
  decision TEXT CHECK (decision IN ('approve', 'reject', 'escalate_edd', 'request_documents')),
  decision_by INTEGER, -- user_id
  decision_notes TEXT,

  -- Audit
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (client_id) REFERENCES clients(id),
  FOREIGN KEY (assigned_to) REFERENCES users(id),
  FOREIGN KEY (decision_by) REFERENCES users(id)
);

-- Indexes
CREATE INDEX idx_applications_client ON applications(client_id);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_risk_level ON applications(risk_level);
CREATE INDEX idx_applications_assigned_to ON applications(assigned_to);
CREATE INDEX idx_applications_reference ON applications(reference);
CREATE INDEX idx_applications_country ON applications(country);
CREATE INDEX idx_applications_sector ON applications(sector);
```

---

#### directors Table
Company directors for ownership verification.

```sql
CREATE TABLE directors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  nationality TEXT,
  is_pep BOOLEAN DEFAULT 0,
  pep_confidence REAL,
  sanctions_screening_result JSON, -- Stores latest screening result
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id)
);

-- Indexes
CREATE INDEX idx_directors_application ON directors(application_id);
CREATE INDEX idx_directors_is_pep ON directors(is_pep);
```

---

#### ubos Table
Ultimate Beneficial Owners (UBOs) for ownership chain verification.

```sql
CREATE TABLE ubos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  nationality TEXT,
  ownership_pct REAL, -- Percentage ownership
  is_pep BOOLEAN DEFAULT 0,
  pep_confidence REAL,
  sanctions_screening_result JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id)
);

-- Indexes
CREATE INDEX idx_ubos_application ON ubos(application_id);
CREATE INDEX idx_ubos_is_pep ON ubos(is_pep);
```

---

#### documents Table
Uploaded documents and their verification status.

```sql
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  person_id INTEGER, -- director_id or ubo_id (NULL for company documents)
  doc_type TEXT NOT NULL, -- passport, id, business_registration, etc.
  doc_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  file_size INTEGER,
  mime_type TEXT,

  -- Verification Status
  verification_status TEXT CHECK (verification_status IN (
    'pending',
    'in_progress',
    'verified',
    'rejected',
    'manual_review'
  )),
  verification_results JSON, -- {document_fraud, liveness_check, face_match, etc.}

  -- Sumsub Integration
  sumsub_document_id TEXT,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id)
);

-- Indexes
CREATE INDEX idx_documents_application ON documents(application_id);
CREATE INDEX idx_documents_verification_status ON documents(verification_status);
CREATE INDEX idx_documents_doc_type ON documents(doc_type);
```

---

#### risk_config Table
Risk scoring model configuration and sector mappings.

```sql
CREATE TABLE risk_config (
  id INTEGER PRIMARY KEY,

  -- 5D Risk Model Weights
  account_risk_weight REAL DEFAULT 0.20,
  sector_risk_weight REAL DEFAULT 0.20,
  kyc_risk_weight REAL DEFAULT 0.20,
  narrative_risk_weight REAL DEFAULT 0.20,
  ownership_risk_weight REAL DEFAULT 0.20,

  -- Sector Risk Mappings (JSON)
  -- {sector_name: 1-4 risk_level, ...}
  sector_risk_mappings JSON,

  -- FATF Country Classifications (JSON)
  -- {country_code: 'BLACK'|'GREY'|'SANCTIONED'|'LOW_RISK', ...}
  fatf_country_mappings JSON,

  -- Pricing Tiers
  pricing_low REAL DEFAULT 500,
  pricing_medium REAL DEFAULT 1500,
  pricing_high REAL DEFAULT 3500,
  pricing_very_high REAL DEFAULT 5000,

  -- Risk Thresholds
  low_risk_max REAL DEFAULT 20,
  medium_risk_max REAL DEFAULT 50,
  high_risk_max REAL DEFAULT 75,

  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

#### ai_agents Table
Configuration for 10-agent pipeline.

```sql
CREATE TABLE ai_agents (
  agent_number INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  stage TEXT CHECK (stage IN ('onboarding', 'monitoring')),
  icon TEXT,
  description TEXT,
  enabled BOOLEAN DEFAULT 1,

  -- Checks definition (JSON array)
  checks JSON,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

#### ai_checks Table
Individual compliance checks with execution results.

```sql
CREATE TABLE ai_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  agent_number INTEGER NOT NULL,
  check_id TEXT NOT NULL,
  check_name TEXT,
  risk_signal TEXT,
  passed BOOLEAN,
  confidence REAL, -- 0-1
  result_data JSON,
  executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id),
  FOREIGN KEY (agent_number) REFERENCES ai_agents(agent_number)
);

-- Indexes
CREATE INDEX idx_ai_checks_application ON ai_checks(application_id);
CREATE INDEX idx_ai_checks_agent ON ai_checks(agent_number);
CREATE INDEX idx_ai_checks_passed ON ai_checks(passed);
```

---

#### audit_log Table
Comprehensive audit trail for compliance.

```sql
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  -- Actor
  user_id INTEGER,
  user_name TEXT,
  user_role TEXT,

  -- Action
  action TEXT NOT NULL, -- applications:read, applications:decision, etc.
  target TEXT NOT NULL, -- application:101, sar:1001, etc.
  detail TEXT,

  -- Security
  ip_address TEXT,
  user_agent TEXT,

  INDEX idx_audit_timestamp(timestamp),
  INDEX idx_audit_user(user_id),
  INDEX idx_audit_action(action),
  INDEX idx_audit_target(target)
);
```

---

#### SAR Tables
Suspicious Activity Report workflow.

```sql
CREATE TABLE sar_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  alert_id INTEGER,
  sar_reference TEXT UNIQUE NOT NULL,

  -- SAR Content
  report_type TEXT, -- suspected_pep, sanctions_match, etc.
  subject_name TEXT NOT NULL,
  subject_type TEXT, -- individual, business
  risk_level TEXT CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),

  narrative TEXT,
  indicators JSON, -- Array of indicators
  transaction_details JSON,
  supporting_documents JSON, -- Array of document IDs

  -- Workflow
  filing_status TEXT CHECK (filing_status IN (
    'draft',
    'pending_review',
    'approved',
    'filed',
    'rejected',
    'archived'
  )),

  -- Actors
  prepared_by INTEGER, -- user_id
  reviewed_by INTEGER,
  approved_by INTEGER,

  -- Timestamps
  filed_at TIMESTAMP,
  regulatory_body TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id),
  FOREIGN KEY (prepared_by) REFERENCES users(id),
  FOREIGN KEY (reviewed_by) REFERENCES users(id),
  FOREIGN KEY (approved_by) REFERENCES users(id)
);

-- Indexes
CREATE INDEX idx_sar_application ON sar_reports(application_id);
CREATE INDEX idx_sar_filing_status ON sar_reports(filing_status);
CREATE INDEX idx_sar_reference ON sar_reports(sar_reference);
```

---

#### Monitoring Tables

```sql
CREATE TABLE monitoring_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  alert_type TEXT NOT NULL, -- sanctions_match, adverse_media, etc.
  severity TEXT CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  status TEXT CHECK (status IN ('open', 'dismissed', 'escalated', 'resolved')),
  description TEXT,
  ai_recommendation TEXT,
  action_taken TEXT,
  triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id)
);

CREATE TABLE periodic_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  review_status TEXT CHECK (review_status IN ('pending', 'in_progress', 'completed')),
  risk_level TEXT,
  last_review_date TIMESTAMP,
  next_review_due TIMESTAMP,
  assigned_to INTEGER,
  review_decision TEXT,
  reviewed_by INTEGER,
  reviewed_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id),
  FOREIGN KEY (assigned_to) REFERENCES users(id),
  FOREIGN KEY (reviewed_by) REFERENCES users(id)
);

CREATE TABLE compliance_memos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER UNIQUE NOT NULL,
  version INTEGER,
  memo_data JSON,
  generated_by INTEGER,
  generated_at TIMESTAMP,
  ai_recommendation TEXT,
  review_status TEXT CHECK (review_status IN ('pending_review', 'reviewed', 'approved')),
  reviewed_by INTEGER,
  reviewed_at TIMESTAMP,

  FOREIGN KEY (application_id) REFERENCES applications(id),
  FOREIGN KEY (generated_by) REFERENCES users(id),
  FOREIGN KEY (reviewed_by) REFERENCES users(id)
);

CREATE TABLE client_notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  notification_type TEXT,
  title TEXT,
  message TEXT,
  read BOOLEAN DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE client_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  jwt_jti TEXT UNIQUE, -- JWT session ID for binding
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP,

  FOREIGN KEY (client_id) REFERENCES clients(id)
);
```

---

## 5. Security Architecture

### 5.1 Authentication & Authorization

**JWT (JSON Web Token) Implementation:**
- Algorithm: HS256 (HMAC with SHA-256)
- Issuer: `arie-finance`
- Header validation: Issuer verification required
- Session Binding: JWT includes `jti` (JWT ID) claim linked to session in database
- Time Claims: `nbf` (not-before), `exp` (expiration) mandatory
- Expiration: 1 hour for access tokens
- Refresh: Optional refresh token endpoint for extended sessions

**JWT Payload Structure:**
```json
{
  "iss": "arie-finance",
  "sub": "user:1",
  "jti": "session_uuid_abc123",
  "type": "officer|client",
  "role": "compliance_officer",
  "nbf": 1710514200,
  "exp": 1710517800,
  "iat": 1710514200,
  "email": "officer@arie.co.uk"
}
```

**Session Binding Verification:**
- On every request, `jti` claim is verified against `client_sessions` table
- Session can be invalidated server-side (logout, account suspension)
- Prevents token replay attacks

---

### 5.2 Password Security

**Bcrypt Password Hashing:**
- Algorithm: bcrypt with salt rounds 12
- Hash generation: `bcrypt.hashpw(password, bcrypt.gensalt(12))`
- Verification: `bcrypt.checkpw(password, hash)`
- Never store plaintext passwords
- Password reset: One-time token sent via email (not implemented in this version)

---

### 5.3 Role-Based Access Control (RBAC)

**User Roles:**

| Role | Applications | Users | Config | Audit | Decisions | KYC | SAR |
|------|---|---|---|---|---|---|---|
| admin | R/W | R/W | R/W | R | - | - | - |
| sco | R | - | - | R | R/W | - | R/W |
| co | R/W | - | - | R | R/W | R | R/W |
| analyst | R | - | - | R | - | R | R |
| client | Own Only | - | - | - | - | - | - |

**Permission Enforcement:**
- Each endpoint validates user role before processing
- Application-level access checks (analyst cannot escalate decisions)
- Attribute-based checks (analyst can only read their assigned applications)

---

### 5.4 Input Sanitization & XSS Prevention

**HTML Escaping:**
```python
import html
sanitized = html.escape(user_input)
```

**JSON Input Validation:**
```python
try:
    data = json.loads(request.body)
    # Validate schema
except json.JSONDecodeError:
    return 400 Bad Request
```

**SQL Injection Prevention:**
- All database queries use parameterized statements
- No string concatenation for SQL queries
- Example: `cursor.execute("SELECT * FROM applications WHERE id = ?", (app_id,))`

---

### 5.5 Rate Limiting

**Login Rate Limiting:**
- 10 failed attempts per 15 minutes per IP address
- Sliding window algorithm
- Implementation: In-memory counter with timestamp tracking

**Registration Rate Limiting:**
- 5 attempts per 30 minutes per IP address
- Prevents email enumeration attacks

**API Rate Limiting (Optional):**
- 100 requests per minute per authenticated user
- Applies to all endpoints except health check

---

### 5.6 Security Headers

**Required Headers (All Endpoints):**

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
```

**Production Headers:**
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

---

### 5.7 CORS Configuration

**Development:**
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
```

**Production:**
```
Access-Control-Allow-Origin: https://arie-portal.example.com
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Access-Control-Allow-Credentials: true
```

---

### 5.8 SECRET_KEY Management

**Validation:**
- Production environment: SECRET_KEY must be set, crashes if missing
- Development: AUTO-GENERATES 32-byte random key if not set (warning logged)
- Minimum length: 32 characters
- Used for JWT signing, session encryption

**Environment Variable:**
```bash
export SECRET_KEY="your-production-secret-key-min-32-chars"
```

---

### 5.9 XSRF Protection

**Implementation:**
- XSRF tokens generated for all state-changing requests (POST, PUT, PATCH, DELETE)
- Token stored in session and validated in request headers
- Exception: Bearer token authentication bypasses XSRF (API clients)

---

### 5.10 File Upload Security

**Validation:**
- Maximum file size: 10MB
- Accepted MIME types: application/pdf, image/jpeg, image/png
- Filename sanitization: Remove path traversal characters, generate UUID-based names
- Storage: Files stored outside web root
- Antivirus scan: Optional integration (not implemented in core)

---

### 5.11 Data Protection

**At Rest:**
- SQLite: No encryption (dev only)
- PostgreSQL: Optional TLS encryption for data in transit
- Sensitive fields: password_hash, verification_results encrypted with SECRET_KEY

**In Transit:**
- All traffic over HTTPS (production requirement)
- TLS 1.2 minimum
- Certificate pinning: Optional for API clients

---

### 5.12 Audit Logging

**Logged Actions:**
- User authentication (login/logout)
- Application status changes
- Decision entries
- Configuration modifications
- SAR filings
- Document uploads
- Failed authorization attempts

**Audit Record Contents:**
- Timestamp (UTC)
- User ID, name, role
- Action type
- Target resource
- Detail (old value → new value for updates)
- IP address
- User agent

**Retention:**
- Retained for minimum 7 years for compliance
- Immutable (append-only)

---

## 6. DevOps & Deployment

### 6.1 Server Startup & Configuration

**start.sh Script:**
```bash
#!/bin/bash

# Environment setup
export ENVIRONMENT="${ENVIRONMENT:-development}"
export PORT="${PORT:-8080}"
export DATABASE_URL="${DATABASE_URL:-}"
export SECRET_KEY="${SECRET_KEY:-}"

# Production validation
if [ "$ENVIRONMENT" = "production" ]; then
    if [ -z "$SECRET_KEY" ]; then
        echo "CRITICAL: SECRET_KEY not set in production"
        exit 1
    fi
fi

# Database setup
if [ -z "$DATABASE_URL" ]; then
    export DB_PATH="${DB_PATH:-data/arie.db}"
    mkdir -p data
fi

# Start application
python server.py
```

**Environment Variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| ENVIRONMENT | No | development | development, staging, production |
| PORT | No | 8080 | HTTP server port |
| SECRET_KEY | Yes (prod) | Auto-generated (dev) | JWT signing key |
| DATABASE_URL | No (dev) | data/arie.db | PostgreSQL connection string |
| DB_PATH | No | data/arie.db | SQLite file path (dev) |
| ALLOWED_ORIGIN | No | http://localhost:3000 | CORS origin |
| OPENSANCTIONS_API_KEY | No | simulated | OpenSanctions API key |
| OPENCORPORATES_API_KEY | No | simulated | OpenCorporates API key |
| IPAPI_API_KEY | No | simulated | ipapi.co API key |
| SUMSUB_API_KEY | No | simulated | Sumsub API key |
| OPENAI_API_KEY | No | simulated | OpenAI/Claude API key |

---

### 6.2 Production Deployment

**Architecture:**
```
Internet
   ↓
Caddy/Nginx (TLS termination, reverse proxy)
   ↓
Tornado (8080) - Stateless application server
   ↓
PostgreSQL (port 5432) - Database
```

**Deployment with Supervisor:**
```ini
[program:arie]
command=python /opt/arie/server.py
directory=/opt/arie
user=arie
environment=ENVIRONMENT=production,SECRET_KEY=...,DATABASE_URL=...
autostart=true
autorestart=true
startsecs=10
stopwaitsecs=10
stdout_logfile=/var/log/arie/app.log
stderr_logfile=/var/log/arie/error.log
```

**Deployment with Gunicorn:**
```bash
gunicorn \
  --bind 127.0.0.1:8080 \
  --workers 4 \
  --worker-class tornado \
  --timeout 30 \
  --access-logfile /var/log/arie/access.log \
  --error-logfile /var/log/arie/error.log \
  server:app
```

---

### 6.3 Docker Deployment

**Dockerfile:**
```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static/ static/

EXPOSE 8080

ENV ENVIRONMENT=production
CMD ["python", "server.py"]
```

**docker-compose.yml:**
```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      ENVIRONMENT: production
      SECRET_KEY: ${SECRET_KEY}
      DATABASE_URL: postgresql://arie:password@postgres:5432/arie
    depends_on:
      - postgres

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: arie
      POSTGRES_USER: arie
      POSTGRES_PASSWORD: password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  caddy:
    image: caddy:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data

volumes:
  postgres_data:
  caddy_data:
```

---

### 6.4 Database Migrations

**SQLite Initialization (Development):**
```python
def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA_SQL)
    db.commit()
    db.close()
```

**PostgreSQL Initialization (Production):**
```python
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.executescript(SCHEMA_SQL)
    await pool.close()
```

**Schema Version Management:**
```sql
CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY,
  description TEXT,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema');
```

---

### 6.5 Monitoring & Observability

**Prometheus Metrics Endpoint:**
- Endpoint: `/metrics`
- Format: Prometheus text format
- Metrics:
  - `arie_api_requests_total` (counter)
  - `arie_api_request_duration_seconds` (histogram)
  - `arie_database_connections` (gauge)
  - `arie_screenings_processed_total` (counter)
  - `arie_sanctions_matches_total` (counter)

**Logging:**
```python
import json
import logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_data)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger()
logger.addHandler(handler)
```

**Log Aggregation:**
- Send logs to ELK stack, Splunk, or CloudWatch
- Query example: `level: ERROR AND timestamp: [now-24h TO now]`

---

### 6.6 Backup & Disaster Recovery

**PostgreSQL Backup:**
```bash
# Daily backup
0 2 * * * pg_dump -U arie arie | gzip > /backups/arie-$(date +\%Y\%m\%d).sql.gz

# Test restore
pg_restore -U arie -d arie_test /backups/arie-20260315.sql.gz
```

**RTO/RPO Targets:**
- Recovery Time Objective (RTO): 4 hours
- Recovery Point Objective (RPO): 1 hour

---

## 7. Compliance Logic

(Continued in next section due to length constraints...)

---

---

## 7. Compliance Logic

### 7.1 FATF Country Risk Classification

The ARIE platform implements Financial Action Task Force (FATF) country risk classifications:

**BLACK List (High Risk):**
- Iran, North Korea, Syria, Crimea region
- Auto-escalation: Applications rejected immediately
- Onboarding Lane: Not available

**GREY List (Elevated Risk):**
- Countries with strategic AML/CFT deficiencies but with FATF commitment
- 100+ jurisdictions including many Eastern European, African, and Asian nations
- Onboarding Lane: Escalated (requires senior compliance officer approval)
- Additional screening: Enhanced due diligence mandatory

**SANCTIONED (Special Regime):**
- OFAC sanctions programs active
- UN sanctions applicable
- EU sanctions regimes
- Treatment: Critical risk, requires immediate escalation and potential SAR

**LOW_RISK:**
- FATF compliant jurisdictions
- UK, EU member states, US, Canada, Australia, etc.
- Default treatment: Standard onboarding lane

---

### 7.2 Sector Risk Classification

Sector risk is mapped on a 1-4 scale (1=Low, 4=Critical):

**Level 1 (Low Risk):**
- Professional services, technology, education, healthcare
- Manufacturing (non-strategic), agriculture, tourism

**Level 2 (Medium Risk):**
- Financial services, real estate, insurance, export-import
- Telecommunications, construction, media

**Level 3 (High Risk):**
- Virtual assets, cryptocurrency, money transmission, gaming
- Jewelry, precious metals, import-export of controlled goods
- Insurance intermediaries, forex trading

**Level 4 (Critical Risk - Prohibited):**
- Weapons manufacturing, explosives
- Narcotics production, trafficking
- Counterfeiting, human trafficking
- Proliferation financing
- Nuclear materials
- Terrorism financing

**Sector Risk Scoring:**
```
sector_risk_score = (sector_level / 4) * 100
Level 1 → 25, Level 2 → 50, Level 3 → 75, Level 4 → 100
```

---

### 7.3 PEP Detection & Management

**Definition:** Politically Exposed Person - individual holding or held prominent public function

**Categories:**
1. **Domestic PEPs:** National government officials, central bank governors, judicial officials
2. **International Organization PEPs:** UN officials, World Bank officials, IMF officials
3. **Foreign PEPs:** Officials of foreign governments
4. **Family/Close Associates:** Spouse, children, parents of above
5. **Beneficial Owners via Control:** Individuals with effective control of PEP assets

**Undeclared PEP Flagging:**
- Agent 6 uses OpenSanctions to identify news-derived PEP status
- If applicant did NOT declare PEP status but screening reveals it: **CRITICAL risk**
- Triggers automatic SAR consideration
- Escalation to compliance officer mandatory

**Declared PEP Handling:**
- Applicant explicitly declared PEP status
- Allowed to proceed to compliance review
- Enhanced due diligence applies (mandatory)
- Higher pricing tier assigned
- More frequent monitoring required

---

### 7.4 Sanctions Screening Integration

**OpenSanctions API Coverage:**
- OFAC (US Office of Foreign Assets Control)
- UN Security Council sanctions
- EU consolidated sanctions list
- UK consolidated sanctions list (post-Brexit)
- 200+ additional national/regional sanctions programs
- Real-time updates (checked daily)

**Screening Logic:**
```
1. Exact name + DOB match → CRITICAL risk
2. Fuzzy match (confidence > 85%) → HIGH risk
3. Alias detected → CRITICAL risk
4. Family member match → HIGH risk
5. Entity sanctions match → CRITICAL risk
```

**Match Confidence Scoring:**
```
confidence =
  (name_match_score × 0.5) +
  (dob_match_score × 0.3) +
  (nationality_match_score × 0.2)
```

**Screening Frequency:**
- Onboarding: Post-document submission (agent 3)
- Post-KYC: Immediate (agent 6)
- Ongoing monitoring: Daily for all clients (agent 10)
- Alert frequency: Immediate if new match detected

---

### 7.5 Company Registry Verification

**OpenCorporates Integration:**
- 200+ million company records
- Covers 100+ jurisdictions
- Real-time API queries

**Verification Steps:**
1. Search by company name + registration number + jurisdiction
2. Retrieve company status (active/dissolved/struck-off)
3. Retrieve director listing
4. Cross-check UBO declarations against registry
5. Verify company age and formation date

**Failure Modes:**
- Company not found in registry: VERY_HIGH risk, escalation required
- Company status dissolved: Rejection
- Director mismatch: HIGH risk, reconciliation required
- UBO hidden/unknown: Escalation

---

### 7.6 IP Geolocation & VPN Detection

**ipapi.co API Outputs:**
- Country (ISO 3166-1 alpha-2)
- City, latitude/longitude
- ASN (Autonomous System Number)
- VPN detection (boolean)
- Proxy detection (boolean)
- Datacenter detection (boolean)
- Threat level classification

**Risk Signals:**
```
VPN/Proxy at registration → HIGH risk
Datacenter IP → MEDIUM risk
IP mismatch with declared country → HIGH risk
High-risk country IP → Sector/country risk applied
```

---

### 7.7 Risk Dimension Calculation

**5D Risk Model:**

| Dimension | Factors | Weight | Score Range |
|-----------|---------|--------|-------------|
| Account Risk | Email validation, VPN/proxy, IP geolocation, velocity, duplicates | 20% | 0-100 |
| Sector Risk | Business sector classification, prohibited sectors | 20% | 0-100 |
| KYC Risk | Document verification, face match, liveness, sanctions on ID | 20% | 0-100 |
| Narrative Risk | Business model plausibility, source of funds clarity, red flag language | 20% | 0-100 |
| Ownership Risk | UBO identification, ownership chain, shell indicators, sanctions screening | 20% | 0-100 |

**Calculation:**
```python
final_score = (
  (account_risk * 0.20) +
  (sector_risk * 0.20) +
  (kyc_risk * 0.20) +
  (narrative_risk * 0.20) +
  (ownership_risk * 0.20)
)

if any([
  sector_risk > 95,  # Critical sector
  kyc_risk > 90,      # Document fraud
  sanctioned_entity
]):
  final_score = min(final_score, 100)  # Cap at VERY_HIGH
  risk_level = "VERY_HIGH"
```

---

### 7.8 Onboarding Workflow Status Machine

**Status Progression:**

```
┌─ DRAFT (Client fills form)
│  ↓
├─ PRESCREENING_SUBMITTED (Agents 1, 2, 4, 5 run)
│  ├─ Decision: Approved → PRICING_REVIEW
│  ├─ Decision: Escalate → COMPLIANCE_REVIEW
│  └─ Decision: Rejected → (terminal)
│
├─ PRICING_REVIEW (Compliance officer reviews risk + pricing)
│  ├─ Decision: Approve pricing → PRICING_ACCEPTED
│  └─ Decision: Adjust pricing → PRICING_REVIEW
│
├─ PRICING_ACCEPTED (Client accepts pricing)
│  ↓
├─ KYC_DOCUMENTS (Client uploads documents)
│  ↓
├─ KYC_SUBMITTED (Sumsub verification in progress)
│  ├─ Sumsub result: Approved → COMPLIANCE_REVIEW
│  └─ Sumsub result: Rejected → (terminal or request resubmit)
│
├─ COMPLIANCE_REVIEW (Agent 9 generates memo, compliance officer reviews)
│  ├─ Decision: Approve → APPROVED
│  ├─ Decision: Reject → REJECTED
│  ├─ Decision: Escalate EDD → EDD_REQUIRED
│  └─ Decision: Request docs → KYC_DOCUMENTS
│
├─ EDD_REQUIRED (Enhanced due diligence - manual investigation)
│  ├─ Decision: Approve → APPROVED
│  ├─ Decision: Reject → REJECTED
│  └─ Decision: Request info → KYC_DOCUMENTS
│
├─ IN_REVIEW (Legacy status, alternate to COMPLIANCE_REVIEW)
│  ↓ (transitions to APPROVED/REJECTED)
│
├─ APPROVED (Terminal - client onboarded)
│  ↓ Transition to monitoring
│
├─ REJECTED (Terminal - application declined)
│
├─ RMI_SENT (Terminal - risk mitigation instructions sent)
│
└─ WITHDRAWN (Terminal - client withdrew application)
```

---

### 7.9 Risk-Based Routing

**Onboarding Lane Assignment Logic:**

```
if risk_score <= 20:
  lane = "Simple"
  route = [prescreening, pricing, kyc, compliance_review, approved]
  review_required_after_kyc = false

elif risk_score <= 50:
  lane = "Standard"
  route = [prescreening, pricing, kyc, compliance_review, approved]
  review_required_after_kyc = true

elif risk_score <= 75:
  lane = "Enhanced"
  route = [prescreening, pricing, COMPLIANCE_REVIEW_PRE_KYC, kyc, compliance_review, approved]
  edd_available = true

elif risk_score > 75:
  lane = "Escalated"
  route = [prescreening, ESCALATION_TO_SCO, pricing, COMPLIANCE_REVIEW_PRE_KYC, kyc, COMPLIANCE_REVIEW_POST_KYC, approved_or_edd]
  edd_mandatory = true
  sco_approval_required = true
```

---

### 7.10 Pricing Model

Pricing tiers are determined by final risk level:

| Risk Level | Tier | Pricing | Justification |
|-----------|------|---------|-----------------|
| LOW | Standard | £500 | Minimal compliance effort required |
| MEDIUM | Enhanced | £1,500 | Standard due diligence + compliance review |
| HIGH | Premium | £3,500 | Enhanced due diligence + pre-KYC compliance review |
| VERY_HIGH | Enterprise | £5,000 | EDD mandatory, senior compliance officer required |

---

### 7.11 SAR (Suspicious Activity Report) Workflow

**Trigger Conditions (Auto-SAR):**
1. Exact sanctions match detected
2. Undeclared PEP confirmed post-verification
3. Critical adverse media findings
4. Multiple alert escalations
5. Transaction anomalies exceeding thresholds
6. Manual escalation by compliance officer

**SAR Workflow States:**

```
DRAFT → (prepare_sar endpoint) → PENDING_REVIEW
  ↓ (compliance review)
  ├─ APPROVED → (file_sar endpoint) → FILED
  ├─ REJECTED (archived, reason documented)
  └─ request_changes
      ↓
      → PENDING_REVIEW (resubmit)
```

**SAR Content Structure:**
```json
{
  "report_type": "suspected_pep|sanctions|aml|other",
  "subject_name": "Company/Individual Name",
  "subject_type": "business|individual",
  "risk_level": "low|medium|high|critical",
  "narrative": "Detailed description of suspicious activity",
  "indicators": [
    "undeclared_pep",
    "sanctions_match",
    "adverse_media",
    "transaction_anomaly"
  ],
  "transaction_details": {
    "amount": 50000,
    "currency": "GBP",
    "destination": "Iran",
    "pattern": "structured"
  },
  "supporting_documents": [501, 502, 503]
}
```

**Filing Destinations:**
- FCA (Financial Conduct Authority) - UK
- FinCEN (US Financial Crimes Enforcement Network)
- FATF-aligned AML/CFT authorities

---

## 8. Onboarding Workflow

### 8.1 Detailed Workflow Progression

**Stage 1: DRAFT → PRESCREENING_SUBMITTED**

Client fills out initial onboarding form:
- Company name, registration number, country
- Sector, entity type, ownership structure
- Business narrative (minimum 500 characters)
- Source of funds declaration
- Expected transaction volume/frequency

On submit, agents 1, 2, 4, 5, 8 execute:
- Agent 1 validates account details (email, IP, VPN)
- Agent 2 assesses sector/country risk
- Agent 4 analyzes narrative plausibility
- Agent 5 validates UBO declarations
- Agent 8 calculates final risk score + lane assignment

**Decision Point:**
- LOW/MEDIUM risk → Proceed to PRICING_REVIEW
- HIGH risk → Route to COMPLIANCE_REVIEW for pre-review
- VERY_HIGH risk → Escalate to SCO, proceed to COMPLIANCE_REVIEW

**Result Example:**
```json
{
  "status": "prescreening_submitted",
  "risk_score": 42,
  "risk_level": "MEDIUM",
  "onboarding_lane": "Standard",
  "pricing_tier": "MEDIUM",
  "pricing_amount": 1500,
  "next_step": "Review and accept pricing"
}
```

---

**Stage 2: PRICING_REVIEW → PRICING_ACCEPTED**

Compliance officer reviews:
- Risk assessment summary
- Pricing tier appropriateness
- Option to adjust pricing up/down based on manual findings

Client accepts pricing and proceeds to KYC.

---

**Stage 3: KYC_DOCUMENTS → KYC_SUBMITTED**

Client uploads documents (via Sumsub SDK or API):
- Identity documents (passport, national ID)
- Company registration documents (if applicable)
- Proof of address (if required)
- Beneficial ownership documents (director registry extract)

Agent 3 (Document Verification) executes:
- File validation (size, format, MIME type)
- OCR extraction from document images
- Document fraud detection (synthetic docs, tampering)
- Facial liveness check (selfie verification)
- Face matching (selfie vs. ID photo)
- Sanctions screening on extracted identity data

Sumsub provides:
- Document verification result (GREEN/YELLOW/RED)
- Extracted data (name, DOB, nationality, document number)
- Liveness confidence score (0-1)
- Face match confidence score (0-1)

---

**Stage 4: COMPLIANCE_REVIEW**

Agent 9 (Compliance Memo Generator) executes:
- Aggregates all screening results
- Generates structured compliance memo
- AI recommendation: approve/escalate/reject

Compliance officer reviews:
- Memo content
- All agent findings
- Manual notes/additional findings

**Decision Options:**
- **Approve:** Application meets all compliance requirements
- **Reject:** Application fails compliance criteria
- **Escalate EDD:** Request enhanced due diligence (additional investigation needed)
- **Request Documents:** Additional documentation required

**Result Example:**
```json
{
  "status": "approved",
  "decision": "approve",
  "decided_at": "2026-03-15T14:45:00Z",
  "decision_by": 1,
  "decision_notes": "LOW risk profile, all compliance checks passed. Approved for onboarding."
}
```

---

### 8.2 Pricing Tier Logic

**Calculation:**
```python
risk_level = calculate_risk_level(risk_score)

pricing_matrix = {
  "LOW": 500,
  "MEDIUM": 1500,
  "HIGH": 3500,
  "VERY_HIGH": 5000
}

pricing_amount = pricing_matrix[risk_level]

# Manual override allowed
if compliance_officer_override:
  pricing_amount = override_amount
```

**Pricing Justification:**
- Reflects compliance effort (higher risk = higher effort)
- Covers KYC, screening, document verification, compliance memo generation
- Enhanced due diligence (EDD) may incur additional fees

---

### 8.3 Application State Transitions

**Valid Transitions:**
```python
VALID_TRANSITIONS = {
  "draft": ["prescreening_submitted"],
  "prescreening_submitted": ["pricing_review", "compliance_review", "withdrawn"],
  "pricing_review": ["pricing_accepted", "withdrawn"],
  "pricing_accepted": ["kyc_documents", "withdrawn"],
  "kyc_documents": ["kyc_submitted", "withdrawn"],
  "kyc_submitted": ["compliance_review", "withdrawn"],
  "compliance_review": ["approved", "rejected", "edd_required", "kyc_documents"],
  "edd_required": ["approved", "rejected", "kyc_documents"],
  "in_review": ["approved", "rejected", "edd_required"],
  "approved": [],  # Terminal
  "rejected": [],  # Terminal
  "rmi_sent": [],  # Terminal
  "withdrawn": []   # Terminal
}
```

**Validation:**
```python
def transition_application(app_id, from_status, to_status):
  if to_status not in VALID_TRANSITIONS.get(from_status, []):
    raise InvalidTransitionError(f"{from_status} → {to_status}")
  # Execute transition
  audit_log_transition(app_id, from_status, to_status)
```

---

### 8.4 Document Upload Workflow

**Upload Endpoint:** `POST /api/applications/:id/documents`

**Validation Pipeline:**
1. File size check (max 10MB)
2. MIME type validation (PDF, JPEG, PNG only)
3. Filename sanitization
4. Virus scan (if enabled)
5. Store file outside web root
6. Create document record in database

**Document Verification Endpoint:** `POST /api/documents/:id/verify`

**Verification Steps:**
1. Trigger Sumsub document verification
2. Poll Sumsub API for results (with exponential backoff)
3. Update document verification status
4. Extract OCR data
5. Run sanctions screening on extracted data
6. Generate verification results summary

---

### 8.5 Compliance Memo Structure

**Auto-Generated by Agent 9:**

```markdown
# ARIE Compliance Memo

## Executive Summary
**Application ID:** ARF-2026-00101
**Risk Level:** MEDIUM
**Recommendation:** APPROVE
**Prepared by:** AI Agent 9
**Date:** 2026-03-15

---

## Client Overview
**Company Name:** Acme Corp
**Registration Number:** 12345678
**Country:** United Kingdom
**Sector:** FinTech (Level 2 Risk)
**Entity Type:** Limited Company
**Onboarding Lane:** Standard

---

## Beneficial Ownership & Control
| Name | Role | Nationality | Ownership % | PEP Status |
|------|------|-------------|-------------|-----------|
| John Doe | Director & UBO | GB | 60% | No |
| Jane Smith | Director & UBO | GB | 40% | No |

**Ownership Chain:** Direct ownership, no shell entities identified.

---

## Screening Results
- **Sanctions Screening:** CLEAR (No matches)
- **PEP Detection:** NEGATIVE (No matches)
- **Adverse Media:** CLEAR
- **Company Registry:** VERIFIED (Active, Companies House)

---

## Document Verification
- **Passport (John Doe):** VERIFIED, GREEN
- **Passport (Jane Smith):** VERIFIED, GREEN
- **Liveness Checks:** PASSED (both)
- **Face Matching:** PASSED (both)

---

## Risk Assessment

### 5D Risk Score
- Account Risk: 25 (LOW)
- Sector Risk: 50 (MEDIUM)
- KYC Risk: 30 (LOW)
- Narrative Risk: 45 (MEDIUM)
- Ownership Risk: 40 (MEDIUM)

**Final Score:** 42/100 (MEDIUM)

---

## Source of Funds Assessment
**Declared Source:** Retained earnings from previous operations over 5 years
**Assessment:** Plausible, consistent with industry experience, documented with financial statements
**Risk Level:** LOW

---

## AI Recommendation
Based on comprehensive screening and compliance assessment, **RECOMMEND APPROVAL** for onboarding.

**Rationale:**
1. Medium risk profile with no critical flags
2. Clear beneficial ownership and no shell structures
3. All identity documents verified with liveness confirmation
4. Transparent source of funds with supporting documentation
5. Business model plausible for sector

---

## Compliance Officer Notes
[Manual additions by reviewing officer]

---

## Decision
**Decision:** APPROVE
**Approved by:** Officer Name
**Date:** 2026-03-15
**Notes:** Recommend standard pricing tier (£1,500).
```

---

### 8.6 Application Save/Resume

**Purpose:** Allow clients to pause and resume long application forms

**Auto-Save Mechanism:**
- Client browser saves form data to `/api/save-resume` every 30 seconds
- Server stores in database under application_id
- On page reload, client fetches saved data and re-populates form

**Implementation:**
```javascript
// Client-side (portal)
setInterval(() => {
  const data = getFormData();
  fetch('/api/save-resume', {
    method: 'POST',
    body: JSON.stringify({ application_id, data }),
    headers: { 'Authorization': `Bearer ${token}` }
  });
}, 30000);
```

---

## 9. Monitoring Workflow

### 9.1 Post-Onboarding Monitoring

Once application is **APPROVED**, client transitions to ongoing monitoring:

**Monitoring Agents:** 6, 10 (Financial Crime Intelligence, Ongoing Monitoring)

**Monitoring Frequency:**
- Sanctions screening: Daily for all clients
- Transaction monitoring: Real-time (as transactions are reported)
- Registry monitoring: Weekly
- Adverse media: Daily
- Periodic reviews: Scheduled based on risk level

---

### 9.2 Monitoring Alert System

**Alert Types:**

| Alert Type | Trigger | Severity | Action |
|-----------|---------|----------|--------|
| sanctions_match | Client/UBO matches sanctions list | Critical | Auto-SAR |
| pep_status_change | PEP status changes post-onboarding | Critical | Escalate |
| adverse_media | Negative news about client | High | Review |
| transaction_anomaly | Unusual transaction pattern | High | Review |
| risk_drift | Client risk profile increases | Medium | Monitor |
| registry_change | Company registration changes | Medium | Review |
| geographic_shift | New jurisdiction exposure | Low | Note |

**Alert Workflow:**

```
Alert Triggered
  ↓
Auto-assign severity
  ↓
Store in monitoring_alerts table
  ↓
Compliance officer reviews
  ↓
Decision:
  ├─ Dismiss (documented reason)
  ├─ Escalate (trigger SAR or enhanced monitoring)
  └─ Monitor (periodic follow-up)
```

---

### 9.3 Periodic Review Scheduling

**Review Frequency by Risk Level:**

| Risk Level | Frequency | Notification |
|-----------|-----------|--------------|
| LOW | Biennial (24 months) | 30 days before due |
| MEDIUM | Annual (12 months) | 14 days before due |
| HIGH | Semi-annual (6 months) | 7 days before due |
| VERY_HIGH | Quarterly (3 months) | 3 days before due |

**Periodic Review Process:**

```
1. Scheduled review triggers
2. Create periodic_reviews record
3. Assign to compliance officer
4. Officer reviews:
   - Recent alerts
   - Transaction patterns
   - Sanctions updates
   - Adverse media
   - Risk changes
5. Officer documents findings
6. Officer decides:
   - CONTINUE: No changes needed
   - ENHANCED_MONITORING: Increase monitoring frequency
   - REQUEST_INFO: Additional information required
   - EXIT_RELATIONSHIP: Terminate client relationship
```

---

### 9.4 SAR Auto-Trigger Conditions

Suspicious Activity Reports are automatically triggered if:

1. **Critical Sanctions Match:**
   ```
   sanctions_confidence > 0.95 AND
   sanctions_source IN ('OFAC', 'UN', 'EU', 'UK')
   ```

2. **Undeclared PEP Confirmed:**
   ```
   declared_pep = false AND
   detected_pep_confidence > 0.80
   ```

3. **Multiple Critical Alerts:**
   ```
   critical_alerts_count >= 2 AND
   time_period = "30 days"
   ```

4. **Transaction Threshold Exceeded:**
   ```
   total_transactions_to_high_risk_jurisdiction > threshold AND
   risk_level = 'VERY_HIGH'
   ```

---

### 9.5 Monitoring Agent Implementation

**Agent 10 (Ongoing Monitoring) Execution:**

```python
def run_monitoring_agent(client_id):
  client = get_client(client_id)

  # Check 1: Sanctions re-screening
  sanctions_result = screen_sanctions(
    client.name,
    client.ubos,
    client.directors
  )
  if sanctions_result.matches:
    create_alert(client_id, 'sanctions_match', 'critical')

  # Check 2: Transaction monitoring
  transactions = get_recent_transactions(client_id)
  anomalies = detect_anomalies(transactions)
  if anomalies:
    create_alert(client_id, 'transaction_anomaly', 'high')

  # Check 3: Company registry check
  registry_data = fetch_company_registry(client.company_number)
  if registry_data.status != client.company_status:
    create_alert(client_id, 'registry_change', 'medium')

  # Check 4: Adverse media scan
  adverse_articles = search_adverse_media(
    client.company_name,
    client.ubos,
    client.directors
  )
  if adverse_articles:
    create_alert(client_id, 'adverse_media', 'high')

  # Check 5: Risk drift detection
  current_risk = recalculate_risk(client_id)
  if current_risk > client.risk_score + DRIFT_THRESHOLD:
    create_alert(client_id, 'risk_drift', 'medium')
```

---

### 9.6 Alert Management UI

**Compliance Officer Alert Dashboard:**

```json
{
  "total_alerts": 23,
  "critical_open": 5,
  "high_open": 12,
  "medium_open": 6,
  "alerts": [
    {
      "id": 5001,
      "client_id": 101,
      "company_name": "Acme Corp",
      "alert_type": "sanctions_match",
      "severity": "critical",
      "triggered_at": "2026-03-15T10:30:00Z",
      "details": {
        "match_type": "exact",
        "source": "OFAC",
        "confidence": 0.97,
        "matched_entity": "Acme Corp Trading Ltd"
      },
      "ai_recommendation": "FILE_SAR",
      "status": "open"
    }
  ]
}
```

**Compliance Officer Actions on Alert:**

1. **Dismiss:**
   - Record reason (false positive, client clarified, etc.)
   - Audit logged

2. **Escalate:**
   - Immediate escalation to senior compliance
   - Generate preliminary SAR draft
   - Notify client (if appropriate)

3. **Trigger Periodic Review:**
   - Create ad-hoc periodic review
   - Assign to senior officer
   - Expedite review timeline

---

## 10. Error Handling & Resilience

### 10.1 Graceful API Fallbacks

**Simulated Mode for External APIs:**

If any external API is unavailable or returns error:

```python
try:
  result = opensanctions.search(name, dob)
except (APITimeout, ConnectionError, HTTPError):
  # Fallback to simulated result
  logger.warning(f"OpenSanctions API failed, using simulated mode")
  result = simulate_sanctions_screening(name, dob)
  result['source'] = 'SIMULATED'
  # Application can proceed but flagged for follow-up
  create_alert(
    application_id,
    alert_type='api_unavailable',
    message='Sanctions screening unavailable, using cached results'
  )
```

**Supported Simulated Modes:**
- OpenSanctions: Returns cached results or CLEAR status
- OpenCorporates: Returns basic company data or NOT_FOUND
- Sumsub: Returns PENDING status for documents
- ipapi.co: Returns generic geolocation
- Claude/OpenAI: Returns templated responses

---

### 10.2 ThreadPoolExecutor Timeout Handling

**Configuration:**
```python
executor = ThreadPoolExecutor(max_workers=30)
EXECUTOR_TIMEOUT = 30  # seconds

def run_parallel_screening(application_id, agents):
  futures = []
  for agent in agents:
    future = executor.submit(agent.run, application_id)
    futures.append(future)

  results = []
  for future in as_completed(futures, timeout=EXECUTOR_TIMEOUT):
    try:
      result = future.result(timeout=1)
      results.append(result)
    except TimeoutError:
      logger.error(f"Agent execution timeout")
      results.append({
        'status': 'timeout',
        'message': 'Agent execution exceeded 30-second limit'
      })
    except Exception as e:
      logger.error(f"Agent execution error: {e}")
      results.append({
        'status': 'error',
        'message': str(e)
      })

  return results
```

---

### 10.3 Rate Limiting Implementation

**Sliding Window Algorithm:**

```python
class RateLimiter:
  def __init__(self, max_attempts, window_seconds):
    self.max_attempts = max_attempts
    self.window_seconds = window_seconds
    self.requests = {}  # {identifier: [(timestamp, count)]}

  def is_allowed(self, identifier):
    now = time.time()

    if identifier not in self.requests:
      self.requests[identifier] = []

    # Remove old requests outside window
    self.requests[identifier] = [
      (ts, count) for ts, count in self.requests[identifier]
      if now - ts < self.window_seconds
    ]

    # Count requests in window
    total = sum(count for _, count in self.requests[identifier])

    if total < self.max_attempts:
      self.requests[identifier].append((now, 1))
      return True
    return False

# Usage
login_limiter = RateLimiter(max_attempts=10, window_seconds=900)  # 10/15min

if not login_limiter.is_allowed(client_ip):
  return 429 Too Many Requests
```

---

### 10.4 Database Concurrency & WAL Mode

**SQLite WAL (Write-Ahead Logging):**

```python
db.execute("PRAGMA journal_mode=WAL")
```

**Benefits:**
- Multiple concurrent readers allowed
- Single writer maintains ACID
- Reduced lock contention
- Automatic checkpoint management

**Configuration for High Concurrency:**
```python
db.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint every 1000 pages
db.execute("PRAGMA synchronous=NORMAL")        # Balance safety/performance
```

---

### 10.5 Input Sanitization & XSS Prevention

**HTML Escape All User Input:**

```python
import html

def sanitize_input(data):
  if isinstance(data, str):
    return html.escape(data)
  elif isinstance(data, dict):
    return {k: sanitize_input(v) for k, v in data.items()}
  elif isinstance(data, list):
    return [sanitize_input(item) for item in data]
  return data

# Middleware application
class BaseHandler(RequestHandler):
  def data_received(self, chunk):
    try:
      self.request_body = json.loads(chunk)
      self.request_body = sanitize_input(self.request_body)
    except json.JSONDecodeError:
      self.set_status(400)
      self.finish({'error': 'Invalid JSON'})
```

**SQL Injection Prevention:**

```python
# WRONG - DO NOT DO THIS
cursor.execute(f"SELECT * FROM applications WHERE id = {app_id}")

# CORRECT - Parameterized queries
cursor.execute("SELECT * FROM applications WHERE id = ?", (app_id,))

# PostgreSQL
cursor.execute("SELECT * FROM applications WHERE id = %s", (app_id,))
```

---

### 10.6 Structured Error Responses

**Standard Error Response Format:**

```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "Email format is invalid",
    "http_status": 400,
    "timestamp": "2026-03-15T14:30:00Z",
    "request_id": "req_abc123def456",
    "details": {
      "field": "email",
      "reason": "Does not match email regex"
    }
  }
}
```

**Error Codes:**

| Code | HTTP Status | Description |
|------|-------------|-------------|
| INVALID_INPUT | 400 | Validation error |
| UNAUTHORIZED | 401 | Authentication failed |
| FORBIDDEN | 403 | Authorization failed |
| NOT_FOUND | 404 | Resource not found |
| CONFLICT | 409 | Duplicate or state conflict |
| RATE_LIMITED | 429 | Too many requests |
| UNPROCESSABLE_ENTITY | 422 | Request cannot be processed |
| INTERNAL_ERROR | 500 | Server error |
| SERVICE_UNAVAILABLE | 503 | External API unavailable |

---

### 10.7 Request ID Tracking

**Request ID Generation & Propagation:**

```python
import uuid

class BaseHandler(RequestHandler):
  def prepare(self):
    # Generate or extract request ID
    self.request_id = self.request.headers.get(
      'X-Request-ID',
      str(uuid.uuid4())
    )

    # Propagate to all downstream calls
    self.set_header('X-Request-ID', self.request_id)

    # Log with request ID
    logger.info(
      f"[{self.request_id}] {self.request.method} {self.request.path}",
      extra={'request_id': self.request_id}
    )
```

---

### 10.8 Health Check Resilience

**Periodic Health Verification:**

```python
async def health_check():
  checks = {
    'database': await check_database(),
    'opensanctions_api': await check_opensanctions(),
    'opencorporates_api': await check_opencorporates(),
    'sumsub_api': await check_sumsub(),
    'ipapi': await check_ipapi(),
    'disk_space': check_disk_space(),
    'memory': check_memory_usage()
  }

  all_healthy = all(check['status'] == 'ok' for check in checks.values())

  return {
    'status': 'healthy' if all_healthy else 'degraded',
    'checks': checks,
    'timestamp': datetime.utcnow().isoformat()
  }

# Endpoint
class HealthHandler(BaseHandler):
  async def get(self):
    health = await health_check()
    status = 200 if health['status'] == 'healthy' else 503
    self.set_status(status)
    self.write(health)
```

---

### 10.9 Audit Logging for Compliance

**Comprehensive Audit Trail:**

```python
def audit_log(user_id, user_name, user_role, action, target, detail, ip_address):
  cursor = db.cursor()
  cursor.execute("""
    INSERT INTO audit_log
    (timestamp, user_id, user_name, user_role, action, target, detail, ip_address)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  """, (
    datetime.utcnow(),
    user_id,
    user_name,
    user_role,
    action,
    target,
    detail,
    ip_address
  ))
  db.commit()
```

**Audit Trigger Points:**
- User login/logout
- Application status changes
- Compliance decisions
- Document uploads
- Screening executions
- SAR filings
- Configuration changes
- User account modifications
- Failed authorization attempts

---

### 10.10 Graceful Shutdown & Signal Handling

**Signal Handling:**

```python
import signal

def shutdown_handler(signum, frame):
  logger.info("Shutdown signal received, gracefully shutting down...")

  # Stop accepting new requests
  server.stop()

  # Wait for in-flight requests to complete (max 30 seconds)
  for _ in range(30):
    if server.active_requests == 0:
      break
    time.sleep(1)

  # Close database connections
  db.close()

  # Close thread pool
  executor.shutdown(wait=True)

  logger.info("Shutdown complete")
  sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)
```

---

## 11. Appendix: Compliance Frameworks

### 11.1 Regulatory References

**Standards & Regulations:**
- Anti-Money Laundering Directive (AMLD5/6) - EU
- Money Laundering Regulations 2017 - UK
- Bank Secrecy Act (BSA) - USA
- Financial Action Task Force (FATF) Recommendations
- Know Your Customer (KYC) Standards
- Customer Due Diligence (CDD) Requirements
- Enhanced Due Diligence (EDD) for High-Risk Customers

### 11.2 API Key Management

**Environment Variable Setup:**

```bash
# Production deployment
export SECRET_KEY="your-32-char-minimum-secret-key"
export DATABASE_URL="postgresql://user:pass@host:5432/arie"
export ENVIRONMENT="production"
export ALLOWED_ORIGIN="https://arie-portal.example.com"

# API Integrations
export OPENSANCTIONS_API_KEY="your-key"
export OPENCORPORATES_API_KEY="your-key"
export SUMSUB_API_KEY="your-key"
export IPAPI_API_KEY="your-key"
export OPENAI_API_KEY="your-key"  # For memo generation
```

### 11.3 Testing Credentials

**Development Test Accounts:**
- Officer: officer@arie.co.uk / password123
- Analyst: analyst@arie.co.uk / password123
- Client: client@example.com / password123

### 11.4 Common Integration Points

1. **AML/CFT Reporting:** SAR filing to FCA/FinCEN
2. **Customer Communication:** Notification system for document requests
3. **External Data Sources:** Registry lookups, sanctions updates
4. **Document Management:** Encrypted storage, access audit
5. **Analytics & Reporting:** Risk metrics, compliance dashboard

---

## Document Control

**Version History:**
- v1.0 (2026-03-15): Initial technical dossier

**Reviewed By:**
- System Architect
- Compliance Officer
- Security Lead

**Next Review Date:** 2026-06-15

**Classification:** Technical Reference - Internal Use

---

*End of ARIE Finance RegTech Platform Technical Dossier*

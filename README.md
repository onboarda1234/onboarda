# Onboarda

**AI-powered compliance onboarding for regulated financial institutions.**

Onboarda automates KYC/AML due diligence for banks, EMIs, and payment providers using a deterministic multi-layer AI pipeline. It combines rule-based risk scoring, external screening APIs, and Claude AI–generated compliance memos into a single auditable workflow.

The platform ships as two branded surfaces:

- **Onboarda** — Client-facing portal where applicants submit documents and forms
- **RegMind** — Internal back-office where compliance officers review, approve, or reject applications

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 / [Tornado](https://www.tornadoweb.org/) |
| Database | PostgreSQL (production) · SQLite (local dev) |
| AI | [Anthropic Claude](https://www.anthropic.com/) (risk-based Sonnet/Opus routing for memo generation) |
| KYC Provider | [Sumsub](https://sumsub.com/) (identity verification, AML/PEP screening) |
| Document Storage | AWS S3 |
| PDF Generation | [WeasyPrint](https://weasyprint.org/) |
| Frontend | Vanilla JS — single-file HTML (no build step) |
| CI/CD | GitHub Actions |
| Deployment | AWS ECS Fargate af-south-1 (staging + production) · [Render.com](https://render.com/) (demo) |

---

## Repository Structure

```
onboarda/
├── arie-backend/                  # Python backend
│   ├── server.py                  # Main API server (all endpoints)
│   ├── claude_client.py           # Claude AI integration (10 compliance agents)
│   ├── db.py                      # Database layer (SQLite + PostgreSQL)
│   ├── rule_engine.py             # Country/sector risk scoring (FATF lists)
│   ├── memo_handler.py            # Compliance memo generation
│   ├── validation_engine.py       # 15-point memo quality auditor
│   ├── supervisor_engine.py       # Final review & contradiction detection
│   ├── document_verification.py   # Layered verification pipeline
│   ├── verification_matrix.py     # Document check definitions
│   ├── screening.py               # AML/PEP screening orchestration
│   ├── sumsub_client.py           # Sumsub API client
│   ├── auth.py                    # JWT auth & rate limiting
│   ├── base_handler.py            # Tornado base handler (CORS, CSRF, headers)
│   ├── security_hardening.py      # Approval gates, PII encryption
│   ├── branding.py                # Centralised brand config
│   ├── environment.py             # Feature flags & env detection
│   ├── config.py                  # Environment variable config
│   ├── pdf_generator.py           # Compliance PDF reports
│   ├── gdpr.py                    # GDPR data retention & purge
│   ├── production_controls.py     # Rate limiting & budget monitoring
│   ├── observability.py           # Structured logging
│   ├── s3_client.py               # AWS S3 document management
│   ├── demo_pilot_data.py         # Demo seed data (5 risk-tier scenarios)
│   ├── prescreening/              # Pre-screening field definitions
│   ├── resilience/                # Circuit breaker, retry, resilient HTTP
│   ├── supervisor/                # Advanced AI agent execution & supervision
│   ├── migrations/                # SQL schema migrations
│   ├── tests/                     # Test suite
│   ├── Dockerfile                 # Container build
│   ├── docker-compose.yml         # Backend + PostgreSQL stack
│   └── requirements.txt           # Pinned Python dependencies
├── arie-portal.html               # Client portal UI
├── arie-backoffice.html           # RegMind back-office UI
├── index.html                     # Marketing landing page
├── render.yaml                    # Render.com deployment blueprint
├── docs/                          # Compliance, investor & sprint docs
└── decks/                         # Marketing & pitch decks
```

---

## AI Pipeline

Applications flow through a deterministic 4-layer pipeline:

```
Applicant submits via Portal
            │
            ▼
┌───────────────────────────────┐
│  1. Rule Engine               │  Deterministic risk scoring
│     (country, sector, FATF)   │  (no AI)
└───────────┬───────────────────┘
            ▼
┌───────────────────────────────┐
│  2. Screening                 │  Sumsub AML/PEP,
│     (external APIs)           │  sanctions & registry lookups
└───────────┬───────────────────┘
            ▼
┌───────────────────────────────┐
│  3. AI Memo Generation        │  Risk-based routing for memo generation:
│     (10 agents, 11 sections)  │  Sonnet (LOW/MEDIUM), Opus (HIGH/VERY_HIGH)
└───────────┬───────────────────┘
            ▼
┌───────────────────────────────┐
│  4. Validation & Supervisor   │  15-point quality audit +
│     (contradiction detection) │  11-check consistency review
└───────────┬───────────────────┘
            ▼
    PDF Report → Back-office Review → Decision
```

**The 10 AI Agents:**

**Onboarding Agents (1–5):**

1. **Identity & Document Integrity** — OCR, document validation, cross-document consistency
2. **External Database Cross-Verification** — Registry lookups, corporate verification
3. **FinCrime Screening Interpretation** — Sanctions/PEP analysis, false-positive reduction
4. **Corporate Structure & UBO Mapping** — Ownership chains, nominee detection
5. **Compliance Memo & Risk Recommendation** — Composite scoring, final memo generation

**Monitoring Agents (6–10):**

6. **Periodic Review Preparation** — Officer-triggered review scheduling, state machine, and data collection (automatic scheduler not yet implemented)
7. **Adverse Media & PEP Monitoring** — PEP screening and adverse-media signal parsing from Sumsub results (external adverse-media API call not yet implemented)
8. **Behaviour & Risk Drift** — Transaction pattern analysis and risk drift detection
9. **Regulatory Impact** — Regulatory change assessment for existing clients
10. **Ongoing Compliance Review** — Continuous compliance posture evaluation

> **Note**: Monitoring agents 6–10 are scaffolded with full state machines, endpoints, and audit trails. Automatic scheduling (agent 6) and real-time external adverse-media calls (agent 7) are not yet implemented. The active screening provider is Sumsub; ComplyAdvantage is a registered alternative provider behind a disabled feature flag (`ENABLE_SCREENING_ABSTRACTION=false` by default).

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 16 (production) or SQLite (local development)

### Local Development

```bash
# Install dependencies
cd arie-backend
pip install -r requirements.txt

# Start the server (defaults to SQLite in demo mode)
python server.py
```

The backend runs on port **10000** by default (local development, AWS ECS, and Render). Open `arie-portal.html` and `arie-backoffice.html` directly in a browser (or serve via any static file server) — no build step required.

### Docker

```bash
cd arie-backend

# Build and start backend + PostgreSQL
docker compose up -d

# View logs
docker compose logs -f backend
```

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ENVIRONMENT` / `ENV` | `production` · `demo` · `staging` · `development` (CI uses `testing`, which falls back to `demo`) | No (defaults to `demo`) |
| `SECRET_KEY` | Server secret key | Yes (production) |
| `JWT_SECRET` | JWT signing secret | Yes (production) |
| `DATABASE_URL` | PostgreSQL connection string | Yes (production) |
| `PII_ENCRYPTION_KEY` | Base64-encoded Fernet key for PII encryption | Yes (production) |
| `ANTHROPIC_API_KEY` | Claude API key | Yes (for AI features) |
| `SUMSUB_APP_TOKEN` | Sumsub app token | Yes (for live KYC) |
| `SUMSUB_SECRET_KEY` | Sumsub secret key | Yes (for live KYC) |
| `SUMSUB_WEBHOOK_SECRET` | Sumsub webhook signature secret | Yes (production) |
| `S3_BUCKET` | AWS S3 bucket name | Yes (for document storage) |
| `AWS_ACCESS_KEY_ID` | AWS access key | Yes (for S3) |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | Yes (for S3) |

In demo mode, all external APIs are simulated — no keys required.

---

## Running Tests

```bash
cd arie-backend
python -m pytest tests/ -v
```

The test suite spans dozens of test files covering API endpoints, authentication, risk scoring, validation, supervision, PDF generation, GDPR, integration flows, and more.

### CI Enforcement

The GitHub Actions CI pipeline (`.github/workflows/ci.yml`) enforces:

- **Syntax check** — all Python files must compile
- **Linting** — Flake8 error-only checks (`E9`, `F63`, `F7`, `F82`)
- **Minimum 150 tests** must pass
- **≥ 25% code coverage** threshold
- **Docker build + smoke test** — container starts, health endpoint responds with valid JSON and security headers

---

## Deployment

### Active Environments (Verified May 2026)

| Environment | Platform | Domain | Status |
|-------------|----------|--------|--------|
| **Staging** | AWS ECS Fargate (af-south-1) | staging.regmind.co | ✅ Active — validated |
| **Demo** | Render.com | demo.regmind.co | ✅ Active |
| **Production** | AWS ECS Fargate (af-south-1) | app.regmind.co | ⏳ Planned — DNS not yet provisioned |

Staging and production share the same AWS ECS infrastructure (`regmind-staging` cluster, af-south-1 region). The `deploy-staging.yml` GitHub Actions workflow deploys to staging on every push to `main`. Production (`app.regmind.co`) will use the same pipeline once DNS and ECS service are provisioned.

Demo is deployed separately to Render.com (`arie-finance-demo`) with simulated APIs and seed data. It auto-deploys from `main` via the `render.yaml` blueprint.

> **Note:** The `render.yaml` blueprint also defines an `arie-finance-live` service but this is **not the active production environment**. Production is on AWS ECS. The Render live service is currently suspended.

### Feature Flags

The platform uses **20+ feature flags** (defined in `environment.py`) to control behavior per environment:

| Flag | Production | Demo |
|------|-----------|------|
| `ENABLE_DEMO_MODE` | `false` | `true` |
| `ENABLE_AI_SUPERVISOR` | Dashboard-managed | `true` |
| `ENABLE_DOCUMENT_AI_ANALYSIS` | Dashboard-managed | `true` |
| `ENABLE_REAL_SCREENING` | `true` | `false` |
| `ENABLE_SIMULATED_SCREENING` | `false` | `true` |
| `ENABLE_DEBUG_ENDPOINTS` | `false` | `true` |
| `ENABLE_SHORTCUT_LOGIN` | `false` | `true` |

---

## Branding

All brand names are config-driven via `branding.py` — never hardcode brand strings.

```python
from branding import BRAND

BRAND["portal_name"]      # "Onboarda"  (client-facing)
BRAND["backoffice_name"]  # "RegMind"   (internal)
BRAND["system_id"]        # "regmind"   (logs, metrics)
```

---

## Documentation

| Directory | Contents |
|-----------|----------|
| `docs/compliance/` | Audit reports, security hardening, remediation |
| `docs/investor/` | Due diligence, valuations, product spec |
| `docs/sprint-reports/` | Sprint 1–4 exit reports |
| `docs/commercial/` | Demo scripts, pilot proposals, cost model |
| `docs/DEPLOYMENT_RUNBOOK.md` | Deployment procedures and troubleshooting |
| `decks/` | Marketing and pitch presentations |

---

## License

Proprietary — © 2026 Onboarda Ltd. All rights reserved.

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
| AI | [Anthropic Claude](https://www.anthropic.com/) (Sonnet for LOW/MEDIUM risk, Opus for HIGH/VERY_HIGH) |
| KYC Provider | [Sumsub](https://sumsub.com/) (identity verification, AML/PEP screening) |
| Document Storage | AWS S3 |
| PDF Generation | [WeasyPrint](https://weasyprint.org/) |
| Frontend | Vanilla JS — single-file HTML (no build step) |
| CI/CD | GitHub Actions |
| Deployment | [Render.com](https://render.com/) |

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
│  3. AI Memo Generation        │  Claude Sonnet (LOW/MEDIUM)
│     (10 agents, 11 sections)  │  Claude Opus  (HIGH/VERY_HIGH)
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

6. **Periodic Review Preparation** — Scheduled review triggers and data collection
7. **Adverse Media & PEP Monitoring** — Continuous media and PEP screening
8. **Behaviour & Risk Drift** — Transaction pattern analysis and risk drift detection
9. **Regulatory Impact** — Regulatory change assessment for existing clients
10. **Ongoing Compliance Review** — Continuous compliance posture evaluation

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

The backend runs on port **8080** by default for local development. On Render, `PORT=10000` is set via `render.yaml`. Open `arie-portal.html` and `arie-backoffice.html` directly in a browser (or serve via any static file server) — no build step required.

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

Deployment is managed via **Render.com** using the `render.yaml` blueprint at the repo root. Two services are defined:

| Service | Environment | Auto-deploy | External APIs |
|---------|------------|-------------|--------------|
| `arie-finance-live` | Production | On push to `main` (CI-gated) | Real (Sumsub, S3, Claude) |
| `arie-finance-demo` | Demo | On push to `main` | Simulated / sandbox |

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

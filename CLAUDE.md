# CLAUDE.md — Onboarda Project Context

## Project Overview

Onboarda is an AI-powered compliance onboarding platform for regulated financial institutions (banks, EMIs, payment providers). It automates KYC/AML due diligence with a deterministic 4-layer AI pipeline.

**Two branded surfaces:**
- **Onboarda** — Client-facing portal where applicants submit documents and forms
- **RegMind** — Internal back-office compliance engine for compliance officers

## Architecture

### Tech Stack
- **Backend**: Python 3 / Tornado web framework
- **Database**: PostgreSQL (SQLite for local dev)
- **AI**: Anthropic Claude API (document verification & agent analysis; live memo generation is deterministic — see Model Routing note below)
- **KYC / IDV Provider**: Sumsub (individual identity verification)
- **AML Screening Provider**: ComplyAdvantage Mesh when `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true`
- **Hosting**: AWS ECS Fargate af-south-1 (staging + planned production) · Render.com (demo only)
- **CI/CD**: GitHub Actions (`.github/workflows/ci.yml`)
- **Frontend**: Single-file HTML (no build step)

### AI Pipeline (4-layer deterministic)
1. **Rule Engine** (`rule_engine.py`) — Risk scoring, regulatory rule checks
2. **Memo Generation** (`memo_handler.py`) — Deterministic compliance memo builder (no LLM in the live path; memo metadata carries `ai_source: "deterministic"`)
3. **Validation Engine** (`validation_engine.py`) — Cross-checks memo against rules
4. **Supervisor** (`supervisor_engine.py`) — Final review, approve/reject/escalate

### Risk-Based Model Routing (Optional Claude Memo Integration — OFF by default)
The live memo path is deterministic (`memo_handler.py`). A draft Claude memo
integration exists (`claude_memo_integration.py`, gated by `ENABLE_CLAUDE_MEMO`,
unset in all environments) that wraps `claude_client.generate_compliance_memo`,
which routes LOW/MEDIUM risk → Claude Sonnet and HIGH/VERY_HIGH risk → Claude
Opus (`claude_client.select_memo_model`). Do not enable it without wiring its
output through the validation and supervisor gates (see PC-4 in
`docs/audits/pr661_remediation_production_conditions.md`).

## Repository Structure

```
onboarda/
├── arie-backend/           # Python backend (Tornado)
│   ├── server.py           # Main server (~4000 lines, all API endpoints)
│   ├── branding.py         # Centralised branding config (BRAND dict)
│   ├── auth.py             # Authentication
│   ├── claude_client.py    # Claude API integration
│   ├── rule_engine.py      # Risk assessment rules
│   ├── memo_handler.py     # Compliance memo generation
│   ├── validation_engine.py # Memo validation
│   ├── supervisor_engine.py # Final review layer
│   ├── sumsub_client.py    # Sumsub KYC integration
│   ├── screening.py        # AML/PEP screening
│   ├── pdf_generator.py    # PDF report generation
│   ├── security_hardening.py # Security middleware
│   ├── observability.py    # Logging & monitoring
│   ├── gdpr.py             # GDPR compliance
│   ├── config_loader.py    # Environment config
│   ├── db.py               # Database layer
│   ├── demo_pilot_data.py  # Demo seed data
│   ├── Dockerfile          # Container build
│   ├── docker-compose.yml  # Local dev setup
│   └── tests/              # 5,200+ tests (pytest)
│       ├── test_api.py
│       ├── test_application.py
│       ├── test_auth.py
│       ├── test_rule_engine.py
│       ├── test_validation_engine.py
│       ├── test_supervisor.py
│       ├── test_pdf_generator.py
│       ├── test_integration.py
│       └── ...
├── arie-backoffice.html    # RegMind back-office UI (~550KB single file)
├── arie-portal.html        # Onboarda client portal UI
├── index.html              # Landing page
├── render.yaml             # Render.com blueprint (2 services)
├── docs/
│   ├── investor/           # Due diligence, valuations, product spec
│   ├── compliance/         # Audit reports, hardening, remediation
│   ├── sprint-reports/     # Sprint 1-4 exit reports
│   └── commercial/         # Demo scripts, pilot proposals, cost model
├── decks/                  # Marketing & pitch decks
├── data/                   # Demo video
├── archive/                # Old ARIE-branded files (gitignored)
└── scripts/                # JS generator scripts (gitignored)
```

## Key Configuration

### Branding (`branding.py`)
All branding is config-driven via the `BRAND` dict. Never hardcode brand names.
- External/client-facing: "Onboarda"
- Internal/back-office: "RegMind"
- Domain: `onboarda.com` (NOT `ariefinance.mu` — fully migrated)

### Environment Variables
```
DATABASE_URL          # PostgreSQL connection string
ANTHROPIC_API_KEY     # Claude API key
SUMSUB_APP_TOKEN      # Sumsub app token
SUMSUB_SECRET_KEY     # Sumsub secret key
ENVIRONMENT           # "production" | "demo" | "development"
DEMO_MODE             # "true" for demo environment
```

### Demo Credentials
- Email: `asudally@onboarda.com`
- Passwords: Set via environment variables (`DEMO_PORTAL_PASSWORD`, `DEMO_BACKOFFICE_PASSWORD`, `DEMO_CLIENT_PASSWORD`) in Render dashboard or `.env`

## Document Sections (Back Office)

The back office displays documents in 4 sections:
- **Section A** — Corporate Entity Documents (Certificate of Incorporation, etc.)
- **Section B** — Directors, UBOs & Intermediary Shareholders KYC Documents (per-person: passport, proof of address, etc.)
- **Section C** — Business Documents (Business Plan, Tax Clearance, etc.)
- **Section D** — Other Documents (Legal Opinion, Compliance Certification, etc.)

Each document has AI verification checks: Format, Authenticity, Expiry, Name Match, Tampering.

## Running Locally

```bash
cd arie-backend
pip install -r requirements.txt
python server.py
```

Backend runs on port 10000. Portal and back office are static HTML — open directly in browser or serve via any static server.

## Running Tests

```bash
cd arie-backend
python -m pytest tests/ -v
```

All tests should pass (5,222 passed, 17 skipped as of 2026-06-11). Tests cover API endpoints, authentication, rule engine, validation engine, supervisor, PDF generation, GDPR, and integration flows.

## Deployment

### Active Environments (Verified May 2026)

| Environment | Platform | Domain | Status |
|-------------|----------|--------|--------|
| Staging | AWS ECS Fargate (af-south-1) | staging.regmind.co | ✅ Active — validated |
| Demo | Render.com (`arie-finance-demo`) | demo.regmind.co | ✅ Active |
| Production | AWS ECS Fargate (af-south-1) | app.regmind.co | ⏳ Planned — DNS not yet provisioned |
| Render Live | Render.com (`arie-finance-live`) | arie-finance-live-mwmr.onrender.com | ❌ Suspended |

### AWS ECS (Staging)
- **Cluster:** `regmind-staging` (af-south-1)
- **ECR:** `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend`
- **Database:** AWS RDS PostgreSQL 15 (`regmind-staging-db`)
- **Secrets:** AWS Secrets Manager (`regmind/staging`)
- **Logs:** CloudWatch (`/ecs/regmind-staging`)
- **Deploy workflow:** `.github/workflows/deploy-staging.yml` — triggers on push to `main`

### Render.com (Demo)
- **Service:** `arie-finance-demo-mwmr.onrender.com`
- **Custom domain:** `demo.regmind.co`
- **Auto-deploys** from `main` branch on GitHub (`onboarda1234/onboarda`)

### render.yaml Note
The root `render.yaml` defines an `arie-finance-live` service labelled "production" — this is **not the active production environment**. Active production will be AWS ECS at `app.regmind.co` once provisioned. The `arie-finance-live` Render service is currently suspended.

### Git Tags
- `v4.0-stable` — Pre-document-visibility-fix rollback point
- `v4.1-stable` — Post-document-visibility-fix (current stable)

To rollback: `git checkout v4.1-stable`

## Code Style & Conventions

- Backend: Python 3, no type hints required, functional style
- Frontend: Vanilla JS in single HTML files, no framework, no build step
- Branding: Always use `BRAND` dict from `branding.py`, never hardcode
- Tests: pytest, aim for 100% pass rate before any deploy
- Commits: Descriptive messages with category prefix (fix:, feat:, refactor:, etc.)

## Important Notes
## Feature Scope (What Is and Is Not Implemented)

These clarifications prevent marketing claims from diverging from code behaviour:

| Feature | Status | Details |
|---------|--------|---------|
| **ComplyAdvantage Mesh sanctions/PEP/RCA screening** | ✅ ACTIVE WHEN CONFIGURED | Active AML source when `SCREENING_PROVIDER=complyadvantage`, `ENABLE_SCREENING_ABSTRACTION=true`, and CA credentials are present. |
| **Adverse media parsing** | ✅ ACTIVE | Parses adverse-media signals from screening provider results and prescreening data |
| **Adverse media (external provider)** | ⚠️ NOT IMPLEMENTED | No external adverse-media API call; no `ADVERSE_MEDIA_API_KEY`. Back office correctly notes: "Distinct adverse-media results are not persisted in the current screening report." |
| **Provider source of truth** | ✅ RUNTIME CONFIGURED | `/api/screening/status` is the operator source of truth. It separates ComplyAdvantage Mesh AML screening, Sumsub IDV/KYC, and OpenCorporates registry/enrichment status. Unknown provider evidence must remain unknown and must not default to CA. |
| **Periodic review (state machine)** | ✅ ACTIVE | `periodic_review_engine.py` enforces review states, audit trails, and lifecycle linkage |
| **Periodic review (automatic scheduler)** | ✅ ACTIVE (staging/production) | Tornado `PeriodicCallback` runs the due-review sweep (`monitoring_automation.run_due_monitoring_reviews`); enabled by default only when `ENVIRONMENT` is staging/production or `MONITORING_AUTOMATION_ENABLED=true`. No manual "Schedule Due Reviews" button exists in the back office; the `/api/monitoring/reviews/schedule` backfill endpoint is API-only. |



- `server.py` is large (~4000 lines) — all endpoints are in one file
- `arie-backoffice.html` is ~550KB — contains all JS/CSS inline
- The `arie-` prefix in filenames is legacy; the product is now "Onboarda" (portal) and "RegMind" (back office)
- Never commit `.env`, `*.db`, or files in `uploads/`
- The `archive/` and `scripts/` folders are gitignored

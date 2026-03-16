# ARIE Finance — Virtual Engineering Team

## Team Structure

```
Aisha Sudally — CEO & Co-Founder
    │
    ├── Virtual CTO (arie-cto)
    │   Architecture, strategy, code reviews, vendor decisions
    │
    ├── Lead Backend Developer (arie-backend-dev)
    │   Python/Tornado, APIs, database, Sumsub/Claude integrations
    │
    ├── Frontend Developer (arie-frontend-dev)
    │   Portal & backoffice HTML/CSS/JS, UX, responsive design
    │
    ├── DevOps Engineer (arie-devops)
    │   AWS infrastructure, deployment, CI/CD, security, monitoring
    │
    └── QA Engineer (arie-qa)
    │   Testing, regression, UAT, compliance validation
    │
    Compliance Officer (Human) — MLRO / AML Review
```

## How to Use

Each team member has a dedicated SKILL.md file in this directory.
Invoke them by asking Claude to act in that role, e.g.:

- "As my backend developer, wire up the Sumsub webhook endpoint"
- "As my DevOps engineer, set up the AWS EC2 instance"
- "As my QA engineer, test the full onboarding flow"
- "As my CTO, review this architecture decision"
- "As my frontend developer, fix the mobile layout on the portal"

## Platform Context (shared across all roles)

- **Product:** ARIE Finance — AI-powered RegTech/compliance onboarding platform
- **Stack:** Python 3 / Tornado (backend), vanilla HTML/CSS/JS (frontend), PostgreSQL (database), AWS (infrastructure)
- **Key files:**
  - Portal: `/arie-portal.html` (~7200 lines, client-facing SPA)
  - Backoffice: `/arie-backoffice.html` (~3700 lines, compliance officer SPA)
  - Server: `/arie-backend/server.py` (~4500 lines, Tornado REST API)
- **External services:** Sumsub (KYC/AML), Claude API (AI agents), OpenCorporates (company registry)
- **AI Agents:** 10 agents (4 active in pilot: Identity, FinCrime Screening, UBO Mapping, Compliance Memo)
- **Target market:** Mauritius-based financial services, expanding to ADGM/DIFC

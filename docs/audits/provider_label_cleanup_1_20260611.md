# PROVIDER-LABEL-CLEANUP-1 - OpenSanctions Removal & Provider Responsibility Cleanup

Date: 2026-06-11  
Branch: `codex/provider-label-cleanup-1`  
PR: [#454](https://github.com/onboarda1234/onboarda/pull/454)  
Implementation commit SHA: `bdfdf329a8fd2775e41d7ddc393720796c360e73`

## Source-of-Truth Verification

| Item | Value |
|---|---|
| GitHub repo | `onboarda1234/onboarda` |
| Source branch | `origin/main` |
| `origin/main` SHA at branch cut | `1a9c2216ee5b81b9a2ef2855e2108092e8e01177` |
| Branch base SHA | `1a9c2216ee5b81b9a2ef2855e2108092e8e01177` |
| Local HEAD SHA after implementation commit | `bdfdf329a8fd2775e41d7ddc393720796c360e73` |
| Staging `/api/version` SHA | Unavailable: endpoint returned `401 Authentication required` |
| Public staging version | `1.0.0-pilot` from `/api/config/environment` |
| Staging matches final branch/merge SHA | Cannot determine; PR not deployed to staging in this run |
| ECS task definition | Not available from local/GitHub context |
| Ignored local artifacts | `arie-treasury-portal.html`, unrelated final validation reports, prior SQ/KYC diagnosis report, `tmp/` |

## Files Changed

Provider cleanup changed buyer/officer UI, backend status/config, docs, mock memo wording, resilience exports, and tests:

- `index.html`, `onboarda-website.jsx`
- `arie-backoffice.html`, `arie-backend/arie-backoffice.html`
- `arie-backend/server.py`, `config.py`, `environment.py`, `validation_engine.py`, `start.sh`, `render.yaml`
- `arie-backend/resilience/__init__.py`, `arie-backend/resilience/integration_wrappers.py`
- `arie-backend/claude_client.py`, `screening.py`, `screening_normalizer.py`, `screening_adapter_sumsub.py`, `security_hardening.py`
- docs/runbooks under `docs/`, `README.md`, `CLAUDE.md`, resilience docs
- tests including new `arie-backend/tests/test_provider_label_policy.py`

## Cleanup Evidence

Counts exclude `docs/audits/` evidence reports and unrelated untracked local artifacts.

| Search class | Before on `origin/main` | After on branch |
|---|---:|---:|
| OpenSanctions variants | 42 | 0 |
| Prohibited Sumsub screening labels | 34 | 0 |
| CA identity-verification labels | 0 | 0 |

Required grep after cleanup:

```text
rg -n -i "opensanctions|opensanction|open sanctions|open-sanctions|open_sanctions|sumsub aml|sumsub sanctions|sumsub watchlist|sumsub pep|sumsub adverse media|sumsub screening|sumsub customer screening|sumsub company screening|sumsub monitoring|complyadvantage identity|complyadvantage kyc|ca identity verification" . --glob '!node_modules' --glob '!venv' --glob '!tmp' --glob '!docs/audits/*' --glob '!arie-treasury-portal.html'
```

Result: no matches.

## Backend/API Changes

- Removed OpenSanctions config constants and environment helper.
- Removed OpenSanctions from admin health integration output.
- Removed OpenSanctions provider object from `/api/screening/status`.
- Removed import/export of the active `ResilientOpenSanctionsClient` wrapper.
- Removed OpenSanctions env expectation from `render.yaml` and startup validation.
- Updated Sumsub status wording to individual identity verification / KYC only.
- Updated startup/status wording to use CA screening for screening responsibility.

No approval gate, KYC architecture, screening truth, provider-call, or real data mutation behavior was intentionally changed.

## UI/Docs/Generated Wording

- Buyer-facing screening/adverse-media copy now names ComplyAdvantage for screening responsibilities.
- Back-office provider resource cards now say `ComplyAdvantage Screening - Sanctions, Watchlists, PEPs & RCAs`.
- Sumsub remains labelled for identity verification / individual KYC only.
- Mock/generated memo content no longer names OpenSanctions.
- Docs and runbooks no longer describe OpenSanctions as active/integrated.

## Tests Run

```text
python3 -m py_compile server.py screening.py screening_normalizer.py screening_state.py rule_engine.py base_handler.py security_hardening.py sumsub_client.py
```

Result: passed.

```text
pytest -q tests/test_provider_label_policy.py tests/test_backoffice_monitoring_navigation_static.py tests/test_backoffice_ca_truthflow_static.py
```

Result: `34 passed`.

```text
pytest -q tests/test_screening_state_priority_a.py tests/test_screening_queue_state_integrity.py tests/test_screening_queue.py tests/test_screening_review.py tests/test_screening_freshness.py tests/test_approval_gate.py
```

Result: `142 passed`.

```text
pytest -q tests/test_sumsub_hardening_pr14.py tests/test_sumsub_verification.py
```

Result: `58 passed`.

```text
pytest -q tests/test_api.py -k "screening or provider or health or sumsub or kyc"
```

Result: `31 passed, 106 deselected`.

## Browser / Staging Evidence

PR #454 is open as a draft. The branch was not deployed to staging in this run, so authenticated browser validation against the PR deployment was not performed.

Read-only staging metadata probes:

- `GET https://staging.regmind.co/api/version` returned `401 Authentication required`; deployed SHA unavailable.
- `GET https://staging.regmind.co/api/config/environment` returned `environment=staging`, `version=1.0.0-pilot`.

Required post-deploy browser checks remain pending:

- back-office dashboard
- Screening Queue
- Application detail
- Monitoring Alerts
- AI Verification Checks
- Resources
- Settings/provider status
- buyer-facing landing pages

## Remaining Historical References

No approved historical OpenSanctions references remain in product/source/docs surfaces scanned by the new regression test. Audit reports under `docs/audits/` may mention removed terms as evidence and are excluded from the product-surface banned-string test.

## Remaining Gaps

- Staging deployment and authenticated browser validation are pending.
- `/api/version` remains auth-gated, so unauthenticated SHA drift checks are not possible.
- Legacy function names such as `screen_sumsub_aml` were intentionally not renamed to avoid behavior-contract churn in a provider-label sprint. Product-facing labels and docs no longer describe Sumsub as the screening provider.

## Final Verdict

**PASS WITH MINOR ISSUES**.

Repository cleanup passes the provider-label acceptance criteria: no active/buyer/officer/API OpenSanctions references remain outside audit evidence, Sumsub labels are KYC/identity-only, and CA labels are screening/monitoring-only. The minor issue is operational: PR #454 was not deployed to staging in this run, so staging browser validation and deployed SHA comparison remain pending.

# Validation Report

Repository base: `4b8b27690fbe5e639494f39607e3257bc0feef2d`

## Runtime collection

- Environment: staging
- Backend task definition: `regmind-staging:982`
- Worker task definition: `regmind-verification-worker:430`
- Staging source SHA: `4b8b27690fbe5e639494f39607e3257bc0feef2d`
- Database mode: `transaction_read_only=on`
- Isolation: `REPEATABLE READ`
- Completion: `ROLLBACK`
- Monitoring Alerts read: `19`
- All four Monitoring feature-governance flags: `OFF`
- Monitoring Alert rows modified: `0`

The source fingerprints recorded in `runtime_inventory.json` were identical
before and after collection in the same read-only snapshot.

## Engineering validation

| Gate | Result |
|---|---|
| Focused audit contracts | `26 passed` |
| Protected-module regression | `1,572 passed` with zero skip/xfail/xpass |
| Full repository suite (CI PDF exclusion) | `8,252 passed, 2 expected skips, 4 expected xfails` |
| CI-excluded PDF lane | `8 passed` |
| Python compilation | PASS |
| CI fatal flake8 (`E9,F63,F7,F82`) | PASS, `0` findings |
| JSON statistic reconciliation | PASS |
| Sensitive-material scan | PASS |
| Independent review | READY, no P0/P1/P2 |

The protected runner covers Applications/KYC, Screening Queue and Screening
Review, RSMP, EDD, Change Management, and Monitoring ownership boundaries.

## Scope verification

No application runtime module, endpoint, UI, database schema, migration, feature
flag, workflow, scheduler, CI definition, deployment definition, Applications
behaviour, Screening behaviour, RSMP behaviour, EDD behaviour, or Change
Management behaviour is changed by this PR.

Browser QA remains a post-deployment read-only confirmation gate.

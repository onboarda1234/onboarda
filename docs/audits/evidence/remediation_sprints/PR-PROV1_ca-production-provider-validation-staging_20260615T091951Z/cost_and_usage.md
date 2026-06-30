# PR-PROV1 Cost And Usage Controls

## Status

APPROVED CAPS RECORDED; RUNTIME BLOCKED.

## Completed Usage

- CA OAuth credential-only probe: 1 token acquisition.
- Post-approval authenticated RegMind status checks: read-only.
- Screening requests sent: 0.
- Webhooks triggered: 0.
- Expected screening cost incurred by PR-PROV1 so far: none.

## Approved Runtime Caps

- Approved screening case cap: maximum `10` screening cases total.
- Approved expected CA usage/cost exposure: maximum `USD 50`.
- Approved subjects:
  - Entity: `Multigate Technologies Limited`
  - Director: `Stephen Margolis`
  - UBO: `Sir Michael Lawrence Davis`
  - Intermediary: `Gemrock UK Plc`

## Required Before Runtime Screening

Dashboard/account/API mode must be intentionally confirmed as Production before
spending any screening calls because prior dashboard evidence reportedly showed
Sandbox. API credential URLs are production-domain, but dashboard/account mode
was not independently confirmed in this run.

## Monitoring

To be completed after dashboard/account-mode confirmation:

- CA Mesh usage dashboard checked by operator.
- CloudWatch application/provider logs checked for errors only, with no secrets exported.
- RegMind audit/provider evidence checked as primary proof.

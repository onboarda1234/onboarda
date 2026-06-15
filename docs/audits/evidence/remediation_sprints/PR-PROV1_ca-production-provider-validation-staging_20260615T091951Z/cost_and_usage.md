# PR-PROV1 Cost And Usage Controls

## Status

AWAITING APPROVAL.

## Completed Usage

- CA OAuth credential-only probe: 1 token acquisition.
- Screening requests sent: 0.
- Webhooks triggered: 0.
- Expected screening cost incurred by PR-PROV1 so far: none.

## Required Before Runtime Screening

Operator must approve:

- Exact case cap.
- Expected number of entity screenings.
- Expected number of director screenings.
- Expected number of UBO screenings.
- Expected number of intermediary screenings.
- Expected number of rescreen attempts.
- Whether adverse media provider calls are expected to bill separately.
- CA dashboard/usage monitoring owner.

Recommended initial cap:

- Maximum 5 applications.
- Maximum 15 subject screenings.
- Maximum 1 rescreen per controlled case.
- No uncontrolled monitoring subscriptions unless explicitly approved.

## Monitoring

To be completed after approval:

- CA Mesh usage dashboard checked by operator.
- CloudWatch application/provider logs checked for errors only, with no secrets exported.
- RegMind audit/provider evidence checked as primary proof.

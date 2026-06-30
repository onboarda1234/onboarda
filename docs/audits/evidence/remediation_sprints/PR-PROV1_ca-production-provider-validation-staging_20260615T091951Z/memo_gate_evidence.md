# PR-PROV1 Memo and Approval Gate Evidence

## Status

NOT RUN.

No provider-backed controlled application was created under PR-PROV1 after
operator approval because dashboard/account mode remains unconfirmed.

## Read-Only Preconditions

- Staging `/api/version`: PASS.
- `/api/screening/status`: PASS for ComplyAdvantage Mesh active AML provider,
  Sumsub IDV/KYC only, fallback disabled.
- API credential mode inference: `production_domain`.

## Missing Runtime Evidence

The following remain untested for PR-PROV1:

- Memo adverse-media parity for an approved provider-backed case.
- Memo staleness/regeneration after provider result changes.
- Approval gate blocking unresolved screening/adverse-media risk.
- Approval gate blocking stale/partial/provider-error CA state.
- Approval gate allowing clean terminal no-hit only where all other gates are satisfied.

## Required Next Evidence

After dashboard/account Production mode is confirmed, run the controlled matrix
and capture redacted:

- queue/detail canonical truth;
- memo metadata and adverse-media fields;
- memo stale/requires-regeneration flags;
- approval blocker response;
- audit events linking blocker/memo state to CA provider references.

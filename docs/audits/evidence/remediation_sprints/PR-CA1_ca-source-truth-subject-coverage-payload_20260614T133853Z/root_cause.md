# PR-CA1 Root Cause

## Summary

The CA integration had live-provider capability, but the surrounding source-of-truth, subject orchestration, payload construction, and UI terminology were still shaped around older Sumsub/legacy screening assumptions.

## Root Causes By Issue

### CA-001

Provider governance was split between environment flags, runtime readiness helpers, and UI/status presentation. The code could route to ComplyAdvantage, but the canonical source-of-truth rules still returned `legacy` and the status endpoint did not clearly expose separate AML, IDV/KYC, registry, abstraction, and fallback states.

### CA-005

The screening adapter contract and normalized report model stopped at company, director, and UBO subjects. Intermediaries were loaded by application code but never passed into CA screening or terminality/evidence checks.

### CA-006

The CA company payload builder was conservative to the point of being too thin. It did not use identifiers already present in the application data, reducing provider match quality and weakening traceability.

### CA-008 / CA-UX-012

Back-office provider labels and fallback helpers predated the Mesh source-of-truth decision. Generic and legacy wording remained, and blank provider data could fall through to ComplyAdvantage terminology instead of remaining unknown.

### CA-012

Docs were updated incrementally across earlier remediation phases and no longer matched the current provider role split: CA Mesh for AML screening, Sumsub for IDV/KYC, and OpenCorporates for registry/enrichment.

## Scoped Non-Goals

This PR intentionally does not solve:

- Full CA/Mesh dashboard parity.
- Adverse-media UI rebuild.
- Full audit-chain completeness.
- Historical contradictory screening state cleanup.
- PR-CA2, PR-CA3, PR-CA4, PR-7, DOC, or CR remediation items.

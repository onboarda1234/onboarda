# Remediation PR Checklist

Copy this checklist into every remediation PR description.

## Source of Truth

- [ ] Confirmed GitHub `origin/main` is the source of truth
- [ ] Started from latest `origin/main`
- [ ] Recorded `origin/main` SHA before diagnosis
- [ ] Did not rely on stale reports, old screenshots, local-only evidence, or branch-only evidence for closure

## Diagnosis and Fix

- [ ] Re-diagnosed the issue on current `main`
- [ ] Confirmed whether issue still exists
- [ ] Identified root cause
- [ ] Implemented minimal safe fix
- [ ] Added regression tests

## Local and Branch Validation

- [ ] Recorded branch commit SHA
- [ ] Ran targeted tests
- [ ] Ran full relevant backend suite
- [ ] Ran frontend/static checks where relevant
- [ ] Ran browser tests where UI/client/officer workflow is affected

## Main and Staging Validation

- [ ] Merged to `main`
- [ ] Recorded merged main SHA
- [ ] Deployed `main` to staging
- [ ] Confirmed staging `/api/version` equals merged main SHA
- [ ] Ran staging API smoke tests
- [ ] Ran staging browser smoke tests where applicable

## Evidence and Closure

- [ ] Saved evidence pack
- [ ] Updated remediation tracker/report
- [ ] Did not mark closed without deployed staging proof

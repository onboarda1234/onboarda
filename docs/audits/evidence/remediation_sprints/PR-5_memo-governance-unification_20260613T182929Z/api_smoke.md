# PR-5 API Smoke

Branch-stage API smoke: not run against staging.

Required after merge and staging deployment:

- Application detail uses canonical latest memo.
- Validation uses canonical latest memo.
- Supervisor uses canonical latest memo.
- Approval uses canonical latest memo.
- Export/evidence path uses canonical latest memo where applicable.
- Stale/historical memo is not treated as current without label.
- Memo approval without `approval_reason` fails.
- Memo approval with valid `approval_reason` succeeds when gates allow.
- Approval reason persists and appears in audit/evidence where applicable.
- Consolidated memo status fields are present and non-contradictory.
- FSI-001, FSI-002, FSI-003, and FSI-007 regressions remain passing.

Raw redacted staging responses must be saved under `runtime_json/` before closure.

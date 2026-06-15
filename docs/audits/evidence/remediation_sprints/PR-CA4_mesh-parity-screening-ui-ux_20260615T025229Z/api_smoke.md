# PR-CA4 API Smoke

Status: pending for merged-main staging validation.

Local regression tests prove the canonical queue/detail/memo and adverse-media rollup behavior against fixtures. Staging API smoke must still be run after merge/deploy.

Required staging API smoke assertions:

- Queue/detail/memo inputs agree on canonical current risk count.
- Adverse media rolls up consistently.
- Application detail does not say no adverse media when canonical evidence exists.
- Provider refs are available or an explicit missing reason is shown.
- Unresolved risks do not appear clear.
- Memo consumes canonical adverse-media truth.
- PR-CA1, PR-CA2, and PR-CA3 regressions remain passing.

No staging API smoke is claimed before merge/deploy.

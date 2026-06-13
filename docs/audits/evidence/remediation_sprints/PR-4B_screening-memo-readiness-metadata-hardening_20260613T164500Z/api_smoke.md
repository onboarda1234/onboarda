# API Smoke

Pre-corrective PR-4 staging smoke failed.

Failed probe:
- Endpoint: `GET /api/applications/13cabbdf214542ea`
- Role: back-office officer
- Result: current `screening_truth_summary` safe, but stale `latest_memo_data.metadata.*` screening summaries retained `approval_ready=true` while `approval_blocking=true`.
- Evidence: `runtime_json/staging_app_detail_contradiction_probe_redacted.json`

Required after PR-4B merge and deployment:
- Confirm staging `/api/version` `git_sha` and `image_tag` equal merged PR-4B main SHA.
- Re-run application detail probe.
- Confirm no nested dictionary in application detail returns `approval_ready=true` while `approval_blocking=true`.
- Confirm current `screening_truth_summary` remains safe.
- Confirm `latest_memo_data.metadata.screening_state_summary` is safe.
- Confirm `latest_memo_data.metadata.agent5_input_contract.screening_terminality_summary` is safe.
- Re-run FSI-001/FSI-002/FSI-003 regression smoke.

# Diagnosis

Corrective follow-up for `FSI-007` after PR-4 merged and deployed.

PR-4 staging deployment:
- Merged main SHA: `ef5ee1d7e3d5e1ee83ad4b8015d70f432485492a`
- Staging `/api/version`: `git_sha` and `image_tag` matched `ef5ee1d7e3d5e1ee83ad4b8015d70f432485492a`
- Deploy workflow: `27471759666`

Failed staging API smoke:
- Application: `13cabbdf214542ea`
- Endpoint: `GET /api/applications/13cabbdf214542ea`
- Role: back-office officer
- Current `screening_truth_summary` was safe:
  - `approval_ready=false`
  - `approval_blocking=true`
  - `screening_gate_ready=false`
  - `approval_blocked_reasons=["director_screening_0:live_terminal_match"]`
- Residual unsafe memo metadata remained in:
  - `latest_memo_data.metadata.screening_state_summary`
  - `latest_memo_data.metadata.agent5_input_contract.screening_terminality_summary`
- Both stale memo metadata paths still emitted:
  - `approval_ready=true`
  - `approval_blocking=true`
  - `blocking_reasons=["director_screening_0:live_terminal_match"]`

Evidence:
- `runtime_json/staging_app_detail_contradiction_probe_redacted.json`

Conclusion:
- `FSI-007` remains open after PR-4.
- Corrective PR-4B is required before closure.

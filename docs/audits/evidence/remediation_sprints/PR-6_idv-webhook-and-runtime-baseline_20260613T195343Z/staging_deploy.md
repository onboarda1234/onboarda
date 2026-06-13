# PR-6 Staging Deploy

Branch-stage status: not deployed.

Required after merge:

1. Merge PR-6 into `main`.
2. Deploy merged `main` to staging.
3. Confirm `/api/version` returns:
   - `git_sha` equal to merged main SHA
   - `image_tag` equal to merged main SHA
4. Confirm backend ECS task definition image/env matches merged main SHA.
5. Confirm verification worker ECS task definition image/env matches merged main SHA.
6. Confirm backend and worker services are stable with desired/running counts healthy.
7. Save raw redacted ECS/task/version output under `runtime_json/`.

Current pre-fix staging diagnosis is saved under:

- `runtime_json/staging_runtime_baseline_diagnosis_redacted.json`
- `runtime_json/staging_runtime_baseline_helper_prefix_redacted.json`

Current pre-fix status:

- Backend aligned with `b061c52f147b6fa42398629bb2b5dd2502682f3d`.
- Worker healthy but stale on `15b281fa620d19c8a475f5d3e94e78edcf976f5a`.

FSI-011 and POST-INFRA must remain `PARTIALLY FIXED` until merged-main staging deployment and worker smoke pass.

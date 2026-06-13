# Diagnosis

Remediation ID: FSI-001

Corrective scope: PR-1C - Client Application RMI Boundary Hardening

Source of truth:
- `origin/main`: `12be9e5c3d127400b6f74d7013bab1ae63d418b7`
- This commit is the merged and staged PR-1B main SHA.

Diagnosis result:
- PR-1B post-merge staging API smoke confirmed `/api/version` matched `12be9e5c3d127400b6f74d7013bab1ae63d418b7`.
- `GET /api/notifications` with a client token no longer exposed the previously confirmed `Officer notes: testing of PEP` or `runtime audit` notification text.
- PR-1 boundary regression checks still denied client access to internal screening/application surfaces.
- New remaining leak found in client-owned application detail: `GET /api/applications/{own_application_id}` returned `rmi_requests[0].created_by` and `rmi_requests[0].created_by_name`.

Evidence:
- `runtime_json/diagnosis_application_detail_rmi_leak_redacted.json`

Conclusion:
- FSI-001 remains PARTIALLY FIXED after PR-1B.
- Client notification leakage is fixed, but the portal-safe application detail projection still needs RMI request sanitization before FSI-001 can be closed.

# Portal Upload FK Regression Diagnosis

Symptom:

```text
insert or update on table "documents" violates foreign key constraint "documents_uploaded_by_fkey"
DETAIL: Key (uploaded_by)=(21eb50f952e54634) is not present in table "users".
```

Root cause:

- The portal document upload handler populated `documents.uploaded_by` with the authenticated portal subject.
- `documents.uploaded_by` is constrained to `users.id`.
- Portal/client upload actors are not guaranteed to exist in the `users` table.
- The write therefore failed before the upload could complete.

Actor model decision:

- Preserve `documents.uploaded_by` for back-office/officer users only.
- Do not drop or weaken the `uploaded_by` FK.
- For portal uploads, leave `documents.uploaded_by` null and persist explicit actor metadata:
  - `uploaded_by_actor_type`
  - `uploaded_by_actor_id`
  - `uploaded_by_display`
  - `upload_source`
- Continue to record upload audit events.

Files:

- `arie-backend/db.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_upload_latency_contracts.py`


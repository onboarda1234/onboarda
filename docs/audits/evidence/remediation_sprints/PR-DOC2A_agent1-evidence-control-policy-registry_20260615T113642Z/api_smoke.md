# API Smoke

Environment: local demo smoke server

Base URL: `http://127.0.0.1:18181`

## Result

Passed locally.

## Evidence Files

- `runtime_json/api_health.json`
- `runtime_json/api_version.json`
- `runtime_json/api_applications.json`
- `runtime_json/db_documents_schema.json`
- `runtime_json/browser_fixture_document_row.json`

## Checks

- `/api/health` returned `status=ok`.
- Authenticated `/api/version` returned build metadata.
- Authenticated `/api/applications` returned five demo applications and no HTTP 500 after migration `v2.42`.
- `documents` schema includes `uploaded_by`.
- Browser fixture document row stores verification payload with routine passes plus a material OCR warning.

## Staging Status

Staging validation is not complete in this pre-merge evidence pack. After merge and deploy, `/api/version.git_sha` and `/api/version.image_tag` must be checked against the merged main SHA.

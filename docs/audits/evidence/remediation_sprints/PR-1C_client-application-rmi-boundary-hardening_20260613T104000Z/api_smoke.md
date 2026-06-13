# API Smoke

Status: PENDING post-merge.

Required staging API smoke after PR-1C deploy:
- Client `GET /api/applications/{own_application_id}` returns `200`.
- Response contains no `rmi_requests[].created_by`.
- Response contains no `rmi_requests[].created_by_name`.
- Response contains no unsafe RMI reason/item text such as provider raw status, internal risk, memo/supervisor, audit, or officer notes.
- Client `GET /api/notifications` remains sanitized.
- Client remains denied from `/api/screening/queue`.
- Client remains denied from internal `/api/applications` list.
- Client remains denied from another client's application by ID/ref.
- Back-office application and screening queue access still works.

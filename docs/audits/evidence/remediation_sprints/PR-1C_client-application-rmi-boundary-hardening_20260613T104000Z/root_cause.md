# Root Cause

`ApplicationHandler.get` loads raw RMI rows with:

```python
result["rmi_requests"] = _load_rmi_requests(db, result["id"])
```

For client-authenticated requests, the handler later calls `_client_safe_application_detail(result)`.

PR-1B added `_client_safe_rmi_request(...)`, but only applied it to `/api/notifications` through `_load_client_rmi_requests(...)` and notification `rmi_request` projection. The shared application detail path kept the raw `rmi_requests` list, so client users could still receive officer/internal creator metadata such as `created_by` and `created_by_name`.

The fix is to apply the same `_client_safe_rmi_request(...)` projection inside `_client_safe_application_detail(...)` whenever `rmi_requests` are present.

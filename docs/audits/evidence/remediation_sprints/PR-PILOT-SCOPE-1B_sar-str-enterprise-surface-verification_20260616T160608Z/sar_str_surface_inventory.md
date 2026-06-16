# SAR/STR Surface Inventory

Verified / remediated SAR/STR surfaces:
- Monitoring alert modal no longer exposes active `File SAR`.
- Monitoring alert modal shows disabled `SAR/STR Coming Soon` for high/critical eligible contexts.
- Frontend `triggerSARFromAlert()` exits before API call when SAR/STR pilot flags are inactive.
- Backend SAR endpoints are gated:
  - `GET /api/sar`
  - `POST /api/sar`
  - `GET /api/sar/:id`
  - `PUT /api/sar/:id`
  - `POST /api/sar/:id/workflow`
  - `POST /api/sar/auto-trigger`
- Disabled response is controlled, business-friendly, and does not expose secrets or internal config.

Test assertion: SAR create and auto-trigger return 403 with `enterprise_module_inactive`; no `sar_reports` rows are created and monitoring alert `officer_action` is unchanged.


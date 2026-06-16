# Provider Status

Gate result: PASS

| Check | Value |
| --- | --- |
| ComplyAdvantage active AML provider | ComplyAdvantage Mesh (complyadvantage) |
| ComplyAdvantage runtime active/configured | true |
| ComplyAdvantage status | live |
| Fallback/simulation mode | disabled; enabled=false |
| CA Sandbox operator confirmation | Confirmed by operator in workstream instruction |
| Workspace identifier exposed by API | false |
| Sumsub IDV | live; configured=true |
| OpenCorporates / registry enrichment | simulated; configured=false |

Note: `/api/screening/status` does not expose the ComplyAdvantage workspace identifier or credential hostname. Sandbox mode is therefore recorded from operator confirmation plus runtime evidence that staging uses ComplyAdvantage Mesh with fallback disabled. The prescreening smoke is the runtime check that the configured staging provider path does not fail with the prior Production-workspace 503.

Raw provider status is stored in `runtime_json/screening_status_gate.json`.

# Screening Results

Provider status gate passed, but the prescreening smoke failed.

| Check | Result |
| --- | --- |
| `/api/screening/status` active AML provider | ComplyAdvantage Mesh |
| Requested provider | `complyadvantage` |
| CA fallback/simulation | disabled / false |
| Sumsub IDV | live / configured |
| OpenCorporates / registry enrichment | simulated / not configured |
| Smoke submit | 503 twice |

Smoke error: `Screening provider temporarily unavailable. Please retry in a moment.`

Raw evidence:

- `runtime_json/screening_status_gate.json`
- `runtime_json/provider_gate_result.json`
- `runtime_json/prescreening_smoke_record.json`

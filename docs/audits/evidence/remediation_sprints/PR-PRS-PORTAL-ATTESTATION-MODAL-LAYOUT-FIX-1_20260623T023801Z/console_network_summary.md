# Console And Network Summary

Browser smoke result: PASS

Console:
- `console_errors`: `[]`
- `console_warnings`: `[]`
- `page_errors`: `[]`

Network:
- Bad responses: `[]`
- Failed requests: none recorded.
- API route target: `https://staging.regmind.co/api/*`

Observed API calls:

| Method | Path | Status |
| --- | --- | ---: |
| GET | `/api/config/environment` | 200 |
| GET | `/api/portal/applications` | 200 |
| GET | `/api/save-resume/active` | 200 |
| GET | `/api/portal/applications/prportalmodal-c0925b5d-app/periodic-review` | 200 |

The repeated calls above were expected because the smoke reopened the portal at three viewport widths.

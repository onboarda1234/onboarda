# Portal Wording Scan

Task: PR-PORTAL-PILOT-BOUNDARY-1

Timestamp UTC: 20260618T030901Z

## Local Static Scan

Command:

```bash
rg -n "Risk Assessment|AI-powered risk|AI risk scoring|5-dimension|5 weighted dimensions|Composite Risk Score|AI Transparency|Risk unavailable|Risk Rating|Next Review|All Checks Cleared|Sanctions Screening|Document Authenticity|Document Validity|Enhanced Due Diligence|\bEDD\b|LOW RISK|MEDIUM RISK|HIGH RISK|VERY HIGH RISK|Medium-Low|MEDIUM-LOW|High Risk|Low Risk|high risk|low-risk|elevated risk profile" arie-portal.html
```

Result: no matches in `arie-portal.html`.

## Rendered Client-State Coverage

`arie-backend/tests/test_portal_pilot_boundary_static.py` checks visible text for:

- Pending
- Application processing
- Pre-approval hold
- Compliance hold
- Pricing
- KYC document upload
- Submission review
- Document review
- Approved
- Client notifications

Result: passed.

## Notes

Remaining `risk` and `ai` tokens in `arie-portal.html` are compatibility class, function, or comment names and are not rendered client wording in the tested portal states. Dedicated staff visibility remains in `arie-backoffice.html`.

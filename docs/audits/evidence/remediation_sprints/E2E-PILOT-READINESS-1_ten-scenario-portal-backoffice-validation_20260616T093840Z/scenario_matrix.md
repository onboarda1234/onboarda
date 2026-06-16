# Scenario Matrix

| Scenario | Purpose | Expected |
| --- | --- | --- |
| S01 Clean low-risk standard company | Baseline happy path. | Low/normal risk, no unnecessary EDD, correct portal-slot document mapping, approval blocked only by unresolved screening/memo gates. |
| S02 Missing required corporate document | Test document request/blocker logic. | Back office shows missing Register of Directors, approval/pre-approval blocked, no false complete state. |
| S03 Expired or stale document | Test freshness rules. | Stale/expired evidence is detected or marked review-required; approval blocked unless accepted. |
| S04 Director/UBO person KYC issue | Test person document verification. | Person-level section shows missing/problem document; Sumsub IDV separate from Agent 1. |
| S05 PEP declared | Test PEP declaration routing and enhanced requirement trigger. | Risk increases; EDD/enhanced requirements generated if configured; Agent 1 does not claim sanctions/PEP screening ownership. |
| S06 High-risk jurisdiction / country-risk scenario | Test jurisdiction risk scoring and EDD routing. | Country-risk setting reflected; no silent low-risk fallback for unknown/current manual-source country risk. |
| S07 High-risk business activity / regulated activity | Test enhanced document requests from activity/risk settings. | Enhanced requirement requests generated for regulated/high-risk activity; approval blocked until resolved. |
| S08 Complex ownership / multiple UBOs | Test ownership/risk logic. | Multiple UBOs display without duplication; ownership evidence completeness/inconsistency affects gate. |
| S09 Intermediary / introducer involved | Test intermediary scope. | Back office shows intermediary context and requirements without leakage into client/director/UBO slots. |
| S10 Manual acceptance / override path | Test officer decision controls. | Accept with reason is enforced and audited; approval gate updates only if policy allows. |

## Master Table

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 Clean low-risk standard company | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S02 Missing required corporate document | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S03 Expired or stale document | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S04 Director/UBO person KYC issue | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S05 PEP declared | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S07 High-risk business activity / regulated activity | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S08 Complex ownership / multiple UBOs | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S09 Intermediary / introducer involved | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S10 Manual acceptance / override path | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |

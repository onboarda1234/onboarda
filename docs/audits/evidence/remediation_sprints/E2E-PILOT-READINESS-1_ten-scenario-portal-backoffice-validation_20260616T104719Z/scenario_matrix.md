# Scenario Matrix

The ten-scenario E2E was not executed because the prescreening smoke gate failed.

| Scenario | Purpose | Executed | Verdict |
| --- | --- | --- | --- |
| S01 Clean low-risk standard company | Baseline happy path | No | BLOCKED |
| S02 Missing required corporate document | Document request/blocker logic | No | BLOCKED |
| S03 Expired or stale document | Freshness rules | No | BLOCKED |
| S04 Director/UBO person KYC issue | Person document verification | No | BLOCKED |
| S05 PEP declared | PEP declaration and enhanced routing | No | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | Jurisdiction risk scoring and EDD routing | No | BLOCKED |
| S07 High-risk business activity / regulated activity | Activity-driven enhanced document requests | No | BLOCKED |
| S08 Complex ownership / multiple UBOs | Ownership/risk logic | No | BLOCKED |
| S09 Intermediary / introducer involved | Intermediary scope | No | BLOCKED |
| S10 Manual acceptance / override path | Officer manual acceptance controls | No | BLOCKED |

## Master Table

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S02 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S03 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S04 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S05 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S06 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S07 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S08 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S09 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S10 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |

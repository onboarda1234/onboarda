# Scenario Matrix

The planned scenario matrix was not executed because staging failed the source-of-truth version gate.

| Scenario | Purpose | Verdict |
| --- | --- | --- |
| S01 Clean low-risk standard company | Baseline happy path | BLOCKED |
| S02 Missing required corporate document | Document request/blocker logic | BLOCKED |
| S03 Expired or stale document | Freshness rules | BLOCKED |
| S04 Director/UBO person KYC issue | Person document verification | BLOCKED |
| S05 PEP declared | PEP declaration and enhanced routing | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | Jurisdiction risk scoring and EDD routing | BLOCKED |
| S07 High-risk business activity / regulated activity | Activity-driven enhanced document requests | BLOCKED |
| S08 Complex ownership / multiple UBOs | Ownership/risk logic | BLOCKED |
| S09 Intermediary / introducer involved | Intermediary scope | BLOCKED |
| S10 Manual acceptance / override path | Officer manual acceptance controls | BLOCKED |

## Master Table

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S02 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S03 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S04 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S05 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S06 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S07 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S08 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S09 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S10 | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |

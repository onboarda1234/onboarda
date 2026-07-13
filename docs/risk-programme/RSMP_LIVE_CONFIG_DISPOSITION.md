# RSMP Live-Config Disposition

**Status:** HOLD — read-only comparison; staging was not changed.

**Pinned live config:** risk_config:2026-07-13 07:15:16.941658
**Hash verification:** reconstructed country, sector, and entity maps exactly match the recorded live hashes.

| Config family | Key | Live value | Seed/Gate 0 value | Difference | Recommended action | Founder decision |
|---|---|---:|---:|---|---|---|
| country_risk_scores | democratic republic of congo | — | 3 | Missing from live | Add the canonical score-3 key only in a separate approved config change after founder alias approval and dry run. | PENDING |
| country_risk_scores | north korea | — | 4 | Missing from live | Defer canonical-key reconciliation to Tier 1B; retain the current DPRK live key unchanged. | PENDING |
| country_risk_scores | iraq | 4 | 3 | Live score is one tier higher | Retain the stricter live score 4 pending founder/compliance Tier 1B disposition; do not overwrite. | PENDING |
| country_risk_scores | lebanon | 4 | 3 | Live score is one tier higher | Retain the stricter live score 4 pending founder/compliance Tier 1B disposition; do not overwrite. | PENDING |
| country_risk_scores | south sudan | 4 | 3 | Live score is one tier higher | Retain the stricter live score 4 pending founder/compliance Tier 1B disposition; do not overwrite. | PENDING |
| country_risk_scores | venezuela | 4 | 3 | Live score is one tier higher | Retain the stricter live score 4 pending founder/compliance Tier 1B disposition; do not overwrite. | PENDING |
| country_risk_scores | yemen | 4 | 3 | Live score is one tier higher | Retain the stricter live score 4 pending founder/compliance Tier 1B disposition; do not overwrite. | PENDING |
| country_risk_scores | bulgaria | 3 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| country_risk_scores | central african republic | 4 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| country_risk_scores | croatia | 3 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| country_risk_scores | dr congo | 3 | — | Extra in live | Retain until canonical DRC key approval and a reviewed usage/recompute plan; then decide retirement. | PENDING |
| country_risk_scores | namibia | 3 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| country_risk_scores | north korea (dprk) | 4 | — | Extra in live | Retain unchanged; reconcile canonical naming only in Tier 1B. | PENDING |
| country_risk_scores | ukraine (crimea/donetsk/luhansk) | 4 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| country_risk_scores | zimbabwe | 4 | — | Extra in live | Retain unchanged pending Tier 1B country/FATF disposition. | PENDING |
| sector_risk_scores | manufacturing | 1 | 2 | Live 1; Seed/Gate 0 2 | Founder decides 1 or 2; any alignment must be a separate reviewed config update with dry run. | PENDING |
| sector_risk_scores | precious metals | 4 | 4 (seed) / 3 (Gate 0) | Corrected Gate 0 score differs from live and seed | HOLD for a separate reviewed runtime/config change and dry run; do not overwrite staging in this task. | HOLD |
| sector_risk_scores | agriculture / food | 1 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | arms / defence | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | banking / financial services | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | construction / infrastructure | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | crypto / virtual assets | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | energy / mining | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | fintech / e-money | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | gaming / gambling | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | government / public sector | 1 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | healthcare / pharmaceutical | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | import / export trading | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | media / entertainment | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | money service business (msb) | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | non-profit / ngo | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | precious metals / stones | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | professional services (legal/accounting) | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | retail / consumer goods | 1 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | shipping / maritime / logistics | 3 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| sector_risk_scores | technology / saas | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit and founder disposition; do not use this row to approve an alias. | PENDING |
| entity_type_scores | unregulated fund | 4 | 4 (seed) / 3 (Gate 0) | Corrected Gate 0 score differs from live and seed | HOLD for a separate reviewed runtime/config change and dry run; do not overwrite staging in this task. | HOLD |
| entity_type_scores | regulated fund (cis / licensed) | 2 | — | Extra in live | Retain unchanged pending exact-key usage audit; the proposed resolver continues to use the approved base seed key. | PENDING |
| entity_type_scores | unregulated fund / spv | 4 | — | Extra in live | Retain unchanged pending exact-key usage audit; the proposed resolver continues to use the approved base seed key. | PENDING |
| sector resolver contract | investment management | — | 3 (Gate 0 label score) | Founder-approved sector is not implemented in the live/seed resolver contract | HOLD for a separate reviewed runtime/config implementation and dry run. | HOLD |
| sector resolver contract | cloud services | — | 2 (Gate 0 label score) | Founder-approved sector is not implemented in the live/seed resolver contract | HOLD for a separate reviewed runtime/config implementation and dry run. | HOLD |
| sector resolver contract | private banking | — | 4 (Gate 0 label score) | Founder-approved sector is not implemented in the live/seed resolver contract | HOLD for a separate reviewed runtime/config implementation preserving the sector-score-4 High floor, followed by dry run. | HOLD |
| sector resolver contract | wealth management | — | 3 (Gate 0 label score) | Approved Family Office label has no exact live/seed config key | HOLD for a separate reviewed config-contract change adding the exact score-3 key, followed by dry run. | HOLD |

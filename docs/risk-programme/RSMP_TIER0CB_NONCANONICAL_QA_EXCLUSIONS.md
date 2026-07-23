# RSMP Tier 0C-B Noncanonical QA Exclusions

Status: proposed formal classification for human review. This document does
not authorize Tier 0C-B execution, fixture deletion, RSMP activation, or
production use.

## Runner boundary

The dedicated Tier 0C-B runner continues to select exactly
`RM-PILOT-001` through `RM-PILOT-041`. Noncanonical QA applications are not
selection inputs. The accompanying data-only registry is documentary and is
not imported or consumed by the runner.

## ARF-QAFIX-H5-PR840

| Field | Evidence |
|---|---|
| Classification | Excluded noncanonical QA fixture |
| Application ID | `f1xedprc840h5001` |
| Origin | PR #840 post-merge H5 staging validation |
| Origin merge | `6a769883cdcfc00425d803a1b1136a13fd7b790d` |
| Purpose | Validate sanctions-first triage bucketing for two stored synthetic sanctions/adverse-media hits |
| Provider activity | None; the validation used stored provider-shaped evidence |
| Persistent dependency | None; the permanent regression uses isolated test ref `ARF-PRC-H5` |
| Current disposition | Retain pending a reviewed, exact-identity sanctioned cleanup path |

The PR #840 validation report records that this dedicated fixture was seeded
because no existing QAFIX row exposed the required combined-category shape.
It also records no provider call, screening run, RM-PILOT mutation, or delete.
Repository and GitHub history contain no persistent use of this staging ref.

The July 23, 2026 read-only staging inventory proved:

- one application row with `is_fixture=true`;
- shared inactive client `qafix-client`;
- two embedded synthetic hits categorised as `sanctions` and `adverse_media`;
- zero rows in application-linked child tables;
- 41 canonical RM-PILOT applications and eight other noncanonical fixtures;
- no RSMP activation or database write during discovery.

Discovery execution was pinned to deployed SHA
`8e89c9fd0526f403ea08927531a987b2127fedc7`, task definition
`regmind-staging:932`, and encrypted database `regmind-staging-db`. The
read-only ECS task `d21b7cffc4a748c58a8656ef4a9bcff7` ran from
2026-07-23 06:37:51.779+04:00 to 06:38:16.230+04:00 and exited successfully.
Its reviewed probe source SHA-256 was
`b33372584bf54c4b52620ce89935a19b5060ef6f86233244406cb5034555f73a`;
the pinned database-identity evidence SHA-256 was
`a95b258b983965c7092b168da8a8e840d97cb65cc9530e39177a8f5276bf0c4a`.

Read-only snapshot evidence (not a runtime selection input):

| Scope | SHA-256 |
|---|---|
| Target application row | `2b10844e8a930aad7127c1a1779e21aade87cd3e0df872f70af9bc0aca9803cf` |
| Canonical application rows | `c3c2c2bc0859bf4250c2ecfbbe1dd8108c90b14c8c8cccdbf27ffd0319b965d8` |
| Canonical IDs and references | `10951b16a8f5c48a43b15058dfaffb1cc86bbbd6cc99e3936d1a60aa54dd84be` |
| Canonical scores, tiers, lanes and evidence | `db34a8acf8d5c8fd1e332ede7973d16d864828ebde69af4bb16c71183e6a9914` |
| Canonical linked rows | `ad085af24092cf35578896339081a75b223b725b5bfc863c0d137a734f91bf7a` |
| Eight unaffected noncanonical applications | `f539fa5ada78be2b2e0461d94227d2824668209fc7519c95dd39a63b50252207` |
| Risk configuration | `9213982636cffee18aa8a8f8d3656faf13cc56fa4b9c83dc716f962848de9129` |

The deployed code manifest was `45ceaa32d592f754289fb888bbb6d6a863349cf9bde406e7d7055b6c7dc23d25`;
the 41 stored canonical identities still carried the separately tracked prior
hash `825d267a6488545ee892789f09869362faabdf77fb23df8d1d63b99f6dc27951`.
This classification does not modify either value.

## Why it was not deleted

Current main has no reviewed cleanup command scoped to this unregistered
fixture. The registered-fixture cleanup rejects it, the screening-QA wipe is
scoped to a different fixed set, and name-based execution is deliberately
disabled. Combining the low-level deletion context with bespoke SQL would be
an ad hoc operating procedure and is prohibited.

Deletion therefore requires a separately reviewed exact-identity cleanup path
with dry-run inventory, marker validation, one transaction, rollback/residue
checks, and preservation of the shared `qafix-client`. Until then, formal
exclusion is safer than changing or widening the canonical runner.

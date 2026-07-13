# RSMP Tier 0A Activation and Human Review Plan

**Status:** HOLD — draft implementation only; activation and recomputation are not authorized

**GitHub baseline:** `origin/main` at `02eeae5062d1f1d8f77e7ca69c4629bac72c57b0`

## 1. Merge safety

The parser/mapping fidelity behavior is controlled by
`ENABLE_RSMP_TIER0A_MAPPING_FIDELITY`. The flag is `false` in every code default.
Merging this implementation cannot enable the changed scoring path unless an
operator separately changes the deployment environment.

The ownership wording change is a rename only. Both the new wording and the
exact legacy alias score 4 and preserve the existing `floor_rule_opaque_ownership`
High floor and `opaque_or_incomplete_ownership` EDD route.

## 2. Scope boundary

Tier 0A covers only sector, entity type, ownership, transaction complexity,
introduction method, and monthly volume. Geography changes are limited to these
exact aliases:

- Hong Kong SAR → hong kong
- Congo (DRC) → democratic republic of congo
- Türkiye → turkey

Blank incorporation country is reserved for Tier 0B fail-closed handling. All
other countries and all regions retain the pilot manual FATF treatment pending
Tier 1B.

## 3. Required activation sequence

1. Export the live staging `risk_config` in a read-only transaction and record
   its version and canonical SHA-256 hashes.
2. Compare the live export against the code seed and the founder-approved Gate 0
   v4 artifact. A Gate 0 v4 hash is mandatory; `not_provided` is a HOLD result.
3. Export all active scored staging applications plus only the risk inputs
   needed for deterministic replay. Do not export names, contact data, identity
   documents, or other unnecessary PII.
4. Run `arie-backend/scripts/rsmp_tier0a_dry_run.py` offline. The tool imports no
   database module, performs zero database writes, and pseudonymizes application
   identifiers in its result.
5. Review every score, tier, routing, and unresolved-mapping delta. Assign an
   explicit disposition to each unresolved controlled value.
6. Founder and Compliance/Model Owner sign the same config, registry, Gate 0,
   and dry-run hashes.
7. Deliberately enable the flag in staging only. Verify the approved sample and
   rollback procedure before any wider activation.
8. Execute Tier 0C recomputation only under a separate, approved runbook.

## 4. Offline dry-run interface

Input JSON shape:

```json
{
  "risk_config": {"updated_at": "...", "dimensions": [], "thresholds": []},
  "gate0_v4": {"approved_registry": {}},
  "applications": [
    {
      "application": {"id": "...", "prescreening_data": {}},
      "directors": [],
      "ubos": [],
      "intermediaries": []
    }
  ]
}
```

Command:

```bash
python arie-backend/scripts/rsmp_tier0a_dry_run.py \
  --input-json /secure/path/staging-export.json \
  --output-json /secure/path/rsmp-tier0a-dry-run.json
```

The output records legacy recalculation, flag-enabled recalculation, exact
mapping evidence, score/tier deltas, and config/registry hashes. It is an
inspection artifact, not an activation command.

## 5. Human gates

- PR-1 remains draft until code review and mapping-policy review complete.
- The activation flag remains OFF until the live/code/Gate 0 comparison and the
  complete active-application dry run are approved.
- PR-2 remains stacked on PR-1 for review. After PR-1 is human-merged, PR-2 must
  be rebased onto the resulting `main` merge commit and re-verified before it can
  be considered for merge.
- Neither PR makes a production-readiness claim.

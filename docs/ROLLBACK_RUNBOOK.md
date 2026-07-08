# Rollback Runbook (H11) — "Practiced, Boring Rollbacks"

**Scope:** Staging (`regmind-staging`, AWS ECS Fargate, af-south-1). Production
(`app.regmind.co`) is planned/not-yet-provisioned (see `CLAUDE.md`); a prod
placeholder section is at the end and must be completed when prod is stood up.

**Goal:** a known, tested, ~5-minute path back to the previous good version —
including a decision tree for database migrations that are **not** trivially
reversible.

---

## ⚠️ Section 0 — Preconditions (fix these before the runbook is truly "practiced")

The image-rollback substrate mostly exists, but these gaps must be closed or the
5-minute path silently fails:

1. **The rollback tag does not exist.** `CLAUDE.md` says "To rollback:
   `git checkout v4.1-stable`", but `git ls-remote --tags origin` shows only
   `v4.0-stable` and `v5.0-pre-screening-abstraction` — **`v4.1-stable` is not on
   origin.** Action: cut a real stable tag on the current known-good `main`
   (`v4.1-stable` or a fresh `vX-stable`) and push it, then **tag every deploy's
   known-good SHA** going forward. *(Requires a human to pick the canonical good
   SHA; not done in this PR.)*
2. **Confirm ECR retains prior SHA images** (immutable tags + no lifecycle rule
   expiring the last N), and that **prior task-def revisions exist for BOTH
   services** — `regmind-backend` and `regmind-verification-worker`. If a
   lifecycle rule expired the prior image, the 5-minute path is gone → Section 5.
3. **Record, at every deploy, the current-good task-def revision for BOTH
   services and the current-good `GIT_SHA`.** `DEPLOYMENT_RUNBOOK.md:92` already
   prompts this for the API service — **add the worker.**
4. **Confirm RDS backup retention ≥ 7 days + deletion protection + PITR** on
   `regmind-staging-db` (checklist `DEPLOYMENT_RUNBOOK.md:452`). This is the only
   substrate for the destructive-migration branch (B2).

---

## Section 1 — Detect & Decide

**Rollback trigger:** failed post-deploy health (`/api/liveness`, `/api/health`,
`/portal`, `/backoffice` not 200 — same probes the deploy workflow runs at
`deploy-staging.yml:255-290`) **or** a functional regression caught in smoke.

Capture:
- the **bad** `GIT_SHA` — authenticated `GET /api/version` returns
  `git_sha`/`image_tag` (`server.py` VersionHandler).
- the **target good** SHA and task-def revisions for both services (from the
  deploy log / Section 0.3 record).

---

## Section 2 — Image rollback (the ~5-minute path)

No schema change happens on rollback: the old image's boot runs `init_db()`
(idempotent) + the forward-only migration runner, which sees **0 pending**
migrations (an older image has a strict *subset* of migration files) and
releases the boot advisory lock. So this is purely an ECS task-def swap.

```bash
# 1. Find the previous good revisions for BOTH families
aws ecs list-task-definitions --family-prefix regmind-staging --sort DESC --region af-south-1
aws ecs list-task-definitions --family-prefix regmind-verification-worker --sort DESC --region af-south-1

# 2. Point BOTH services back (⚠️ the worker is easy to forget — see Edge cases)
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --task-definition regmind-staging:<PREV_API_REV> --force-new-deployment --region af-south-1
aws ecs update-service --cluster regmind-staging --service regmind-verification-worker \
  --task-definition regmind-verification-worker:<PREV_WORKER_REV> --force-new-deployment --region af-south-1

# 3. Wait for steady state
aws ecs wait services-stable --cluster regmind-staging \
  --services regmind-backend regmind-verification-worker --region af-south-1
```

---

## Section 3 — Database decision tree

**Q1 — Did the bad deploy apply ANY migration?**
Compare the bad-SHA vs target-SHA migration file sets (`python migrate.py status`,
or diff `schema_version` rows). Migrations run fail-closed at boot behind the PG
advisory lock; a bad deploy leaves the schema at the **last fully-applied**
migration.

- **No migration applied →** pure image rollback (Section 2). Done. ~5 min.
- **Migration(s) applied →** classify each migration newer than the target SHA:

**Branch A — additive / idempotent (today: ALL of migrations 001–041).**
`ADD COLUMN`/`CREATE TABLE|INDEX IF NOT EXISTS` or idempotent data-only
`UPDATE`, and every new `NOT NULL` column carries a `DEFAULT` (e.g. 019
`webhook_live`, 040 `FALSE`) so the old image's INSERTs still succeed.
→ **Roll back the image, KEEP the schema.** The old image ignores the extra
columns/tables; the older runner sees 0 pending. **No DB action.** ~5 min.

**Branch B — a future migration is destructive/irreversible** (`DROP`
TABLE/COLUMN, `RENAME`, type-narrowing, or a data-destroying `UPDATE`/backfill)
**AND** it was applied **AND** the target old image can't run against the mutated
schema. Image rollback alone is unsafe. Choose:
- **B1 — Roll forward with a hotfix** *(preferred when data is intact and the
  fault is code, not schema)*: fastest, no data loss.
- **B2 — Restore from snapshot**: declare an incident, scale **both** ECS
  services to 0 (stop writers), RDS point-in-time-restore to the instant
  **before** the destructive migration, repoint `DATABASE_URL`/promote the
  restored instance, then deploy the target old image. **Not a 5-minute path**
  (RDS restore is minutes-to-tens-of-minutes) and **loses all writes since the
  restore point** — requires SCO/compliance sign-off and customer comms (AML
  data).

> **Guardrail that keeps Branch B rare:** enforce **expand/contract
> (expand-only)** migrations — new migrations must be additive and
> backward-compatible for at least one release; never `DROP`/`RENAME` in the
> same deploy that stops using a column. Put any destructive "contract" step in
> a **separate later migration**, only after the old image is fully retired.
> This keeps every deploy in Branch A — the reason the ~5-min path stays boring.

---

## Section 4 — Smoke / confirm the rollback

- Authenticated `GET /api/version` → `git_sha` == the **target good** SHA (API;
  worker provenance via CloudWatch logs / `SERVICE_NAME`).
- `GET /api/liveness` → 200 `ok`; `GET /api/health` → 200; `/portal` + `/backoffice` → 200.
- Authenticated `GET /api/readiness` (admin/sco) → 200 `ready: true`.
- CloudWatch: no `MigrationFailure`, no "connection pool exhausted", no "mock mode".
- Both ECS services: PRIMARY deployment at steady state / desired count.

---

## Section 5 — If the prior SHA image is missing (release incident)

The ~5-min path is gone. Rebuild from the last-good **tag** (now that Section 0.1
guarantees one exists), push that SHA, register task-defs, redeploy. Slower.

---

## Section 6 — Who / what / sign-off

- On-call operator with AWS af-south-1 access executes Section 2.
- A second reviewer confirms `/api/version` SHA + health before closing.
- If the destructive-migration branch (B) is hit — especially **B2 restore** —
  an **SCO/compliance sign-off is required** (regulated AML data; potential data
  loss).

---

## Edge cases & gotchas

- **Split-version fleet:** rolling back `regmind-backend` but NOT
  `regmind-verification-worker` (or vice-versa) leaves API and worker on
  different SHAs sharing one DB. **Always roll back both task-def families.**
- **Boot-lock contention:** the rolled-back task takes the PG advisory lock
  (`boot_lock.py`, key `8674309941`); if a stuck old task still holds it, boot
  fails **loud** within `statement_timeout` rather than racing — drain the bad
  tasks first.
- **`NOT NULL`-without-`DEFAULT` in a future migration** would break old-image
  INSERTs even though it's "additive" — Branch A's test is *additive AND every
  new NOT NULL column has a DEFAULT* (true today: 019, 040).
- **`schema_version` has no downward reconciliation:** newer rows persist and
  are ignored after an image rollback (safe). `migrate.py status` on the old
  image showing fewer known files than applied rows is **expected, not an error**.
- **`MIGRATION_FAILURE_MODE=continue` must NEVER be set in staging/prod** — it
  would let a partially-migrated schema boot, defeating the clean-rollback-target
  guarantee. *(Since P12-4 / DCI-005 this is enforced in code: the runner
  ignores the override in staging/production, logs an ERROR, and keeps the
  fail-closed halt-on-failure policy.)*

---

## Production placeholder (complete when app.regmind.co is provisioned)

Re-validate every command above against the production cluster/service names,
confirm prod RDS PITR + deletion protection, tag the prod known-good SHA, and
add prod `/api/version`+health URLs. Until then this runbook is **staging-only**.

---

## Open items for the human (cannot be closed from code)

1. Create the missing `v4.1-stable` (or fresh `vX-stable`) tag on the canonical
   good SHA and adopt tag-per-known-good-deploy.
2. Confirm (live AWS) ECR retention keeps the last N SHA images immutable and
   that prior task-def revisions for **both** services are retained.
3. Confirm RDS PITR window + snapshot cadence + deletion protection on
   `regmind-staging-db`.
4. Adopt the expand/contract migration policy as a hard rule and decide who
   signs off destructive "contract" migrations.
5. Decide B1 (roll-forward) vs B2 (restore) as the first-choice for the
   destructive branch, and the SCO/compliance authorization path for B2.

"""
PR-A: Lifecycle Data Trust Hardening -- quarantine classifier tests.

Covers the three acceptance criteria that are runtime-testable in CI:

  (1) GET /api/lifecycle/queue?include=active returns ZERO rows that are
      legacy-ghost (state='escalated' AND no linkage AND/OR
      application_id IS NULL).

  (2) include=legacy_unmapped returns exactly the seeded legacy rows.

  (3) Every monitoring_alerts row classifies as exactly one of:
      active, historical, legacy_unmapped (no overlap, no orphan).

  Plus: the audit-log INSERT from migration 012 emits one
  ``lifecycle.alert.quarantined`` row per quarantined alert with the
  expected JSON shape (acceptance criterion 4).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


class _LifecycleQuarantineBase(unittest.TestCase):
    """Sqlite-backed harness mirroring test_lifecycle_queue."""

    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pra_lq_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path
        import config as config_module
        import db as db_module
        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()

        # Pre-mark migrations 001..007 as applied (init_db reflects the
        # post-007 schema; runner would otherwise replay history).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "version TEXT UNIQUE NOT NULL, "
            "filename TEXT NOT NULL, "
            "description TEXT DEFAULT '', "
            "applied_at TEXT DEFAULT (datetime('now')), "
            "checksum TEXT)"
        )
        for v, fn in [
            ("001", "migration_001_initial.sql"),
            ("002", "migration_002_supervisor_tables.sql"),
            ("003", "migration_003_monitoring_indexes.sql"),
            ("004", "migration_004_documents_s3_key.sql"),
            ("005", "migration_005_applications_truth_schema.sql"),
            ("006", "migration_006_person_dob.sql"),
            ("007", "migration_007_screening_reports_normalized.sql"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) "
                "VALUES (?, ?)", (v, fn),
            )
        conn.commit()
        from migrations.runner import run_all_migrations_with_connection
        run_all_migrations_with_connection(conn)

        # Seed an application + an officer.
        self._app_id = "app-pra"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PRA", "PRA Test Co",
                 "Mauritius", "Fintech", "single-tier", "MEDIUM",
                 "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PRA", "PRA Test Co"),
            )
        conn.commit()
        self._conn = conn

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        import config as config_module
        import db as db_module
        config_module.DB_PATH = self._orig_config_db_path
        db_module.DB_PATH = self._orig_db_db_path

    def _alert(self, **kw) -> int:
        defaults = dict(
            application_id=self._app_id,
            client_name="PRA Test Co",
            alert_type="manual",
            severity="High",
            summary="seeded",
            status="open",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO monitoring_alerts ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]


# ─────────────────────────────────────────────────────────────────
# Vocabulary parity (the canonical set must match monitoring_routing)
# ─────────────────────────────────────────────────────────────────
class TestQuarantineVocabularyParity(unittest.TestCase):
    def test_canonical_vocabulary_matches_monitoring_routing(self):
        import lifecycle_quarantine as lq
        import monitoring_routing as mr
        # If PR-02 changes the routing vocabulary the quarantine
        # classifier must be updated explicitly. This test is the
        # tripwire.
        canonical_from_engine = {
            mr.STATUS_OPEN, mr.STATUS_TRIAGED, mr.STATUS_ASSIGNED,
            mr.STATUS_DISMISSED, mr.STATUS_ROUTED_REVIEW, mr.STATUS_ROUTED_EDD,
        }
        self.assertEqual(set(lq.CANONICAL_ALERT_VOCABULARY),
                         canonical_from_engine)


# ─────────────────────────────────────────────────────────────────
# Pure classifier (no DB)
# ─────────────────────────────────────────────────────────────────
class TestPureClassifier(unittest.TestCase):
    def _row(self, **kw):
        defaults = dict(
            application_id="app-1",
            status="open",
            linked_periodic_review_id=None,
            linked_edd_case_id=None,
        )
        defaults.update(kw)
        return defaults

    def test_canonical_active_row_is_not_quarantined(self):
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(self._row(status="open"))
        self.assertFalse(is_q)
        self.assertEqual(reasons, [])

    def test_canonical_historical_row_with_app_is_not_quarantined(self):
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(
            self._row(status="dismissed", application_id="app-1")
        )
        self.assertFalse(is_q)
        self.assertEqual(reasons, [])

    def test_vocabulary_ghost_state_is_quarantined(self):
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(
            self._row(status="escalated", application_id="app-1")
        )
        self.assertTrue(is_q)
        self.assertEqual(reasons, [lq.QUARANTINE_REASON_VOCABULARY_GHOST])

    def test_vocabulary_ghost_with_downstream_linkage_is_NOT_quarantined(self):
        # If a non-canonical state somehow has a downstream object,
        # the linkage rescues it from the vocabulary_ghost predicate
        # (only the unscopable predicate could still apply).
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(
            self._row(status="escalated", application_id="app-1",
                      linked_periodic_review_id=42)
        )
        self.assertFalse(is_q)
        self.assertEqual(reasons, [])

    def test_unscopable_dismissed_row_is_quarantined(self):
        # The brief's id=2: dismissed (canonical) but application_id IS NULL.
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(
            self._row(status="dismissed", application_id=None)
        )
        self.assertTrue(is_q)
        self.assertEqual(reasons, [lq.QUARANTINE_REASON_UNSCOPABLE])

    def test_both_predicates_fire_yields_both_reasons(self):
        # The brief's id=1: escalated AND application_id IS NULL.
        import lifecycle_quarantine as lq
        is_q, reasons = lq.is_legacy_unmapped(
            self._row(status="escalated", application_id=None)
        )
        self.assertTrue(is_q)
        self.assertEqual(reasons, [
            lq.QUARANTINE_REASON_VOCABULARY_GHOST,
            lq.QUARANTINE_REASON_UNSCOPABLE,
        ])

    def test_reason_order_is_stable(self):
        # The audit-log entry shape depends on reason order being
        # deterministic across calls.
        import lifecycle_quarantine as lq
        for _ in range(5):
            _, reasons = lq.is_legacy_unmapped(
                self._row(status="escalated", application_id=None)
            )
            self.assertEqual(reasons, list(lq.QUARANTINE_REASON_ORDER))


# ─────────────────────────────────────────────────────────────────
# Bucket containment + counts preservation (acceptance criteria 1-3)
# ─────────────────────────────────────────────────────────────────
class TestQuarantineBuckets(_LifecycleQuarantineBase):
    """Seed the three brief-described ghost rows + healthy rows, then
    verify active / historical / legacy_unmapped containment."""

    def _seed_brief_ghosts(self):
        # Mirrors the staging ghost-row inventory described in the brief.
        # id A: vocabulary_ghost AND unscopable (matches staging id=1).
        a_id = self._alert(
            client_name="Test Corp Ltd",
            alert_type="Sanctions Match",
            severity="Critical",
            status="escalated",
            application_id=None,
            summary="OFAC SDN List",
        )
        # id B: dismissed but unscopable (matches staging id=2).
        b_id = self._alert(
            client_name="(unknown)",
            status="dismissed",
            application_id=None,
        )
        # id C: vocabulary_ghost only -- has app_id but no linkage
        # (matches staging id=3).
        c_id = self._alert(
            client_name="Staging E2E Corp",
            alert_type="Audit Check",
            severity="Medium",
            status="escalated",
            application_id=self._app_id,
        )
        return a_id, b_id, c_id

    def _seed_healthy_rows(self):
        # Canonical active and historical rows we EXPECT to remain
        # visible in their respective buckets.
        active_id = self._alert(status="open")
        historical_id = self._alert(
            status="dismissed", application_id=self._app_id,
        )
        return active_id, historical_id

    def test_active_bucket_excludes_all_quarantined_rows(self):
        # Acceptance criterion 1: active queue returns zero rows that
        # are escalated-no-linkage and zero that are application_id IS NULL.
        import lifecycle_queue as lq
        a, b, c = self._seed_brief_ghosts()
        active_id, _ = self._seed_healthy_rows()

        result = lq.build_lifecycle_queue(
            self._conn, include="active", types=("alert",),
        )
        ids = {it["id"] for it in result["items"]}
        self.assertEqual(ids, {active_id})
        for it in result["items"]:
            self.assertFalse(it["is_legacy_unmapped"])
            self.assertEqual(it["quarantine_reasons"], [])

    def test_historical_bucket_excludes_quarantined_rows(self):
        # Counts preservation: the unscopable-dismissed ghost (id=B)
        # must NOT appear in historical even though its status is
        # canonical-historical.
        import lifecycle_queue as lq
        a, b, c = self._seed_brief_ghosts()
        _, historical_id = self._seed_healthy_rows()

        result = lq.build_lifecycle_queue(
            self._conn, include="historical", types=("alert",),
        )
        ids = {it["id"] for it in result["items"]}
        self.assertEqual(ids, {historical_id})

    def test_legacy_unmapped_bucket_returns_only_quarantined_rows(self):
        # Acceptance criterion 2: include=legacy_unmapped returns
        # exactly the seeded ghost rows.
        import lifecycle_queue as lq
        a, b, c = self._seed_brief_ghosts()
        self._seed_healthy_rows()

        result = lq.build_lifecycle_queue(
            self._conn, include="legacy_unmapped", types=("alert",),
        )
        ids = {it["id"] for it in result["items"]}
        self.assertEqual(ids, {a, b, c})
        for it in result["items"]:
            self.assertTrue(it["is_legacy_unmapped"])
            self.assertFalse(it["is_active"])
            self.assertFalse(it["is_historical"])
            self.assertGreater(len(it["quarantine_reasons"]), 0)

    def test_quarantine_reasons_are_correct_per_row(self):
        import lifecycle_queue as lq
        import lifecycle_quarantine as lqu
        a, b, c = self._seed_brief_ghosts()
        result = lq.build_lifecycle_queue(
            self._conn, include="legacy_unmapped", types=("alert",),
        )
        by_id = {it["id"]: it for it in result["items"]}
        # Row A: both predicates fire.
        self.assertEqual(by_id[a]["quarantine_reasons"], [
            lqu.QUARANTINE_REASON_VOCABULARY_GHOST,
            lqu.QUARANTINE_REASON_UNSCOPABLE,
        ])
        # Row B: unscopable only.
        self.assertEqual(by_id[b]["quarantine_reasons"], [
            lqu.QUARANTINE_REASON_UNSCOPABLE,
        ])
        # Row C: vocabulary_ghost only.
        self.assertEqual(by_id[c]["quarantine_reasons"], [
            lqu.QUARANTINE_REASON_VOCABULARY_GHOST,
        ])

    def test_every_row_classifies_exactly_once(self):
        # Acceptance criterion 3: every monitoring_alerts row maps to
        # exactly one of: active, historical, legacy_unmapped.
        import lifecycle_queue as lq
        self._seed_brief_ghosts()
        self._seed_healthy_rows()
        # Add additional canonical rows of every status.
        for status in ("triaged", "assigned", "routed_to_review",
                       "routed_to_edd"):
            self._alert(status=status)

        all_rows = self._conn.execute(
            "SELECT id FROM monitoring_alerts"
        ).fetchall()
        all_ids = {r["id"] for r in all_rows}

        active = lq.build_lifecycle_queue(
            self._conn, include="active", types=("alert",),
        )["items"]
        historical = lq.build_lifecycle_queue(
            self._conn, include="historical", types=("alert",),
        )["items"]
        quarantined = lq.build_lifecycle_queue(
            self._conn, include="legacy_unmapped", types=("alert",),
        )["items"]

        active_ids = {it["id"] for it in active}
        historical_ids = {it["id"] for it in historical}
        quarantined_ids = {it["id"] for it in quarantined}

        # Disjointness: no row appears in more than one bucket.
        self.assertEqual(active_ids & historical_ids, set())
        self.assertEqual(active_ids & quarantined_ids, set())
        self.assertEqual(historical_ids & quarantined_ids, set())

        # Coverage: every alert row appears in exactly one bucket.
        self.assertEqual(
            active_ids | historical_ids | quarantined_ids,
            all_ids,
        )

    def test_include_all_excludes_quarantined(self):
        # ``all`` returns active+historical and never includes
        # quarantined rows -- legacy must be opt-in.
        import lifecycle_queue as lq
        a, b, c = self._seed_brief_ghosts()
        active_id, historical_id = self._seed_healthy_rows()

        result = lq.build_lifecycle_queue(
            self._conn, include="all", types=("alert",),
        )
        ids = {it["id"] for it in result["items"]}
        self.assertEqual(ids, {active_id, historical_id})
        # Belt-and-braces: counts match.
        self.assertEqual(result["counts"]["alert"], 2)

    def test_legacy_bucket_has_no_reviews_or_edd(self):
        # Quarantine is monitoring_alerts-only; the bucket must be empty
        # for review/edd types because their states are CHECK-constrained.
        import lifecycle_queue as lq
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, status, risk_level, trigger_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._app_id, "X", "pending", "MEDIUM", "time_based"),
        )
        self._conn.execute(
            "INSERT INTO edd_cases "
            "(application_id, client_name, stage, risk_level, trigger_source) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._app_id, "X", "triggered", "HIGH", "officer_decision"),
        )
        self._conn.commit()

        result = lq.build_lifecycle_queue(
            self._conn, include="legacy_unmapped",
        )
        kinds = {it["type"] for it in result["items"]}
        self.assertNotIn("review", kinds)
        self.assertNotIn("edd", kinds)


# ─────────────────────────────────────────────────────────────────
# Migration 012 audit-log emission (acceptance criterion 4)
# ─────────────────────────────────────────────────────────────────
class TestMigration012AuditEmission(_LifecycleQuarantineBase):
    """Migration 012 INSERTs an audit_log row per legacy alert seen
    when the migration was applied. Setup applies the migration AFTER
    seeding so we can observe the emission."""

    def setUp(self):
        # Reuse parent harness but DO NOT run migration 012 yet --
        # parent.setUp already ran all migrations, so we have to
        # delete the audit rows it produced (zero rows existed before
        # seeding) and re-run the migration manually after seeding.
        super().setUp()
        # Make sure we know migration 012 is applied (parent ran it).
        applied = {r["version"] for r in self._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchall()}
        self.assertIn("012", applied,
                      "migration 012 must be present in the catalogue")

    def _rerun_migration_012(self):
        # Idempotent re-execution for the test: load the SQL file and
        # executescript it directly (bypassing schema_version since the
        # migration is already recorded).
        from pathlib import Path
        sql_path = (
            Path(__file__).parent.parent
            / "migrations" / "scripts"
            / "migration_012_legacy_unmapped_audit_classification.sql"
        )
        sql = sql_path.read_text(encoding="utf-8")
        self._conn.executescript(sql)
        self._conn.commit()

    def test_audit_log_row_emitted_for_each_quarantined_row(self):
        # Seed three ghosts + a healthy row, re-run migration to emit
        # audit entries for the new rows, then verify shape.
        a = self._alert(status="escalated", application_id=None,
                        client_name="Test Corp Ltd")
        b = self._alert(status="dismissed", application_id=None)
        c = self._alert(status="escalated", application_id=self._app_id,
                        client_name="Staging E2E Corp")
        # Healthy control row -- must NOT produce an audit entry.
        healthy = self._alert(status="open")

        # Clear any audit_log entries from earlier migration run, then
        # re-execute migration 012 so the emissions cover our seeds.
        self._conn.execute(
            "DELETE FROM audit_log WHERE action = 'lifecycle.alert.quarantined'"
        )
        self._conn.commit()
        self._rerun_migration_012()

        rows = self._conn.execute(
            "SELECT user_id, user_role, action, target, "
            "       detail, before_state, after_state, ip_address "
            "FROM audit_log WHERE action = 'lifecycle.alert.quarantined' "
            "ORDER BY id"
        ).fetchall()
        # One per quarantined alert; healthy row is absent.
        targets = {r["target"] for r in rows}
        self.assertEqual(targets, {
            f"monitoring_alert:{a}",
            f"monitoring_alert:{b}",
            f"monitoring_alert:{c}",
        })
        self.assertNotIn(f"monitoring_alert:{healthy}", targets)

        # Each row carries a system actor.
        for r in rows:
            self.assertEqual(r["user_role"], "system")
            self.assertEqual(r["user_id"], "system:lifecycle-quarantine")
            # ip_address mirrors precedent for system-emitted rows
            # (omitted from the column list -> NULL); it must NOT be a
            # made-up sentinel like the literal string "system".
            self.assertIsNone(r["ip_address"])

            # detail column contains CLASSIFICATION METADATA ONLY --
            # no row state, no before/after embedded.
            detail = json.loads(r["detail"])
            self.assertEqual(detail["classification"], "legacy_unmapped")
            self.assertIn("reasons", detail)
            self.assertGreater(len(detail["reasons"]), 0)
            for reason in detail["reasons"]:
                self.assertIn(reason, (
                    "vocabulary_ghost", "unscopable_no_application",
                ))
            self.assertEqual(
                detail["migration"],
                "012_legacy_unmapped_audit_classification",
            )
            # Defensive: state keys must NOT leak back into detail.
            self.assertNotIn("before_state", detail)
            self.assertNotIn("after_state", detail)
            self.assertNotIn("status", detail)
            self.assertNotIn("application_id", detail)

            # before_state is a dedicated JSON column; populated.
            self.assertIsNotNone(r["before_state"])
            before = json.loads(r["before_state"])
            self.assertIn("id", before)
            self.assertIn("status", before)
            self.assertIn("application_id", before)
            self.assertIn("linked_periodic_review_id", before)
            self.assertIn("linked_edd_case_id", before)
            self.assertEqual(before["bucket"], "hidden_ghost")

            # after_state is a dedicated JSON column; single-field marker.
            self.assertIsNotNone(r["after_state"])
            after = json.loads(r["after_state"])
            self.assertEqual(after, {"bucket": "legacy_unmapped"})

    def test_audit_payload_reasons_match_classifier_per_row(self):
        a = self._alert(status="escalated", application_id=None)
        b = self._alert(status="dismissed", application_id=None)
        c = self._alert(status="escalated", application_id=self._app_id)
        self._conn.execute(
            "DELETE FROM audit_log WHERE action = 'lifecycle.alert.quarantined'"
        )
        self._conn.commit()
        self._rerun_migration_012()
        rows = self._conn.execute(
            "SELECT target, detail, before_state FROM audit_log "
            "WHERE action = 'lifecycle.alert.quarantined'"
        ).fetchall()
        by_target = {
            r["target"]: (json.loads(r["detail"]), json.loads(r["before_state"]))
            for r in rows
        }
        # detail.reasons must be the classifier's output, in stable order.
        self.assertEqual(
            by_target[f"monitoring_alert:{a}"][0]["reasons"],
            ["vocabulary_ghost", "unscopable_no_application"],
        )
        self.assertEqual(
            by_target[f"monitoring_alert:{b}"][0]["reasons"],
            ["unscopable_no_application"],
        )
        self.assertEqual(
            by_target[f"monitoring_alert:{c}"][0]["reasons"],
            ["vocabulary_ghost"],
        )
        # before_state.application_id mirrors the row's actual value.
        self.assertIsNone(by_target[f"monitoring_alert:{a}"][1]["application_id"])
        self.assertIsNone(by_target[f"monitoring_alert:{b}"][1]["application_id"])
        self.assertEqual(
            by_target[f"monitoring_alert:{c}"][1]["application_id"],
            self._app_id,
        )

    def test_audit_json_escapes_quotes_and_backslashes_in_status(self):
        # Defensive: the SQL JSON construction REPLACEs backslashes
        # then quotes so a status (or application_id) with either
        # special character still produces parseable JSON. Practical
        # statuses are short slugs but the migration must not assume so.
        # See the SQL comment block "INPUT-ALPHABET ASSUMPTION" for
        # the bounded scope of this defence (printable ASCII; no
        # control characters such as \n, \t, \u0000).
        tricky_app = "app-x\"with\\backslash"
        # Seed application + alert with the tricky id.
        try:
            self._conn.execute(
                "INSERT INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (tricky_app, "TRICKY", "Tricky Co"),
            )
        except Exception:
            pass
        self._conn.commit()
        self._alert(
            application_id=tricky_app,
            status='escalated"with\\backslash',
        )
        self._conn.execute(
            "DELETE FROM audit_log WHERE action = 'lifecycle.alert.quarantined'"
        )
        self._conn.commit()
        self._rerun_migration_012()
        rows = self._conn.execute(
            "SELECT before_state FROM audit_log "
            "WHERE action = 'lifecycle.alert.quarantined'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        # If escaping is correct, json.loads succeeds and round-trips
        # the original strings exactly. before_state is now a dedicated
        # column (not nested in detail).
        before = json.loads(rows[0]["before_state"])
        self.assertEqual(before["status"],
                         'escalated"with\\backslash')
        self.assertEqual(before["application_id"],
                         'app-x"with\\backslash')

    def test_migration_is_self_idempotent_when_run_twice(self):
        # PR-A follow-up #1: the migration must not duplicate audit
        # rows when executed twice in a row, even if the schema_version
        # gate is bypassed (DBA re-run, test harness, etc).
        self._alert(status="escalated", application_id=None)
        self._alert(status="dismissed", application_id=None)
        self._alert(status="escalated", application_id=self._app_id)
        self._conn.execute(
            "DELETE FROM audit_log WHERE action = 'lifecycle.alert.quarantined'"
        )
        self._conn.commit()

        self._rerun_migration_012()
        first_count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log "
            "WHERE action = 'lifecycle.alert.quarantined'"
        ).fetchone()["c"]
        self.assertEqual(first_count, 3)

        # Second run -- no schema_version touch, just re-execute the SQL.
        self._rerun_migration_012()
        second_count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log "
            "WHERE action = 'lifecycle.alert.quarantined'"
        ).fetchone()["c"]
        self.assertEqual(second_count, 3,
                         "migration must self-guard via NOT EXISTS so "
                         "double-execution does not duplicate audit rows")


# ─────────────────────────────────────────────────────────────────
# Truth-layer parity: UI-facing read agrees with classifier
# ─────────────────────────────────────────────────────────────────
class TestUIReadsAgreeWithClassifier(_LifecycleQuarantineBase):
    """Standing constraint: UI reads must show the same truth as the
    lifecycle endpoints. Verifies that the materialised payload's
    ``is_legacy_unmapped`` flag agrees with the pure classifier for
    every row returned, in every bucket."""

    def test_materialised_flag_matches_pure_classifier(self):
        import lifecycle_queue as lq
        import lifecycle_quarantine as lqu
        # Mix of canonical + ghost rows.
        self._alert(status="open")
        self._alert(status="dismissed", application_id=self._app_id)
        self._alert(status="escalated", application_id=None)
        self._alert(status="dismissed", application_id=None)
        self._alert(status="escalated", application_id=self._app_id)

        for include in ("active", "historical", "all", "legacy_unmapped"):
            result = lq.build_lifecycle_queue(
                self._conn, include=include, types=("alert",),
            )
            for it in result["items"]:
                raw = self._conn.execute(
                    "SELECT * FROM monitoring_alerts WHERE id = ?",
                    (it["id"],),
                ).fetchone()
                pure_q, pure_reasons = lqu.is_legacy_unmapped(raw)
                self.assertEqual(it["is_legacy_unmapped"], pure_q,
                                 f"mismatch on id={it['id']} include={include}")
                self.assertEqual(it["quarantine_reasons"], pure_reasons,
                                 f"reason mismatch on id={it['id']}")


# ─────────────────────────────────────────────────────────────────
# SQL-side vocabulary parity (PR-A review blocker 2, option B)
# ─────────────────────────────────────────────────────────────────
class TestSqlVocabularyParity(unittest.TestCase):
    """Migration 012 hardcodes the canonical PR-02 vocabulary in three
    SQL ``IN (...)`` lists. This test reads the migration file as text,
    extracts every status literal, and asserts set equality with
    monitoring_routing.STATUS_*. If PR-02 ever renames a canonical
    status, the Python tripwire fires AND this SQL tripwire fires --
    so the migration's classification cannot drift silently from the
    runtime classifier."""

    def test_sql_vocabulary_matches_monitoring_routing(self):
        import re
        from pathlib import Path
        import monitoring_routing as mr

        sql_path = (
            Path(__file__).parent.parent
            / "migrations" / "scripts"
            / "migration_012_legacy_unmapped_audit_classification.sql"
        )
        sql = sql_path.read_text(encoding="utf-8")
        # Strip line comments so we only inspect executable SQL.
        executable = "\n".join(
            line.split("--", 1)[0] for line in sql.splitlines()
        )

        canonical_from_engine = {
            mr.STATUS_OPEN, mr.STATUS_TRIAGED, mr.STATUS_ASSIGNED,
            mr.STATUS_DISMISSED, mr.STATUS_ROUTED_REVIEW, mr.STATUS_ROUTED_EDD,
        }

        # Find every ``status NOT IN (...)`` clause and extract its
        # quoted string literals.
        in_clauses = re.findall(
            r"status\s+NOT\s+IN\s*\(([^)]*)\)",
            executable, flags=re.IGNORECASE,
        )
        self.assertGreater(
            len(in_clauses), 0,
            "expected at least one ``status NOT IN (...)`` clause in "
            "migration 012; the regex extractor found none",
        )
        # Every IN clause must carry the FULL canonical vocabulary --
        # not a subset, not an extension. A drifted clause is the
        # silent-misclassification failure mode this test prevents.
        for clause in in_clauses:
            literals = set(re.findall(r"'([^']*)'", clause))
            self.assertEqual(
                literals, canonical_from_engine,
                f"SQL IN-clause vocabulary {literals!r} drifted from "
                f"monitoring_routing.STATUS_* {canonical_from_engine!r}; "
                "update both the Python constant in lifecycle_quarantine "
                "AND every IN-clause in migration 012 together.",
            )


# ─────────────────────────────────────────────────────────────────
# Audit-row shape parity with canonical lifecycle.* emitters
# ─────────────────────────────────────────────────────────────────
class TestQuarantineAuditRowShapeMatchesLifecycleLinkAuditRow(
        _LifecycleQuarantineBase):
    """Reviewer's "byte-identical column population pattern" test.

    Seed one ``lifecycle.link.alert_to_review.created`` audit row via
    the canonical writer (lifecycle_linkage._emit_audit invoked
    indirectly through link_alert_to_review), and one
    ``lifecycle.alert.quarantined`` row via migration 012. Diff the
    set of columns that are non-NULL on each row. The two sets MUST
    be identical, proving the quarantine row uses the same column
    population pattern as every other lifecycle.* row in the system.

    This is the literal proof for the PR description claim "No new
    audit format is invented."
    """

    def _make_canonical_lifecycle_audit_row(self):
        # Use lifecycle_linkage's link_alert_to_review which calls
        # _emit_audit -> audit_writer (i.e. our test audit_writer here).
        # This produces a ``lifecycle.link.alert_to_review.created``
        # row written through the SAME path BaseHandler.log_audit takes.
        import lifecycle_linkage as ll

        # The lifecycle_linkage audit_writer signature:
        #   audit_writer(user, action, target, detail,
        #                db=None, before_state=None, after_state=None)
        # Mirror BaseHandler.log_audit but without ip_address (no
        # request context in a unit test).
        def _audit_writer(user, action, target, detail,
                          db=None, before_state=None, after_state=None):
            import json as _json
            db.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, "
                "action, target, detail, before_state, after_state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user.get("sub", ""), user.get("name", ""),
                    user.get("role", ""),
                    action, target, detail,
                    None if before_state is None else _json.dumps(before_state),
                    None if after_state is None else _json.dumps(after_state),
                ),
            )
            db.commit()

        # Seed an alert + a review, then link them.
        alert_id = self._alert(status="open")
        self._conn.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, status, risk_level, trigger_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._app_id, "PRA", "pending", "MEDIUM", "time_based"),
        )
        self._conn.commit()
        review_id = self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        ll.link_alert_to_review(
            self._conn,
            alert_id,
            review_id,
            user={"sub": "officer-pra", "name": "Officer", "role": "co"},
            audit_writer=_audit_writer,
        )

    def _column_population_pattern(self, row):
        # Map column -> True if non-NULL/non-empty-string.
        return {
            col: (row[col] is not None and row[col] != "")
            for col in (
                "user_id", "user_name", "user_role",
                "action", "target", "detail",
                "ip_address", "before_state", "after_state",
            )
        }

    def test_quarantine_audit_row_shape_matches_lifecycle_link_audit_row(self):
        # Step 1: canonical lifecycle.link.alert_to_review.created row.
        self._make_canonical_lifecycle_audit_row()

        # Step 2: emit a lifecycle.alert.quarantined row via the migration.
        self._alert(status="escalated", application_id=None)
        self._conn.execute(
            "DELETE FROM audit_log WHERE action = 'lifecycle.alert.quarantined'"
        )
        self._conn.commit()
        # Inline re-execution of migration SQL (bypassing schema_version).
        from pathlib import Path
        sql_path = (
            Path(__file__).parent.parent
            / "migrations" / "scripts"
            / "migration_012_legacy_unmapped_audit_classification.sql"
        )
        self._conn.executescript(sql_path.read_text(encoding="utf-8"))
        self._conn.commit()

        # Step 3: fetch one of each.
        link_row = self._conn.execute(
            "SELECT user_id, user_name, user_role, action, target, "
            "       detail, ip_address, before_state, after_state "
            "FROM audit_log WHERE action LIKE 'lifecycle.link.%' LIMIT 1"
        ).fetchone()
        quarantine_row = self._conn.execute(
            "SELECT user_id, user_name, user_role, action, target, "
            "       detail, ip_address, before_state, after_state "
            "FROM audit_log WHERE action = 'lifecycle.alert.quarantined' "
            "LIMIT 1"
        ).fetchone()

        self.assertIsNotNone(link_row,
                             "canonical lifecycle.link.* row was not seeded")
        self.assertIsNotNone(quarantine_row,
                             "migration 012 did not emit a quarantine row")

        link_pattern = self._column_population_pattern(link_row)
        quarantine_pattern = self._column_population_pattern(quarantine_row)

        # The two non-null column sets must be IDENTICAL. If migration
        # 012 ever stuffs before/after back into detail (or omits
        # before_state / after_state entirely), this test fails loudly.
        self.assertEqual(
            link_pattern, quarantine_pattern,
            "lifecycle.alert.quarantined row column population pattern "
            f"({quarantine_pattern}) does not match the canonical "
            f"lifecycle.link.* row pattern ({link_pattern}). The PR-A "
            "claim 'No new audit format is invented' is broken.",
        )

        # Also: detail on the quarantine row must contain classification
        # metadata only, while before_state / after_state are populated
        # in their dedicated columns.
        import json as _json
        q_detail = _json.loads(quarantine_row["detail"])
        self.assertNotIn("before_state", q_detail)
        self.assertNotIn("after_state", q_detail)
        self.assertIn("classification", q_detail)
        self.assertIsNotNone(quarantine_row["before_state"])
        self.assertIsNotNone(quarantine_row["after_state"])


if __name__ == "__main__":
    unittest.main()

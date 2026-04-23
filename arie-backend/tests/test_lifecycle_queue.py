"""
PR-05: lifecycle queue aggregator -- engine-level tests.

These exercise ``lifecycle_queue.build_lifecycle_queue`` and
``lifecycle_queue.build_application_lifecycle_summary`` directly
against a real (sqlite) database, with no HTTP layer. They prove:

  * active vs historical partitioning matches the PR-02 / PR-03 / PR-04
    terminal vocabularies
  * type filtering works for alerts / reviews / edd
  * ownership names are batch-resolved from the users table
  * aging is reported in seconds and days
  * required-item count surfaces from periodic_reviews.required_items
  * PR-04 active memo context surfaces on EDD items, including the
    PR-04a onboarding-attachment-confirmed flag
  * PR-02 reverse-link displacement is honoured (alerts terminal once
    routed, with linkage IDs visible)
  * PR-03 outcome and legacy decision are surfaced as DISJOINT fields
  * application-summary view emits the cross-table linkage edge set
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


class _LifecycleQueueBase(unittest.TestCase):
    """Minimal sqlite-backed harness; mirrors test_periodic_review_handlers."""

    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr05_lq_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        # post-007 schema; runner would otherwise replay history and
        # fail with duplicate-column errors).
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

        # Seed an application + an officer
        self._app_id = "app-pr05"
        try:
            conn.execute(
                "INSERT INTO applications "
                "(id, ref, company_name, country, sector, "
                " ownership_structure, risk_level, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._app_id, "APP-PR05", "PR05 Test Co",
                 "Mauritius", "Fintech", "single-tier", "MEDIUM",
                 "approved"),
            )
        except Exception:
            conn.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name) "
                "VALUES (?, ?, ?)",
                (self._app_id, "APP-PR05", "PR05 Test Co"),
            )

        self._officer_id = "officer-pr05"
        conn.execute(
            "INSERT OR IGNORE INTO users "
            "(id, email, password_hash, full_name, role) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._officer_id, "officer-pr05@example.com",
             "x", "Officer Five", "co"),
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

    # ── seed helpers ────────────────────────────────────────────────
    def _alert(self, **kw) -> int:
        # NOTE: application_id must be non-null unless you want PR-A to
        # quarantine this row (lifecycle_quarantine.is_legacy_unmapped's
        # unscopable_no_application predicate fires on application_id IS
        # NULL and pulls the row out of active/historical buckets).
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 Test Co",
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

    def _review(self, **kw) -> int:
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 Test Co",
            risk_level="MEDIUM",
            status="pending",
            trigger_type="time_based",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO periodic_reviews ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _edd(self, **kw) -> int:
        defaults = dict(
            application_id=self._app_id,
            client_name="PR05 Test Co",
            risk_level="HIGH",
            stage="triggered",
            trigger_source="officer_decision",
        )
        defaults.update(kw)
        cols = ",".join(defaults.keys())
        ph = ",".join(["?"] * len(defaults))
        self._conn.execute(
            f"INSERT INTO edd_cases ({cols}) VALUES ({ph})",
            tuple(defaults.values()),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]


# ───────────────────────────────────────────────────────────────────
# Vocabulary parity with engines (PR-02 / PR-03)
# ───────────────────────────────────────────────────────────────────
class TestVocabularyParity(_LifecycleQueueBase):
    def test_alert_terminal_set_matches_monitoring_routing(self):
        import lifecycle_queue as lq
        import monitoring_routing as mr
        # Sets must be equal. If PR-02 changes the terminal vocabulary
        # the queue must be updated explicitly -- this test is the
        # tripwire.
        self.assertEqual(set(lq.HISTORICAL_ALERT_STATUSES),
                         set(mr.TERMINAL_ALERT_STATUSES))

    def test_edd_terminal_set_matches_monitoring_routing(self):
        import lifecycle_queue as lq
        import monitoring_routing as mr
        self.assertEqual(set(lq.HISTORICAL_EDD_STAGES),
                         set(mr.TERMINAL_EDD_STAGES))


# ───────────────────────────────────────────────────────────────────
# Active vs historical partitioning
# ───────────────────────────────────────────────────────────────────
class TestActiveVsHistorical(_LifecycleQueueBase):
    def test_default_include_active_excludes_historical(self):
        import lifecycle_queue as lq
        self._alert(status="open")
        self._alert(status="dismissed")
        self._review(status="pending")
        self._review(status="completed")
        self._edd(stage="triggered")
        self._edd(stage="edd_approved")

        result = lq.build_lifecycle_queue(self._conn)
        kinds = {(it["type"], it["state"]) for it in result["items"]}
        # Active rows are present
        self.assertIn(("alert", "open"), kinds)
        self.assertIn(("review", "pending"), kinds)
        self.assertIn(("edd", "triggered"), kinds)
        # Historical rows are NOT present
        self.assertNotIn(("alert", "dismissed"), kinds)
        self.assertNotIn(("review", "completed"), kinds)
        self.assertNotIn(("edd", "edd_approved"), kinds)
        # Counts reflect active only
        self.assertEqual(result["counts"]["alert"], 1)
        self.assertEqual(result["counts"]["review"], 1)
        self.assertEqual(result["counts"]["edd"], 1)
        self.assertEqual(result["counts"]["total"], 3)

    def test_include_historical_returns_only_terminal(self):
        import lifecycle_queue as lq
        self._alert(status="open")
        self._alert(status="routed_to_review")
        self._review(status="completed")
        self._edd(stage="edd_rejected")

        result = lq.build_lifecycle_queue(self._conn, include="historical")
        for it in result["items"]:
            self.assertTrue(it["is_historical"])
            self.assertFalse(it["is_active"])

    def test_include_all_returns_both(self):
        import lifecycle_queue as lq
        self._alert(status="open")
        self._alert(status="dismissed")
        result = lq.build_lifecycle_queue(self._conn, include="all", types=("alert",))
        states = {it["state"] for it in result["items"]}
        self.assertEqual(states, {"open", "dismissed"})

    def test_invalid_include_raises_value_error(self):
        import lifecycle_queue as lq
        with self.assertRaises(ValueError):
            lq.build_lifecycle_queue(self._conn, include="bogus")


# ───────────────────────────────────────────────────────────────────
# Type filter
# ───────────────────────────────────────────────────────────────────
class TestTypeFilter(_LifecycleQueueBase):
    def test_alerts_only(self):
        import lifecycle_queue as lq
        self._alert(); self._review(); self._edd()
        r = lq.build_lifecycle_queue(self._conn, types=("alert",))
        self.assertEqual({it["type"] for it in r["items"]}, {"alert"})
        self.assertEqual(r["counts"]["review"], 0)
        self.assertEqual(r["counts"]["edd"], 0)

    def test_reviews_only(self):
        import lifecycle_queue as lq
        self._alert(); self._review(); self._edd()
        r = lq.build_lifecycle_queue(self._conn, types=("review",))
        self.assertEqual({it["type"] for it in r["items"]}, {"review"})

    def test_edd_only(self):
        import lifecycle_queue as lq
        self._alert(); self._review(); self._edd()
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        self.assertEqual({it["type"] for it in r["items"]}, {"edd"})

    def test_unknown_type_raises(self):
        import lifecycle_queue as lq
        with self.assertRaises(ValueError):
            lq.build_lifecycle_queue(self._conn, types=("bogus",))


# ───────────────────────────────────────────────────────────────────
# Ownership / aging / next-action surfacing
# ───────────────────────────────────────────────────────────────────
class TestOwnershipAndAging(_LifecycleQueueBase):
    def test_owner_name_resolved_from_users_table(self):
        import lifecycle_queue as lq
        self._edd(assigned_officer=self._officer_id, stage="analysis")
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        self.assertEqual(len(r["items"]), 1)
        item = r["items"][0]
        self.assertEqual(item["owner_id"], self._officer_id)
        self.assertEqual(item["owner_name"], "Officer Five")

    def test_age_seconds_and_days_computed(self):
        import lifecycle_queue as lq
        # Insert an alert with a created_at 3 days in the past
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(
            sep=" ", timespec="seconds"
        ).replace("+00:00", "")
        self._alert(created_at=past)
        r = lq.build_lifecycle_queue(self._conn, types=("alert",))
        item = r["items"][0]
        self.assertIsNotNone(item["age_seconds"])
        self.assertGreaterEqual(item["age_days"], 2)
        self.assertLessEqual(item["age_days"], 4)

    def test_next_action_hint_present(self):
        import lifecycle_queue as lq
        self._alert(status="open")
        self._review(status="awaiting_information")
        self._edd(stage="pending_senior_review")
        r = lq.build_lifecycle_queue(self._conn)
        by_type = {it["type"]: it["next_action"] for it in r["items"]}
        self.assertIn("Triage", by_type["alert"])
        self.assertIn("Awaiting", by_type["review"])
        self.assertIn("Senior", by_type["edd"])

    def test_active_queue_orders_oldest_first(self):
        import lifecycle_queue as lq
        # Two alerts, one older than the other
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(
            sep=" ", timespec="seconds").replace("+00:00", "")
        new = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
            sep=" ", timespec="seconds").replace("+00:00", "")
        old_id = self._alert(created_at=old)
        new_id = self._alert(created_at=new)
        r = lq.build_lifecycle_queue(self._conn, types=("alert",))
        self.assertEqual(r["items"][0]["id"], old_id)
        self.assertEqual(r["items"][1]["id"], new_id)


# ───────────────────────────────────────────────────────────────────
# Linkage surfacing & PR-03 outcome semantics
# ───────────────────────────────────────────────────────────────────
class TestLinkageAndOutcome(_LifecycleQueueBase):
    def test_alert_linked_to_review_surfaced(self):
        import lifecycle_queue as lq
        rid = self._review(status="in_progress")
        # PR-02 reality: a routed alert is HISTORICAL but linkage is
        # still visible (so officers can navigate to the downstream).
        aid = self._alert(status="routed_to_review",
                          linked_periodic_review_id=rid)
        r = lq.build_lifecycle_queue(self._conn, include="historical",
                                     types=("alert",))
        self.assertEqual(r["items"][0]["linked_periodic_review_id"], rid)

    def test_review_outcome_disjoint_from_legacy_decision(self):
        import lifecycle_queue as lq
        # PR-03a: outcome is the source of truth; legacy decision is
        # preserved unchanged. The aggregator MUST surface both as
        # disjoint fields and never collapse them.
        rid = self._review(status="completed",
                           outcome="enhanced_monitoring",
                           outcome_reason="elevated risk indicators",
                           decision="continue",
                           decision_reason="legacy")
        r = lq.build_lifecycle_queue(self._conn, include="historical",
                                     types=("review",))
        self.assertEqual(len(r["items"]), 1)
        item = r["items"][0]
        self.assertEqual(item["outcome"], "enhanced_monitoring")
        self.assertEqual(item["legacy_decision"], "continue")
        self.assertEqual(item["outcome_reason"], "elevated risk indicators")
        self.assertEqual(item["id"], rid)


# ───────────────────────────────────────────────────────────────────
# Required items (PR-03)
# ───────────────────────────────────────────────────────────────────
class TestRequiredItems(_LifecycleQueueBase):
    def test_required_items_count_surfaced(self):
        import lifecycle_queue as lq
        items_payload = json.dumps([
            {"code": "kyc_refresh", "label": "Refresh KYC",
             "rationale": "annual"},
            {"code": "ubo_confirmation", "label": "Confirm UBOs",
             "rationale": "annual"},
        ])
        self._review(status="in_progress",
                     required_items=items_payload,
                     required_items_generated_at=datetime.now(
                         timezone.utc).isoformat())
        r = lq.build_lifecycle_queue(self._conn, types=("review",))
        item = r["items"][0]
        self.assertEqual(item["required_items_count"], 2)
        self.assertIsNotNone(item["required_items_generated_at"])

    def test_required_items_zero_when_unset(self):
        import lifecycle_queue as lq
        self._review(status="pending")
        r = lq.build_lifecycle_queue(self._conn, types=("review",))
        self.assertEqual(r["items"][0]["required_items_count"], 0)


# ───────────────────────────────────────────────────────────────────
# EDD memo context (PR-04 / PR-04a)
# ───────────────────────────────────────────────────────────────────
class TestEDDMemoContext(_LifecycleQueueBase):
    def _seed_compliance_memo(self) -> int:
        # Minimal compliance_memos row so resolve_active_memo_context
        # can return a confirmed onboarding context.
        self._conn.execute(
            "INSERT INTO compliance_memos "
            "(application_id, version, memo_data) VALUES (?,?,?)",
            (self._app_id, 1, "{}"),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM compliance_memos ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def test_edd_memo_context_onboarding_confirmed(self):
        import lifecycle_queue as lq
        memo_id = self._seed_compliance_memo()
        self._edd(stage="analysis", origin_context="onboarding")
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        ctx = r["items"][0]["memo_context"]
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["kind"], "onboarding")
        self.assertEqual(ctx["memo_id"], memo_id)
        self.assertFalse(ctx["unresolved"])
        # PR-04a: onboarding context with a real memo_id is confirmed.
        self.assertTrue(ctx["onboarding_attachment_confirmed"])

    def test_edd_memo_context_periodic_review_kind(self):
        import lifecycle_queue as lq
        rid = self._review(status="in_progress")
        eid = self._edd(stage="analysis",
                        origin_context="periodic_review",
                        linked_periodic_review_id=rid)
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        item = next(i for i in r["items"] if i["id"] == eid)
        ctx = item["memo_context"]
        self.assertEqual(ctx["kind"], "periodic_review")
        self.assertEqual(ctx["periodic_review_id"], rid)
        self.assertFalse(ctx["unresolved"])

    def test_edd_memo_context_unresolved_surfaced_not_swallowed(self):
        import lifecycle_queue as lq
        # PR-04 contract: an EDD with origin='periodic_review' but no
        # explicit linkage MUST raise MemoContextResolutionError. The
        # queue must surface this as ``unresolved=True`` (not crash,
        # not silently invent context).
        self._edd(stage="analysis", origin_context="periodic_review")
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        ctx = r["items"][0]["memo_context"]
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx["unresolved"])
        self.assertIsNone(ctx["kind"])


# ───────────────────────────────────────────────────────────────────
# Findings present flag
# ───────────────────────────────────────────────────────────────────
class TestFindingsPresent(_LifecycleQueueBase):
    def test_findings_present_true_when_row_exists(self):
        import lifecycle_queue as lq
        eid = self._edd(stage="analysis", origin_context="onboarding")
        self._conn.execute(
            "INSERT INTO edd_findings (edd_case_id, findings_summary) "
            "VALUES (?, ?)", (eid, "draft"),
        )
        self._conn.commit()
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        self.assertTrue(r["items"][0]["findings_present"])

    def test_findings_present_false_by_default(self):
        import lifecycle_queue as lq
        self._edd(stage="analysis", origin_context="onboarding")
        r = lq.build_lifecycle_queue(self._conn, types=("edd",))
        self.assertFalse(r["items"][0]["findings_present"])


# ───────────────────────────────────────────────────────────────────
# Fixture payload normalization (seeded read path)
# ───────────────────────────────────────────────────────────────────
class TestFixturePayloadNormalization(_LifecycleQueueBase):
    def test_fixture_review_payload_maps_to_completed_and_linkage(self):
        import lifecycle_queue as lq
        alert_id = self._alert(status="in_review", source_reference="FIX_SCEN04_ALERT")
        payload = json.dumps({
            "status": "fixture_completed",
            "source_alert_id": alert_id,
            "review_memo": "fixture memo body",
            "outcome": "continue_monitoring",
        })
        self._review(
            status="pending",
            trigger_type="fixture_completed",
            trigger_reason="FIX_SCEN04_REVIEW FIX_REVIEW_JSON:" + payload,
            decision="continue_monitoring",
        )
        result = lq.build_lifecycle_queue(self._conn, include="historical", types=("review",))
        self.assertEqual(len(result["items"]), 1)
        review = result["items"][0]
        self.assertEqual(review["state"], "completed")
        self.assertEqual(review["linked_monitoring_alert_id"], alert_id)
        self.assertEqual(review["outcome"], "continue_monitoring")
        self.assertTrue(review["is_historical"])

    def test_non_fixture_edd_unresolved_memo_context_not_overwritten_by_fixture_guard(self):
        """Non-fixture EDD whose linked_periodic_review_id points at a review
        that does not exist in the DB produces an 'unresolved' memo_context
        from _safe_resolve_memo_context.  The fixture-normalization guard must
        NOT overwrite that with resolution_reason='fixture_payload_source_review_id'
        because no FIX_EDD_JSON sentinel is present.

        Before the payload guard fix, the overwrite would fire because:
          linked_review is not None  (explicit FK column)
          memo_context.get("kind") != "periodic_review"  (unresolved → kind=None)
        The fix (``if payload and linked_review …``) prevents this.
        """
        import lifecycle_queue as lq
        # Insert a review row that we will then DELETE so the EDD FK points
        # at a stale/non-existent review — causing _safe_resolve_memo_context
        # to return an unresolved marker (kind=None).
        rid = self._review(status="in_progress")
        self._conn.execute("DELETE FROM periodic_reviews WHERE id = ?", (rid,))
        self._conn.commit()
        # Non-fixture row: no FIX_EDD_JSON in trigger_notes; explicit FK pointing
        # at the now-deleted review.
        self._edd(
            stage="analysis",
            origin_context="onboarding",
            linked_periodic_review_id=rid,
            trigger_source="officer_decision",
            trigger_notes="Officer initiated EDD review — no fixture payload",
        )
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("edd",))
        self.assertEqual(len(result["items"]), 1)
        edd = result["items"][0]
        ctx = edd.get("memo_context") or {}
        # The fixture-normalization guard must NOT have fired.
        self.assertNotEqual(
            ctx.get("resolution_reason"), "fixture_payload_source_review_id",
            "fixture-normalization guard must not fire on non-fixture rows "
            "even when linked_periodic_review_id is set",
        )

    def test_fixture_edd_payload_surfaces_origin_and_review_link(self):
        import lifecycle_queue as lq
        review_id = self._review(status="completed")
        payload = json.dumps({
            "kind": "periodic_review",
            "source_review_id": review_id,
            "source_alert_id": None,
        })
        self._edd(
            stage="analysis",
            trigger_source="periodic_review",
            trigger_notes="FIX_SCEN03_EDD FIX_EDD_JSON:" + payload,
            origin_context=None,
            linked_periodic_review_id=None,
        )
        result = lq.build_lifecycle_queue(self._conn, include="active", types=("edd",))
        self.assertEqual(len(result["items"]), 1)
        edd = result["items"][0]
        self.assertEqual(edd["origin_context"], "periodic_review")
        self.assertEqual(edd["linked_periodic_review_id"], review_id)
        self.assertEqual((edd.get("memo_context") or {}).get("kind"), "periodic_review")


# ───────────────────────────────────────────────────────────────────
# Application-summary linkage edges
# ───────────────────────────────────────────────────────────────────
class TestApplicationSummary(_LifecycleQueueBase):
    def test_summary_emits_alert_to_review_edge(self):
        import lifecycle_queue as lq
        rid = self._review(status="in_progress")
        self._alert(status="routed_to_review",
                    linked_periodic_review_id=rid)
        s = lq.build_application_lifecycle_summary(self._conn, self._app_id)
        kinds = {e["kind"] for e in s["linkage"]["edges"]}
        # Both directions emitted (alert side and review side reverse-link)
        self.assertIn("alert_to_review", kinds)

    def test_summary_emits_review_to_edd_edge(self):
        import lifecycle_queue as lq
        rid = self._review(status="in_progress")
        eid = self._edd(stage="analysis",
                        origin_context="periodic_review",
                        linked_periodic_review_id=rid)
        # Reverse link on review side
        self._conn.execute(
            "UPDATE periodic_reviews SET linked_edd_case_id = ? WHERE id = ?",
            (eid, rid),
        )
        self._conn.commit()
        s = lq.build_application_lifecycle_summary(self._conn, self._app_id)
        kinds = {e["kind"] for e in s["linkage"]["edges"]}
        self.assertIn("review_to_edd", kinds)
        self.assertIn("edd_from_review", kinds)

    def test_summary_partitions_active_and_historical(self):
        import lifecycle_queue as lq
        self._alert(status="open")
        self._alert(status="dismissed")
        self._edd(stage="triggered")
        self._edd(stage="edd_approved")
        s = lq.build_application_lifecycle_summary(self._conn, self._app_id)
        # Active block has open alert + triggered edd (no review seeded)
        active_types = [it["type"] for it in s["active"]["items"]]
        self.assertIn("alert", active_types)
        self.assertIn("edd", active_types)
        # Historical block has the dismissed alert and approved edd
        hist_states = {it["state"] for it in s["historical"]["items"]}
        self.assertIn("dismissed", hist_states)
        self.assertIn("edd_approved", hist_states)

    def test_summary_requires_application_id(self):
        import lifecycle_queue as lq
        with self.assertRaises(ValueError):
            lq.build_application_lifecycle_summary(self._conn, "")


class TestLegacySchemaFallback(unittest.TestCase):
    """Regression: lifecycle reads must not crash on legacy alert schema."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE monitoring_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT,
                client_name TEXT,
                alert_type TEXT,
                severity TEXT,
                summary TEXT,
                status TEXT,
                source_reference TEXT,
                created_at TEXT,
                reviewed_by TEXT
            )
            """
        )
        # Minimal tables expected by lifecycle_queue read path.
        self.conn.execute(
            """
            CREATE TABLE periodic_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT,
                client_name TEXT,
                status TEXT,
                trigger_type TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE edd_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT,
                client_name TEXT,
                stage TEXT,
                trigger_source TEXT,
                triggered_at TEXT
            )
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_queue_handles_missing_linkage_columns(self):
        import lifecycle_queue as lq
        self.conn.execute(
            """
            INSERT INTO monitoring_alerts
            (application_id, client_name, alert_type, severity, summary, status, source_reference, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("app-legacy", "Legacy Co", "manual", "High", "seed", "open", "FIX_SCEN_08"),
        )
        self.conn.commit()

        result = lq.build_lifecycle_queue(self.conn, include="active")
        self.assertEqual(result["counts"]["alert"], 1)
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(result["items"][0]["state"], "open")

    def test_summary_handles_missing_linkage_columns(self):
        import lifecycle_queue as lq
        self.conn.execute(
            """
            INSERT INTO monitoring_alerts
            (application_id, client_name, alert_type, severity, summary, status, source_reference, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("app-legacy", "Legacy Co", "manual", "High", "seed", "dismissed", "FIX_SCEN_08"),
        )
        self.conn.commit()

        summary = lq.build_application_lifecycle_summary(self.conn, "app-legacy")
        self.assertEqual(summary["application_id"], "app-legacy")
        self.assertEqual(summary["historical"]["counts"]["alert"], 1)


if __name__ == "__main__":
    unittest.main()

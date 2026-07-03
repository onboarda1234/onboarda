import csv
import io
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def test_report_field_allowlist_blocks_raw_json_fields():
    from server import _report_field_list

    fields, ignored = _report_field_list("ref,company_name,prescreening_data,decision_notes,status")

    assert fields == ["ref", "company_name", "status"]
    assert ignored == ["prescreening_data", "decision_notes"]


def test_canonical_pending_status_contract_includes_new_workflow_states():
    from server import _REPORT_EDD_ROUTED_STATUSES, _REPORT_PENDING_STATUSES

    assert "draft" in _REPORT_PENDING_STATUSES
    assert "pricing_review" in _REPORT_PENDING_STATUSES
    assert "in_review" in _REPORT_PENDING_STATUSES
    assert "compliance_review" in _REPORT_PENDING_STATUSES
    assert "kyc_documents" in _REPORT_PENDING_STATUSES
    assert "submitted_to_compliance" in _REPORT_PENDING_STATUSES
    assert "rmi_sent" in _REPORT_PENDING_STATUSES
    assert _REPORT_EDD_ROUTED_STATUSES == ("edd_required",)
    assert "edd_approved" not in _REPORT_EDD_ROUTED_STATUSES


def test_report_lifecycle_buckets_cover_application_status_check_once():
    from server import _REPORT_EDD_ROUTED_STATUSES, _REPORT_PENDING_STATUSES

    db_source = Path(__file__).resolve().parents[1] / "db.py"
    source = db_source.read_text()
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS applications\s*\(.*?"
        r"status TEXT DEFAULT 'draft' CHECK\(status IN \((.*?)\)\),\s*assigned_to",
        source,
        flags=re.S,
    )
    assert match, "Could not locate canonical applications.status CHECK constraint"
    allowed_statuses = set(re.findall(r"'([^']+)'", match.group(1)))
    assert "submitted_to_compliance" in allowed_statuses

    buckets = {
        "pending": set(_REPORT_PENDING_STATUSES),
        "edd_required": set(_REPORT_EDD_ROUTED_STATUSES),
        "approved": {"approved"},
        "rejected": {"rejected"},
        "withdrawn": {"withdrawn"},
    }
    duplicate_memberships = {}
    unmapped = []
    for status in sorted(allowed_statuses):
        memberships = [name for name, values in buckets.items() if status in values]
        if len(memberships) == 0:
            unmapped.append(status)
        elif len(memberships) > 1:
            duplicate_memberships[status] = memberships

    assert unmapped == []
    assert duplicate_memberships == {}


def test_canonical_export_field_contract_includes_risk_score():
    from server import _REPORT_EXPORT_FIELDS, _REPORT_EXPORT_FILENAME_PREFIX

    assert _REPORT_EXPORT_FIELDS == (
        "ref", "company_name", "status", "risk_level", "risk_score",
        "sector", "country", "entity_type", "created_at", "assigned_to",
        "director_count", "ubo_count", "document_count",
    )
    assert _REPORT_EXPORT_FILENAME_PREFIX == "regmind_applications_report"


def test_periodic_review_stats_uses_postgres_safe_date_expression():
    from server import _periodic_review_canonical_stats

    class _Result:
        def __init__(self, one=None, many=None):
            self._one = one
            self._many = many or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._many

    class _PostgresLikeDb:
        is_postgres = True

        def __init__(self):
            self.statements = []

        def execute(self, sql, params=()):
            self.statements.append(sql)
            if "GROUP BY" in sql:
                return _Result(many=[{"status": "pending", "count": 1}])
            return _Result(one={
                "total": 1,
                "pending": 1,
                "active": 1,
                "completed": 0,
                "due": 1,
                "overdue": 0,
            })

    db = _PostgresLikeDb()

    stats = _periodic_review_canonical_stats(db, today="2026-05-19")

    aggregate_sql = db.statements[0]
    assert "pr.next_review_date::text" in aggregate_sql
    assert "pr.due_date::text" in aggregate_sql
    assert "::date" in aggregate_sql
    assert "COALESCE(pr.next_review_date, pr.due_date)" not in aggregate_sql
    assert "NULLIF(pr.next_review_date, '')" not in aggregate_sql
    assert "NULLIF(pr.due_date, '')" not in aggregate_sql
    assert stats["due"] == 1


def test_pdf_download_handler_records_pdf_hash_metadata():
    import inspect
    from server import MemoPDFDownloadHandler

    src = inspect.getsource(MemoPDFDownloadHandler.get)
    assert "pdf_sha256" in src
    assert "X-PDF-SHA256" in src
    assert "memo_id" in src
    assert "memo_version" in src


class _Phase4ReportingHTTPBase(AsyncHTTPTestCase):
    def _patch_attr(self, module, name, value):
        if hasattr(module, name):
            self._module_restore.append((module, name, getattr(module, name)))
            setattr(module, name, value)

    def setUp(self):
        self.db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_phase4_reporting_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        self._env_restore = {
            "DB_PATH": os.environ.get("DB_PATH"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        self._module_restore = []
        os.environ["DB_PATH"] = self.db_path
        os.environ["DATABASE_URL"] = ""

        import config as config_module
        import db as db_module

        self._patch_attr(config_module, "DATABASE_URL", "")
        self._patch_attr(config_module, "DB_PATH", self.db_path)
        self._patch_attr(db_module, "DATABASE_URL", "")
        self._patch_attr(db_module, "DB_PATH", self.db_path)
        self._patch_attr(db_module, "USE_POSTGRESQL", False)

        server_module = sys.modules.get("server")
        if server_module is not None:
            self._patch_attr(server_module, "DATABASE_URL", "")
            self._patch_attr(server_module, "DB_PATH", self.db_path)
            self._patch_attr(server_module, "_CFG_DB_PATH", self.db_path)
            self._patch_attr(server_module, "USE_POSTGRES", False)
            self._patch_attr(server_module, "USE_POSTGRESQL", False)
            self._patch_attr(server_module, "db_get_db", db_module.get_db)
            self._patch_attr(server_module, "db_init_db", db_module.init_db)

        db_module.init_db()
        db = db_module.get_db()
        seed_initial_data = db_module.seed_initial_data
        seed_initial_data(db)
        self._seed_apps(db)
        db.commit()
        db.close()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for module, name, value in reversed(getattr(self, "_module_restore", [])):
            setattr(module, name, value)
        for key, value in getattr(self, "_env_restore", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def get_app(self):
        from server import make_app
        return make_app()

    def _admin_headers(self):
        from server import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        return {"Authorization": f"Bearer {token}"}

    def _seed_apps(self, db):
        suffix = uuid.uuid4().hex[:8]
        self.report_ref_1 = f"P4-RPT-{suffix}-001"
        self.report_ref_2 = f"P4-RPT-{suffix}-002"
        self.report_ref_3 = f"P4-RPT-{suffix}-003"
        rows = [
            (f"phase4-report-{suffix}-1", self.report_ref_1, "Phase Four One Ltd", "rmi_sent", "MEDIUM", 44, "Mauritius", "SME"),
            (f"phase4-report-{suffix}-2", self.report_ref_2, "Phase Four Two Ltd", "compliance_review", "HIGH", 72, "Mauritius", "Company"),
            (f"phase4-report-{suffix}-3", self.report_ref_3, "=Phase Four Three Ltd", "approved", "LOW", 20, "United Kingdom", "SME"),
        ]
        for row in rows:
            db.execute(
                """
                INSERT INTO applications
                (id, ref, company_name, status, risk_level, risk_score, country, entity_type, prescreening_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, json.dumps({"sensitive": "raw-report-json"})),
            )

    def _seed_periodic_review_reconciliation_rows(self):
        from db import get_db

        today = datetime.utcnow().date()
        country = f"PR14-Reconcile-{uuid.uuid4().hex[:8]}"
        rows = [
            ("pending", today - timedelta(days=2), today + timedelta(days=10)),
            ("in_progress", today, None),
            ("awaiting_information", today + timedelta(days=3), None),
            ("pending_senior_review", None, today - timedelta(days=1)),
            ("completed", today - timedelta(days=30), None),
        ]
        app_ids = []
        db = get_db()
        for index, (review_status, next_review_date, due_date) in enumerate(rows, start=1):
            app_id = f"pr14-report-{uuid.uuid4().hex[:12]}"
            app_ids.append(app_id)
            db.execute(
                """
                INSERT INTO applications
                (id, ref, company_name, status, risk_level, risk_score, country, entity_type, is_fixture)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    f"PR14-RPT-{uuid.uuid4().hex[:8]}",
                    f"PR14 Reconcile {index} Ltd",
                    "approved",
                    "MEDIUM",
                    42,
                    country,
                    "SME",
                    0,
                ),
            )
            db.execute(
                """
                INSERT INTO periodic_reviews
                (application_id, client_name, status, next_review_date, due_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    f"PR14 Reconcile {index} Ltd",
                    review_status,
                    next_review_date.isoformat() if next_review_date else None,
                    due_date.isoformat() if due_date else None,
                    (today - timedelta(days=10 + index)).isoformat(),
                ),
            )
        db.commit()
        db.close()
        return country, set(app_ids)


class TestPhase4ReportingHTTP(_Phase4ReportingHTTPBase):
    def test_report_generate_csv_uses_allowlist_and_audits_export(self):
        resp = self.fetch(
            "/api/reports/generate?format=csv&fields=ref,company_name,status,prescreening_data",
            headers=self._admin_headers(),
        )

        assert resp.code == 200
        assert "text/csv" in resp.headers.get("Content-Type", "")
        assert resp.headers.get("X-Report-Canonical-View") == "applications_report_v1"
        assert resp.headers.get("X-Report-Show-Fixtures") == "false"
        assert resp.headers.get("X-Report-Filename", "").startswith("regmind_applications_report_")
        assert "regmind_applications_report_" in resp.headers.get("Content-Disposition", "")
        assert resp.body.startswith("\ufeff".encode("utf-8"))
        reader = csv.reader(io.StringIO(resp.body.decode("utf-8-sig")))
        rows = list(reader)
        assert resp.headers.get("X-Report-Record-Count") == str(len(rows) - 1)
        assert resp.headers.get("X-Report-Field-List") == "ref,company_name,status"
        assert rows[0] == ["ref", "company_name", "status"]
        assert "prescreening_data" not in rows[0]
        assert any(row[0] == self.report_ref_1 for row in rows[1:])
        injected = next(row for row in rows[1:] if row[0] == self.report_ref_3)
        assert injected[1] == "'=Phase Four Three Ltd"

        from db import get_db
        db = get_db()
        audit = db.execute(
            "SELECT detail FROM audit_log WHERE action='Report' AND target='Generate' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        assert audit is not None
        assert "format=csv" in audit["detail"]

    def test_report_generate_json_exposes_canonical_export_metadata(self):
        resp = self.fetch(
            "/api/reports/generate?fields=ref,company_name,status",
            headers=self._admin_headers(),
        )

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        assert body["total"] == len(body["data"])
        assert body["report"]["record_count"] == body["total"]
        assert body["report"]["canonical_view"] == "applications_report_v1"
        assert body["report"]["show_fixtures"] is False
        assert "rmi_sent" in body["report"]["pending_statuses"]
        assert body["report"]["edd_routed_statuses"] == ["edd_required"]
        assert body["report"]["field_list"] == body["fields"]
        assert body["report"]["ignored_fields"] == []
        assert body["report"]["filename_prefix"] == "regmind_applications_report"

    def test_report_generate_rejects_unsupported_format(self):
        resp = self.fetch(
            "/api/reports/generate?format=xlsx",
            headers=self._admin_headers(),
        )

        assert resp.code == 400
        body = json.loads(resp.body.decode())
        assert "Unsupported report format" in body["error"]

    def test_report_generate_uses_application_id_not_joined_user_id(self):
        from db import get_db

        db = get_db()
        db.execute(
            "UPDATE applications SET assigned_to=? WHERE ref=?",
            ("admin001", self.report_ref_1),
        )
        db.commit()
        app_id = db.execute(
            "SELECT id FROM applications WHERE ref=?",
            (self.report_ref_1,),
        ).fetchone()["id"]
        db.close()

        resp = self.fetch(
            f"/api/reports/generate?fields=id,ref,assigned_name&show_fixtures=true",
            headers=self._admin_headers(),
        )

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        row = next(item for item in body["data"] if item["ref"] == self.report_ref_1)
        assert row["id"] == app_id
        assert row["id"] != "admin001"
        assert row["assigned_name"]

    def test_analytics_uses_canonical_pending_statuses_and_reports_scope(self):
        resp = self.fetch("/api/reports/analytics?jurisdiction=Mauritius", headers=self._admin_headers())

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        assert body["summary"]["pending"] >= 2
        assert body["summary"]["total"] >= 2
        assert body["report"]["canonical_view"] == "applications_report_v1"
        assert body["report"]["filters"] == {"jurisdiction": "Mauritius"}
        assert "rmi_sent" in body["report"]["pending_statuses"]
        assert body["report"]["edd_routed_statuses"] == ["edd_required"]

    def test_analytics_summary_reconciles_visible_lifecycle_statuses(self):
        from db import get_db

        country = f"Reconcile-{uuid.uuid4().hex[:8]}"
        rows = [
            ("draft", "draft"),
            ("pricing", "pricing_review"),
            ("review", "in_review"),
            ("rmi", "rmi_sent"),
            ("submitted_to_compliance", "submitted_to_compliance"),
            ("edd", "edd_required"),
            ("approved", "approved"),
            ("rejected", "rejected"),
            ("withdrawn", "withdrawn"),
        ]
        db = get_db()
        for suffix, status in rows:
            db.execute(
                """
                INSERT INTO applications
                (id, ref, company_name, status, risk_level, risk_score, country, entity_type, is_fixture)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"phase4-report-reconcile-{suffix}-{uuid.uuid4().hex[:8]}",
                    f"P4-RPT-RECON-{suffix}-{uuid.uuid4().hex[:8]}",
                    f"Phase Four Reconcile {suffix.title()} Ltd",
                    status,
                    "LOW",
                    20,
                    country,
                    "SME",
                    0,
                ),
            )
        db.commit()
        db.close()

        resp = self.fetch(
            f"/api/reports/analytics?jurisdiction={country}",
            headers=self._admin_headers(),
        )

        assert resp.code == 200
        body = json.loads(resp.body.decode())
        summary = body["summary"]
        classified_total = (
            summary["pending"]
            + summary["edd_required"]
            + summary["approved"]
            + summary["rejected"]
            + summary["withdrawn"]
        )
        assert summary["total"] == len(rows), body
        assert summary["pending"] == 5
        assert "submitted_to_compliance" in body["report"]["pending_statuses"]
        assert classified_total == summary["total"]

    def test_dashboard_in_progress_count_matches_report_pending_bucket(self):
        analytics = self.fetch("/api/reports/analytics", headers=self._admin_headers())
        dashboard = self.fetch("/api/dashboard", headers=self._admin_headers())

        assert analytics.code == 200
        assert dashboard.code == 200
        analytics_body = json.loads(analytics.body.decode())
        dashboard_body = json.loads(dashboard.body.decode())
        expected_pending = analytics_body["summary"]["pending"]
        assert dashboard_body["early_stage_applications"] == expected_pending
        assert dashboard_body["in_progress_applications"] == expected_pending
        assert dashboard_body["pending_statuses"] == analytics_body["report"]["pending_statuses"]
        assert dashboard_body["edd_routed_statuses"] == analytics_body["report"]["edd_routed_statuses"]
        assert dashboard_body["canonical_view"] == "dashboard_metrics_v2"

    def test_periodic_review_report_counts_reconcile_with_lifecycle_queue(self):
        country, app_ids = self._seed_periodic_review_reconciliation_rows()

        analytics = self.fetch(
            f"/api/reports/analytics?jurisdiction={country}",
            headers=self._admin_headers(),
        )
        active_queue = self.fetch(
            "/api/lifecycle/queue?type=reviews&include=active",
            headers=self._admin_headers(),
        )
        historical_queue = self.fetch(
            "/api/lifecycle/queue?type=reviews&include=historical",
            headers=self._admin_headers(),
        )

        assert analytics.code == 200
        assert active_queue.code == 200
        assert historical_queue.code == 200

        stats = json.loads(analytics.body.decode())["periodic_review_stats"]
        active_items = [
            item for item in json.loads(active_queue.body.decode())["items"]
            if item["application_id"] in app_ids
        ]
        historical_items = [
            item for item in json.loads(historical_queue.body.decode())["items"]
            if item["application_id"] in app_ids
        ]

        assert stats["canonical_source"] == "periodic_reviews"
        assert stats["counting_rule"] == "count_distinct_periodic_reviews_id"
        assert stats["date_basis"] == "next_review_date_fallback_due_date"
        assert stats["reconciles"] is True
        assert stats["total"] == 5
        assert stats["active"] == len(active_items) == 4
        assert stats["completed"] == len(historical_items) == 1
        assert stats["pending"] == 1
        assert stats["due"] == 3
        assert stats["overdue"] == 2
        assert sum(stats["by_status"].values()) == stats["total"]

    def test_monitoring_dashboard_periodic_review_due_uses_same_canonical_count(self):
        baseline = self.fetch("/api/monitoring/dashboard", headers=self._admin_headers())
        assert baseline.code == 200
        baseline_stats = json.loads(baseline.body.decode())

        country, _ = self._seed_periodic_review_reconciliation_rows()
        analytics = self.fetch(
            f"/api/reports/analytics?jurisdiction={country}",
            headers=self._admin_headers(),
        )
        dashboard = self.fetch("/api/monitoring/dashboard", headers=self._admin_headers())

        assert analytics.code == 200
        assert dashboard.code == 200
        report_stats = json.loads(analytics.body.decode())["periodic_review_stats"]
        dashboard_stats = json.loads(dashboard.body.decode())
        assert dashboard_stats["periodic_review_due"] == (
            baseline_stats["periodic_review_due"] + report_stats["due"]
        )
        assert dashboard_stats["periodic_review_overdue"] == (
            baseline_stats.get("periodic_review_overdue", 0) + report_stats["overdue"]
        )

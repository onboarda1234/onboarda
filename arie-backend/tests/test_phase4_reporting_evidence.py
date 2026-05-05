import csv
import io
import json
import os
import sys
import tempfile
import uuid

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
    from server import _REPORT_PENDING_STATUSES

    assert "draft" in _REPORT_PENDING_STATUSES
    assert "pricing_review" in _REPORT_PENDING_STATUSES
    assert "in_review" in _REPORT_PENDING_STATUSES
    assert "compliance_review" in _REPORT_PENDING_STATUSES
    assert "kyc_documents" in _REPORT_PENDING_STATUSES
    assert "rmi_sent" in _REPORT_PENDING_STATUSES


def test_pdf_download_handler_records_pdf_hash_metadata():
    import inspect
    from server import MemoPDFDownloadHandler

    src = inspect.getsource(MemoPDFDownloadHandler.get)
    assert "pdf_sha256" in src
    assert "X-PDF-SHA256" in src
    assert "memo_id" in src
    assert "memo_version" in src


class _Phase4ReportingHTTPBase(AsyncHTTPTestCase):
    def setUp(self):
        self.db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_phase4_reporting_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        os.environ["DB_PATH"] = self.db_path
        from db import init_db, seed_initial_data, get_db
        init_db()
        db = get_db()
        seed_initial_data(db)
        self._seed_apps(db)
        db.commit()
        db.close()
        super().setUp()

    def tearDown(self):
        super().tearDown()
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


class TestPhase4ReportingHTTP(_Phase4ReportingHTTPBase):
    def test_report_generate_csv_uses_allowlist_and_audits_export(self):
        resp = self.fetch(
            "/api/reports/generate?format=csv&fields=ref,company_name,status,prescreening_data",
            headers=self._admin_headers(),
        )

        assert resp.code == 200
        assert "text/csv" in resp.headers.get("Content-Type", "")
        assert resp.body.startswith("\ufeff".encode("utf-8"))
        reader = csv.reader(io.StringIO(resp.body.decode("utf-8-sig")))
        rows = list(reader)
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

    def test_analytics_summary_reconciles_visible_lifecycle_statuses(self):
        from db import get_db

        country = f"Reconcile-{uuid.uuid4().hex[:8]}"
        rows = [
            ("draft", "draft"),
            ("pricing", "pricing_review"),
            ("review", "in_review"),
            ("rmi", "rmi_sent"),
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
        assert summary["total"] == len(rows)
        assert summary["pending"] == 4
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
        assert dashboard_body["canonical_view"] == "applications_report_v1"

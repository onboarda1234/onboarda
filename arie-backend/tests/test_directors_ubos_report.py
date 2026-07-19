import csv
import inspect
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


class DirectorsUBOsReportHTTPBase(AsyncHTTPTestCase):
    def setUp(self):
        self.db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_directors_ubos_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        os.environ["DB_PATH"] = self.db_path
        for module_name in ("config", "db", "server"):
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, "DB_PATH"):
                setattr(module, "DB_PATH", self.db_path)
            if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
                setattr(module, "_CFG_DB_PATH", self.db_path)

        from db import get_db, init_db, seed_initial_data

        init_db()
        db = get_db()
        seed_initial_data(db)
        self._seed_report_data(db)
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

    def _headers(self, role="admin", user_id=None):
        from server import create_token

        user_id = user_id or f"{role}_du_report"
        token_type = "client" if role == "client" else "officer"
        token = create_token(user_id, role, f"{role.upper()} Report User", token_type)
        return {"Authorization": f"Bearer {token}"}

    def _insert_doc(self, db, doc_id, app_id, person_id, doc_type, status, expiry=None):
        db.execute(
            """
            INSERT INTO documents
            (id, application_id, person_id, doc_type, doc_name, file_path, verification_status, expiry_date, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, app_id, person_id, doc_type, f"{doc_type}.pdf", f"/tmp/{doc_id}.pdf", status, expiry, 1),
        )

    def _seed_report_data(self, db):
        # Fixture-safe suffix: a raw uuid hex suffix occasionally contains
        # "e2e" (CI incident — seeded ref DU-9e131e2e-001 was silently
        # classified as an E2E fixture and filtered out of the report). The
        # shared helper carries the full incident history.
        from fixture_safe_refs import fixture_safe_suffix

        suffix = fixture_safe_suffix(8)
        self.ref_combined = f"DU-{suffix}-001"
        self.ref_director_missing = f"DU-{suffix}-002"
        self.ref_ubo_verified = f"DU-{suffix}-003"
        self.ref_ubo_missing = f"DU-{suffix}-004"

        db.execute(
            """
            INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status)
            VALUES
              ('admin_du_report', 'admin-du@example.test', 'x', 'Admin DU', 'admin', 'active'),
              ('sco_du_report', 'sco-du@example.test', 'x', 'SCO DU', 'sco', 'active'),
              ('co_du_report', 'co-du@example.test', 'x', 'CO DU', 'co', 'active'),
              ('analyst_du_report', 'analyst-du@example.test', 'x', 'Analyst DU', 'analyst', 'active')
            """
        )
        db.execute(
            """
            INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status)
            VALUES ('client_du_report', 'client-du@example.test', 'x', 'Client DU', 'active')
            """
        )

        prescreening_hit = {
            "screening_report": {
                "director_screenings": [
                    {
                        "person_name": "Alex Shared",
                        "screening": {
                            "api_status": "success",
                            "matched": True,
                            "screened_at": "2026-06-01T10:00:00Z",
                            "results": [
                                {
                                    "match_categories": ["sanctions", "pep", "adverse_media"],
                                    "indicator_type": "sanctions pep adverse_media",
                                }
                            ],
                        },
                    }
                ],
                "screened_at": "2026-06-01T10:00:00Z",
            }
        }
        prescreening_clear = {
            "screening_report": {
                "ubo_screenings": [
                    {
                        "person_name": "Bianca Owner",
                        "screening": {
                            "api_status": "success",
                            "matched": False,
                            "screened_at": "2026-06-02T11:00:00Z",
                            "results": [],
                        },
                    }
                ],
                "screened_at": "2026-06-02T11:00:00Z",
            }
        }
        apps = [
            ("app_du_combined", self.ref_combined, "Combined Holdings Ltd", "compliance_review", "HIGH", 76, "co_du_report", prescreening_hit, "2026-05-10 09:00:00", "2026-06-03 12:00:00"),
            ("app_du_director_missing", self.ref_director_missing, "Missing Director Ltd", "submitted", "MEDIUM", 45, "co_du_report", {}, "2026-05-11 09:00:00", "2026-06-04 12:00:00"),
            ("app_du_ubo_verified", self.ref_ubo_verified, "Verified Owner Ltd", "approved", "LOW", 18, "sco_du_report", prescreening_clear, "2026-05-12 09:00:00", "2026-06-05 12:00:00"),
            ("app_du_ubo_missing", self.ref_ubo_missing, "Missing Ownership Ltd", "edd_required", "VERY_HIGH", 88, "co_du_report", {}, "2026-05-13 09:00:00", "2026-06-06 12:00:00"),
        ]
        for app_id, ref, company, status, risk_level, risk_score, assigned_to, prescreening, created, updated in apps:
            db.execute(
                """
                INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level,
                 risk_score, assigned_to, prescreening_data, is_fixture, created_at, updated_at)
                VALUES (?, ?, 'client_du_report', ?, 'Mauritius', 'Finance', 'Company', ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    app_id,
                    ref,
                    company,
                    status,
                    risk_level,
                    risk_score,
                    assigned_to,
                    json.dumps(prescreening),
                    created,
                    updated,
                ),
            )

        pep_yes = json.dumps({"client_declared_pep": True})
        pep_no = json.dumps({"client_declared_pep": False})
        db.execute(
            """
            INSERT INTO directors
            (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration, date_of_birth, created_at)
            VALUES
              ('dir_combined', 'app_du_combined', 'person-shared', 'Alex', 'Shared', 'Alex Shared', 'South Africa', 'Yes', ?, '1970-01-01', '2026-05-10 09:10:00'),
              ('dir_missing', 'app_du_director_missing', 'person-missing', 'Mira', 'Missing', 'Mira Missing', '', 'No', '{}', '', '2026-05-11 09:10:00')
            """,
            (pep_yes,),
        )
        db.execute(
            """
            INSERT INTO ubos
            (id, application_id, person_key, first_name, last_name, full_name, nationality, ownership_pct, is_pep, pep_declaration, date_of_birth, created_at)
            VALUES
              ('ubo_combined', 'app_du_combined', 'person-shared', 'Alex', 'Shared', 'Alex Shared', 'South Africa', 80, 'Yes', ?, '1970-01-01', '2026-05-10 09:11:00'),
              ('ubo_verified', 'app_du_ubo_verified', 'person-bianca', 'Bianca', 'Owner', 'Bianca Owner', 'United Arab Emirates', 30, 'No', ?, '1985-02-02', '2026-05-12 09:10:00'),
              ('ubo_missing_owner', 'app_du_ubo_missing', 'person-no-owner', 'Noah', 'Owner', 'Noah Owner', 'France', NULL, 'No', '{}', '1990-03-03', '2026-05-13 09:10:00')
            """,
            (pep_yes, pep_no),
        )

        self._insert_doc(db, "doc_combined_passport", "app_du_combined", "person-shared", "passport", "verified")
        self._insert_doc(db, "doc_combined_poa", "app_du_combined", "person-shared", "poa", "failed", "2025-01-01")
        self._insert_doc(db, "doc_bianca_passport", "app_du_ubo_verified", "person-bianca", "passport", "verified")
        self._insert_doc(db, "doc_bianca_poa", "app_du_ubo_verified", "person-bianca", "poa", "verified")
        self._insert_doc(db, "doc_missing_owner_passport", "app_du_ubo_missing", "person-no-owner", "passport", "pending")

        db.execute(
            """
            INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition, disposition_code, reviewer_id, reviewer_name, updated_at)
            VALUES
              ('app_du_combined', 'director', 'Alex Shared', 'escalated', 'sanctions_hit', 'sco_du_report', 'SCO DU', '2026-06-01 10:30:00'),
              ('app_du_ubo_verified', 'ubo', 'Bianca Owner', 'cleared', 'false_positive', 'sco_du_report', 'SCO DU', '2026-06-02 11:30:00')
            """
        )
        db.execute(
            """
            INSERT INTO periodic_reviews (application_id, client_name, status, created_at)
            VALUES ('app_du_combined', 'Combined Holdings Ltd', 'pending', '2026-06-03 12:30:00')
            """
        )

    def _json(self, path, role="admin"):
        resp = self.fetch(path, headers=self._headers(role))
        assert resp.code == 200, resp.body.decode("utf-8", errors="replace")
        # Diagnostic tripwire for the historical order-dependence flake ("seeded
        # rows came back empty"): if any earlier test file leaked state that
        # re-points the db module mid-test, fail HERE with the real cause
        # instead of a mystery empty-rows assertion downstream.
        import db as _db_module
        assert _db_module.DB_PATH == self.db_path, (
            "db.DB_PATH drifted mid-test (leaked cross-test state): "
            f"{_db_module.DB_PATH!r} != {self.db_path!r}"
        )
        assert not _db_module.USE_POSTGRESQL, (
            "db.USE_POSTGRESQL flipped True mid-test (leaked cross-test state)"
        )
        return json.loads(resp.body.decode("utf-8"))

    def _rows(self, path, role="admin"):
        return self._json(path, role=role)["rows"]


class TestDirectorsUBOsReport(DirectorsUBOsReportHTTPBase):
    def test_route_exists_and_requires_authorized_backoffice_role(self):
        from server import DirectorsUBOsReportHandler, make_app

        assert DirectorsUBOsReportHandler is not None
        app = make_app()
        assert any(
            hasattr(rule, "matcher")
            and hasattr(rule.matcher, "regex")
            and "reports/directors-ubos" in rule.matcher.regex.pattern
            for rule in app.wildcard_router.rules
        )
        assert self.fetch("/api/reports/directors-ubos").code == 401
        assert self.fetch("/api/reports/directors-ubos", headers=self._headers("analyst")).code == 403
        assert self.fetch("/api/reports/directors-ubos", headers=self._headers("client", "client_du_report")).code == 403
        assert self.fetch("/api/reports/directors-ubos", headers=self._headers("co")).code == 200

    def test_combined_report_deduplicates_director_and_ubo_person(self):
        payload = self._json("/api/reports/directors-ubos?sort=application_ref&direction=asc")
        roles_by_ref = {row["application_ref"]: row["role"] for row in payload["rows"]}

        assert roles_by_ref[self.ref_combined] == "Director & UBO"
        assert payload["summary"]["total_directors"] == 2
        assert payload["summary"]["total_ubos"] == 3
        assert payload["summary"]["total_unique_persons"] == 4
        assert payload["summary"]["pep_count"] >= 1
        assert payload["summary"]["sanctions_hit_count"] >= 1
        assert payload["summary"]["ownership_above_75_count"] == 1
        combined = next(row for row in payload["rows"] if row["application_ref"] == self.ref_combined)
        assert combined["ownership_above_75"] is True
        assert combined["failed_document_verification"] is True
        assert combined["expired_documents"] is True
        assert "application" in combined["links"]
        assert "screening_review" in combined["links"]
        assert "documents" in combined["links"]
        assert "periodic_review" in combined["links"]

    def test_directors_only_and_ubos_only_views(self):
        director_rows = self._rows("/api/reports/directors-ubos?view=directors")
        ubo_rows = self._rows("/api/reports/directors-ubos?view=ubos")

        assert {row["application_ref"] for row in director_rows} == {self.ref_combined, self.ref_director_missing}
        assert {row["application_ref"] for row in ubo_rows} == {self.ref_combined, self.ref_ubo_verified, self.ref_ubo_missing}

    def test_role_nationality_pep_sanctions_adverse_and_risk_filters(self):
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?nationality=South")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?pep_status=declared_yes")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?sanctions_status=review")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?sanctions_status=match")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?adverse_media_status=review")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?adverse_media_status=match")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?application_risk_level=HIGH")] == [self.ref_combined]

    def test_ownership_and_missing_data_filters(self):
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?ownership_min=50")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?ownership_min=25&ownership_max=50")] == [self.ref_ubo_verified]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?missing_dob=true")] == [self.ref_director_missing]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?missing_nationality=true")] == [self.ref_director_missing]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?missing_ownership=true")] == [self.ref_ubo_missing]
        assert {row["application_ref"] for row in self._rows("/api/reports/directors-ubos?missing_documents=true")} == {self.ref_director_missing, self.ref_ubo_missing}
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?failed_document_verification=true")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?pending_document_verification=true")] == [self.ref_ubo_missing]

    def test_screening_status_filters(self):
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?screening_status=unresolved")] == [self.ref_combined]
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?screening_review_status=cleared")] == [self.ref_ubo_verified]

    def test_application_status_assigned_pagination_sort_and_empty_state(self):
        assert [row["application_ref"] for row in self._rows("/api/reports/directors-ubos?application_status=approved")] == [self.ref_ubo_verified]
        assert {row["application_ref"] for row in self._rows("/api/reports/directors-ubos?assigned_to=co_du_report")} == {self.ref_combined, self.ref_director_missing, self.ref_ubo_missing}

        payload = self._json("/api/reports/directors-ubos?limit=2&offset=1&sort=person_name&direction=asc")
        assert payload["pagination"]["limit"] == 2
        assert payload["pagination"]["offset"] == 1
        assert payload["pagination"]["total"] == 4
        assert len(payload["rows"]) == 2
        assert payload["rows"][0]["person_name"] <= payload["rows"][1]["person_name"]

        empty = self._json("/api/reports/directors-ubos?nationality=Neverland")
        assert empty["pagination"]["total"] == 0
        assert empty["rows"] == []

    def test_csv_export_respects_filters_and_audits_export(self):
        resp = self.fetch(
            "/api/reports/directors-ubos?format=csv&nationality=South",
            headers=self._headers("sco", "sco_du_report"),
        )
        assert resp.code == 200
        assert resp.headers["Content-Type"].startswith("text/csv")
        assert resp.headers["X-Report-Canonical-View"] == "directors_ubos_report_v1"
        assert resp.headers["X-Report-Record-Count"] == "1"
        reader = csv.DictReader(io.StringIO(resp.body.decode("utf-8-sig")))
        assert reader.fieldnames
        assert len(reader.fieldnames) == len(set(reader.fieldnames))
        rows = list(reader)
        assert "created_at" in rows[0]
        assert rows[0]["role"] == "Director & UBO"
        assert rows[0]["person_name"] == "Alex Shared"
        assert rows[0]["nationality"] == "South Africa"
        assert rows[0]["ownership_pct"] in ("80", "80.0")
        assert rows[0]["application_risk_level"] == "HIGH"

        from db import get_db

        db = get_db()
        audit = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM audit_log
            WHERE action='Report' AND target='Directors & UBOs'
              AND detail LIKE '%format=csv%'
            """
        ).fetchone()
        db.close()
        assert audit["count"] >= 1

    def test_report_handler_has_constant_query_count_not_n_plus_one(self):
        from server import DirectorsUBOsReportHandler, _directors_ubos_enrich_record

        get_src = inspect.getsource(DirectorsUBOsReportHandler.get)
        enrich_src = inspect.getsource(_directors_ubos_enrich_record)
        assert get_src.count("db.execute(") <= 6
        assert "get_db(" not in enrich_src

    def test_report_cte_escapes_literal_like_patterns_for_psycopg(self):
        from server import _directors_ubos_report_cte

        cte = _directors_ubos_report_cte()
        literal_patterns = [
            "confirmed_pep",
            "pending_review",
            "false_positive",
            "not_pep",
            "declared_yes",
        ]
        for pattern in literal_patterns:
            assert f"LIKE '%{pattern}%'" not in cte
            assert f"LIKE '%%{pattern}%%'" in cte
